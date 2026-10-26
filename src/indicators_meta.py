#!/usr/bin/env python3
"""Indicator provenance metadata emitter for the quarterly heat index.

Emits data/indicators_meta.json and docs/data/indicators_meta.json.
SOURCES table is keyed by exact panel column name, with:
title, short (from make_site.SHORT_NAMES), component (from run_pipeline.SPEC),
status, source, source_url, series_id, definition, frequency, note.

Build computes per-indicator:
  first_observation, last_observation, last_value (raw panel value, not z-score),
  last_value_status ('observed' or 'nowcast'), nowcast_method,
  last_observed_quarter, last_observed_value, n_observations, n_observed,
  panel_last_quarter, generated_from, generated_by.

Usage:
    python3 src/indicators_meta.py [--data data] [--out data/indicators_meta.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_site import SHORT_NAMES, dumps_strict  # noqa: E402
from run_pipeline import SPEC  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

# Hand-authored source annotations transcribed from provenance-sources-vetted.json
_SOURCES_RAW = {
    "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC) / Chicago Fed, Y-9C bulk data",
        "source_url": None,
        "series_id": [
                "BHCK2170"
        ],
        "definition": "100 x (sum of total assets BHCK2170 for the 5 largest BHCs) / (sum across all Y-9C filers), percent.",
        "frequency": "Quarterly",
        "note": "Chicago Fed bulk files mix filer universes across quarters (Q1/Q3 smaller filer counts than Q2/Q4); share dynamics track despite level distortion in some quarters."
},
    "Broker Dealer Leverage (Dealer financing)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL662051003Q",
        "series_id": [
                "BOGZ1FL662051003Q",
                "BOGZ1FL662151003Q",
                "GDP"
        ],
        "definition": "100 x (security broker/dealer repo assets + repo liabilities) / GDP, percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL794141005Q",
        "series_id": [
                "BOGZ1FL794141005Q",
                "BOGZ1FL793169175Q",
                "GDP"
        ],
        "definition": "100 x (financial-sector short-term loans liabilities + open market paper liabilities) / GDP, percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Illiquidity Conventration Indicator": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL793063005Q",
        "series_id": [
                "BOGZ1FL793063005Q",
                "BOGZ1FL794035005Q",
                "BOGZ1FL794090005Q"
        ],
        "definition": "(Corporate and foreign bond assets + total loan assets) / total financial assets of domestic financial sectors, share (quarter-end levels).",
        "frequency": "Quarterly",
        "note": "Public proxy for the paper's illiquidity-concentration concept; the authors' published series is unfetchable."
},
    "Maturity Mismatch (Loans as a percent of deposits)": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC) / Chicago Fed, Y-9C bulk data",
        "source_url": None,
        "series_id": [
                "BHCK2122",
                "BHDM6631",
                "BHDM6636",
                "BHFN6631",
                "BHFN6636"
        ],
        "definition": "100 x (total loans and leases) / (total deposits, domestic + foreign offices), aggregated across all Y-9C filers, percent.",
        "frequency": "Quarterly",
        "note": ""
},
    "Weighted Average Maturity Gap at Commerical Banks": {
        "status": "proxy",
        "source": "Fitted proxy from public macro inputs (no direct public measurement)",
        "source_url": None,
        "series_id": [],
        "definition": "OLS regression of the authors' accessible maturity-gap series (1997Q2-2010Q4) on the 10-year Treasury yield, VIX, and the loans-to-deposits ratio; fitted gap in months.",
        "frequency": "Quarterly",
        "note": "Fitted, not directly measured: gap = -1.87 - 3.40*y10 - 0.144*vix + 0.423*loan_dep (in-sample R2 0.80, RMSE 1.8 months); out-of-sample quarters rely on the 1997-2010 relationship holding. 2026Q1-Q2 values are regression-extensions (see nowcast provenance)."
},
    "Broker Dealer Leverage (Broker dealer leverage ratio)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL664090005Q",
        "series_id": [
                "BOGZ1FL664090005Q",
                "BOGZ1FL664190005Q"
        ],
        "definition": "100 x (total financial assets - total liabilities) / total financial assets for security brokers and dealers, i.e. the equity/assets capital ratio, percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": "Built as equity/assets (not assets/equity) because the accessible series behaves as a capital ratio; pipeline sign is -1."
},
    "Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL794141005Q",
        "series_id": [
                "BOGZ1FL794141005Q",
                "BOGZ1FL793169175Q",
                "BOGZ1FL792050005Q",
                "BOGZ1FL793069175Q",
                "GDP"
        ],
        "definition": "100 x (short-term loans liabilities + open market paper liabilities - fed funds & repo assets - open market paper assets of domestic financial sectors) / GDP, percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Ratio of U.S. Non Agency Securitization Issuance to GDP": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FA674190005Q",
        "series_id": [
                "BOGZ1FA674190005Q",
                "GDP"
        ],
        "definition": "100 x (ABS-issuer sector net transactions, $M SAAR / 1000) / GDP ($B SAAR), percent; public net-issuance measure of the non-agency securitization universe.",
        "frequency": "Quarterly",
        "note": "Net transactions, not SIFMA gross issuance (SIFMA public quarterly history is truncated)."
},
    "Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL792150005Q",
        "series_id": [
                "BOGZ1FL792150005Q",
                "BOGZ1FL793169175Q",
                "BOGZ1FL634090005Q",
                "GDP"
        ],
        "definition": "(Fed funds & repo liabilities + open market paper liabilities + money market fund shares of domestic financial sectors) / GDP, ratio (quarter-end levels).",
        "frequency": "Quarterly",
        "note": "Omits uninsured deposits, variable-rate demand obligations, and securities lending (not in Z.1)."
},
    "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC) / Chicago Fed, Y-9C bulk data",
        "source_url": None,
        "series_id": [
                "BHCK2800",
                "BHCKB995",
                "BHCKB996",
                "BHCK2332",
                "BHCK2170"
        ],
        "definition": "100 x (fed funds purchased + repos + other borrowed money with remaining maturity <= 1 year) / total assets, aggregated across all Y-9C filers, percent.",
        "frequency": "Quarterly",
        "note": "Fed funds + repos leg spliced: BHCK2800 (1990Q1-2001Q4) to BHCKB995+BHCKB996 (2002Q1+); level runs below the paper's broader 'short-term money' aggregate."
},
    "Bank Leverage (Total common equity to total assets)": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC) / Chicago Fed, Y-9C bulk data",
        "source_url": None,
        "series_id": [
                "BHCK3217",
                "BHCK3163",
                "BHCK2170"
        ],
        "definition": "100 x (total common equity - intangible assets) / (total assets - intangible assets), i.e. tangible common equity / tangible assets, aggregated across all Y-9C filers, percent.",
        "frequency": "Quarterly",
        "note": ""
},
    "Bank Leverage (Tier 1 common equity ratio)": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC) / Chicago Fed, Y-9C bulk data",
        "source_url": None,
        "series_id": [
                "BHCK8274",
                "BHCK3283",
                "BHCK3000",
                "BHCKC502",
                "BHCKB704"
        ],
        "definition": "100 x (Tier 1 capital - perpetual preferred - minority interest - qualifying trust preferred) / total risk-weighted assets, Y-9C aggregate, percent.",
        "frequency": "Quarterly",
        "note": "Series starts 2001Q1 (risk-weighted assets item BHCKB704 first appears then); spliced across the Basel III 2014 break to HC-R reporters (2014Q1+). Pre-2004 trust preferred treated as zero."
},
    "Bank Leverage (Total Capital Ratio)": {
        "status": "proxy",
        "source": "Federal Financial Institutions Examination Council (FFIEC), Call Reports / Y-9C Schedule RC-R",
        "source_url": None,
        "series_id": [
                "BHCK8274",
                "BHCK8275",
                "BHCKB704"
        ],
        "definition": "100 x (Tier 1 + Tier 2 capital) / total risk-weighted assets, percent.",
        "frequency": "Quarterly",
        "note": "Series starts 2001Q1 (risk-weighted assets item first appears then); rebuilt from Y-9C/Call Report RC-R aggregates with a Basel III HC-R splice from 2014Q1. No single landing-page URL is documented in the pipeline code, so no link is claimed."
},
    "Ratio of Total Financial Sector Liabilities to GDP": {
        "status": "replicated",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL794190005Q",
        "series_id": [
                "BOGZ1FL794190005Q",
                "GDP"
        ],
        "definition": "(Domestic financial-sector total liabilities, $M / 1000) / GDP ($B SAAR), ratio (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Ratio of Nonfinancial Business Savings to GDP": {
        "status": "proxy",
        "source": "U.S. Bureau of Economic Analysis (BEA), NIPA quarterly bulk data",
        "source_url": "https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt",
        "series_id": [
                "W995RC",
                "A191RC"
        ],
        "definition": "100 x (net lending (+) / net borrowing (-) of domestic business, i.e. saving net of capital formation) / GDP, percent.",
        "frequency": "Quarterly",
        "note": "Current-vintage NIPA Table 5.1 no longer splits financial vs nonfinancial business; domestic-business net lending is the closest public quarterly aggregate."
},
    "Comsumer Credit of Riskier Borrowers (Ratio of consumer credit debt owed by riskier borrowers to DPI)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/REVOLSL",
        "series_id": [
                "REVOLSL",
                "DPI"
        ],
        "definition": "100 x (quarterly average revolving consumer credit outstanding, $M) / (quarterly average nominal DPI, $B x 1000), percent of DPI.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "Revolving (credit-card) balances are the segment where riskier borrowers concentrate; the stock/DPI ratio tracks the paper's CCP-microdata riskier-borrower debt stock."
},
    "Consumer Credit (Consumer credit ratio to GDP)": {
        "status": "replicated",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/TOTALSL",
        "series_id": [
                "TOTALSL",
                "GDP"
        ],
        "definition": "Total consumer credit outstanding ($B) / GDP ($B SAAR), ratio (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Consumer Credit (Consumer credit debt service ratio to DPI)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/TDSP",
        "series_id": [
                "TDSP"
        ],
        "definition": "Total household debt service payments as a percent of disposable personal income (Fed DSR release), percent (quarter-end values).",
        "frequency": "Quarterly",
        "note": "TDSP covers all household debt including mortgages, while the paper uses a consumer-only DSR; series starts 2005Q1 (the current DSR release's history boundary)."
},
    "Debt Burden of Nonfinancial Businesses (Debt-to-Income ratio)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/BCNSDODNS",
        "series_id": [
                "BCNSDODNS",
                "TCMILBSNNB",
                "GDP"
        ],
        "definition": "100 x (nonfinancial corporate + noncorporate business debt securities and loans) / GDP, percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Real Debt Growth of Nonfinancial Businesses": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/BCNSDODNS",
        "series_id": [
                "BCNSDODNS",
                "TCMILBSNNB",
                "GDPDEF"
        ],
        "definition": "Year-on-year percent change of GDP-deflated total nonfinancial business debt.",
        "frequency": "Quarterly",
        "note": ""
},
    "Ratio of Household Savings to GDP": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/PSAVERT",
        "series_id": [
                "PSAVERT",
                "DSPI",
                "GDP"
        ],
        "definition": "(Personal saving rate as a share of DPI) x (nominal DPI) / GDP, i.e. household saving as a share of GDP.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": ""
},
    "Debt Burden of Nonfinancial Business (Interest expenses)": {
        "status": "proxy",
        "source": "U.S. Bureau of Economic Analysis (BEA), NIPA quarterly bulk data",
        "source_url": "https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt",
        "series_id": [
                "B471RC",
                "A466RC",
                "B467RC",
                "A455RC",
                "A457RC"
        ],
        "definition": "100 x (net interest and miscellaneous payments) / (undistributed after-tax profits + consumption of fixed capital) for nonfinancial corporate business, percent.",
        "frequency": "Quarterly",
        "note": ""
},
    "Risky Debt of Nonfinancial Businesses (Deep junk Issuance Share)": {
        "status": "proxy",
        "source": "Board of Governors of the Federal Reserve System, FEDS Notes excess bond premium file",
        "source_url": "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv",
        "series_id": [
                "GZ spread (FEDS Notes EBP file)"
        ],
        "definition": "Negated quarterly mean of the monthly excess bond premium (EBP), in percent: strong sentiment (low/negative EBP) coincides with low-quality firms issuing.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "Honest coincident proxy only (weak correlation): no public quarterly CCC-or-below share of high-yield issuance exists."
},
    "Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/HHMSDODNS",
        "series_id": [
                "HHMSDODNS",
                "DPI"
        ],
        "definition": "100 x (household mortgage debt, $B) / (nominal DPI, $B SAAR), percent of DPI.",
        "frequency": "Quarterly",
        "note": "Aggregate mortgage-debt/DPI stock as the public stand-in for the paper's CCP-based riskier-borrower mortgage debt/DPI."
},
    "Home Mortage Debt (Ratio of total mortgage debt to GDP)": {
        "status": "replicated",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/HHMSDODNS",
        "series_id": [
                "HHMSDODNS",
                "GDP"
        ],
        "definition": "Household mortgage debt ($B) / GDP ($B SAAR), ratio (quarter-end levels).",
        "frequency": "Quarterly",
        "note": ""
},
    "Home Mortgage Debt (Total mortgage debt service ratio to DPI)": {
        "status": "replicated",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/MDSP",
        "series_id": [
                "MDSP"
        ],
        "definition": "Mortgage debt service payments as a percent of disposable personal income (Fed DSR release), percent (quarter-end values).",
        "frequency": "Quarterly",
        "note": "Series starts 2005Q1 (the current DSR release's history boundary)."
},
    "Risky Debt of Nonfinancial Businesses": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/BCNSDODNS",
        "series_id": [
                "BCNSDODNS",
                "BOGZ1FL104090103Q"
        ],
        "definition": "100 x (nonfinancial corporate business debt securities and loans) / (nonfinancial corporate business total assets), percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": "Aggregate net-leverage analogue of the paper's Compustat-based net leverage of speculative-grade/unrated firms."
},
    "Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)": {
        "status": "proxy",
        "source": "Federal Reserve Z.1 Financial Accounts via FRED",
        "source_url": "https://fred.stlouisfed.org/series/BOGZ1FL893065125Q",
        "series_id": [
                "BOGZ1FL893065125Q",
                "HHMSDODNS"
        ],
        "definition": "100 x (closed-end junior-lien home equity loans outstanding) / (household mortgage debt), percent (quarter-end levels).",
        "frequency": "Quarterly",
        "note": "Series starts 1990Q4 (Z.1 home-equity-loan history boundary; 1990Q1-Q3 are missing). A stock share, not purchase-money incidence."
},
    "Consumer Credit of Riskier Borrowers (Incidence of very rapid consumer borrowing by riskier borrowers)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/REVOLSL",
        "series_id": [
                "REVOLSL"
        ],
        "definition": "Year-on-year percent change of quarterly revolving consumer credit outstanding, percent.",
        "frequency": "Quarterly",
        "note": "Rapid aggregate revolving-balance growth as the public stand-in for the paper's CCP-based incidence of very rapid borrowing."
},
    "Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/HHMSDODNS",
        "series_id": [
                "HHMSDODNS"
        ],
        "definition": "Year-on-year percent change of quarterly household mortgage debt outstanding, percent.",
        "frequency": "Quarterly",
        "note": ""
},
    "Spread of Corporate Bonds (over Treasury Securities) (BBB)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/BAA",
        "series_id": [
                "BAA",
                "DGS10"
        ],
        "definition": "Quarterly mean of monthly Moody's Baa yield minus quarterly mean of daily 10-year Treasury yield, percentage points.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "Public analog of the paper's ICE BofA BBB option-adjusted spread."
},
    "Indicators of Volitility (Credit Default Swap premium)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED) + Board of Governors FEDS Notes EBP file",
        "source_url": "https://fred.stlouisfed.org/series/BAA",
        "series_id": [
                "BAA",
                "DGS10",
                "EBP (FEDS Notes file)"
        ],
        "definition": "Expected-default component of the Baa credit spread: (Moody's Baa - 10y Treasury) minus the Fed excess bond premium (Gilchrist-Zakrajsek decomposition), percentage points, quarterly means.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "Public analog of proprietary Markit IG CDS premia; distinct from the full BBB spread by construction (correlation 0.58). EBP file: https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv (1973+)."
},
    "Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)": {
        "status": "replicated",
        "source": "Shiller long-run stock market data + Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "http://www.econ.yale.edu/~shiller/data/ie_data.xls",
        "series_id": [
                "Shiller E/P (ie_data)",
                "DGS10"
        ],
        "definition": "Shiller earnings/price ratio minus the 10-year Treasury yield (quarterly mean of monthly E/P minus quarterly mean of daily DGS10), percentage points.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": ""
},
    "Equity Valuations (Earnings/Price ratio )": {
        "status": "replicated",
        "source": "Shiller long-run stock market data",
        "source_url": "http://www.econ.yale.edu/~shiller/data/ie_data.xls",
        "series_id": [
                "Shiller E/P (ie_data)"
        ],
        "definition": "Shiller earnings/price ratio, quarterly mean of monthly values, percent.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": ""
},
    "Mortage Lending and FICO Scores (FICO scores)": {
        "status": "proxy",
        "source": "GSE credit supplements (Fannie Mae), transcribed annual file",
        "source_url": None,
        "series_id": [],
        "definition": "Annual UPB-weighted mean origination FICO for GSE single-family acquisitions, interpolated from annual (mid-year) points to quarterly by natural cubic spline.",
        "frequency": "Quarterly (annual data splined to quarterly)",
        "note": "Series starts 2003Q1: the acquisitions table begins with the 2003 acquisition year; earlier supplements profiled the outstanding book, not acquisitions by year. No public landing page is documented in the pipeline code, so no link is claimed."
},
    "National House Prices Relative to Rents": {
        "status": "replicated",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/CSUSHPISA",
        "series_id": [
                "CSUSHPISA",
                "CUSR0000SEHA"
        ],
        "definition": "S&P/Case-Shiller U.S. National Home Price Index divided by the CPI rent-of-primary-residence index, ratio of quarterly means.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": ""
},
    "Spread of Corporate Bonds (over Treasury Securities) (High Yield)": {
        "status": "proxy",
        "source": "Federal Reserve Bank of St. Louis (FRED) + Board of Governors FEDS Notes EBP file",
        "source_url": "https://fred.stlouisfed.org/series/BAA",
        "series_id": [
                "BAA",
                "DGS10",
                "EBP (FEDS Notes file)"
        ],
        "definition": "Monthly Moody's Baa-Treasury spread plus monthly excess bond premium, averaged to quarterly, percentage points (high-yield spread proxy).",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "EBP file: https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv. Replaces the earlier Baa-only proxy."
},
    "Investor Appetite for Risky Corporate Debt (Issuance of Junk Bonds and Leverage Loans)": {
        "status": "proxy",
        "source": "Board of Governors of the Federal Reserve System, FEDS Notes GZ credit-spread file",
        "source_url": "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv",
        "series_id": [
                "GZ spread (FEDS Notes EBP file)"
        ],
        "definition": "Inverse of the quarterly mean monthly Gilchrist-Zakrajsek average credit spread (1 / GZ spread): tight spreads read as strong investor appetite for risky debt.",
        "frequency": "Quarterly (quarterly mean of monthly)",
        "note": "Public proxy: no public quarterly leveraged-loan issuance series exists and SIFMA public quarterly HY history is truncated; proprietary Dealogic/LCD issuance is the paper's concept."
},
    "Prices for Commercial Real Estate (CRE)": {
        "status": "proxy",
        "source": "Bank for International Settlements (BIS), commercial property price statistics",
        "source_url": None,
        "series_id": [
                "BIS commercial property prices, US (Q:US:0:A:0:2:6:0)"
        ],
        "definition": "BIS U.S. quarterly commercial property price index, nominal, index level as published.",
        "frequency": "Quarterly",
        "note": "Manual download (BIS portal); the pipeline applies a 10-year moving-average detrend at index stage, so level/base differences wash out. Draft URL https://www.bis.org/statistics/pp/pp_detailed.htm returned HTTP 404; nulled."
},
    "Change in Lending Standards, CRE Loans": {
        "status": "proxy",
        "source": "Federal Reserve, Senior Loan Officer Opinion Survey (SLOOS) historical tables",
        "source_url": None,
        "series_id": [
                "SUBLPDRCS_N.Q",
                "SUBLPDRCSC_N.Q",
                "SUBLPDRCSN_N.Q",
                "SUBLPDRCSM_N.Q"
        ],
        "definition": "Net percent of domestic banks tightening standards for CRE loans, percent.",
        "frequency": "Quarterly",
        "note": "Splice: discontinued aggregate series 1990Q2-2013Q2 (re-dated back one quarter to the paper's reference-quarter convention) joined to the mean of the three successor sub-questions from 2013Q3. Draft URL https://www.federalreserve.gov/data/sloos/sloos_xml.htm returned HTTP 404; nulled. Series read from work/raw/sloos_cre.csv (pre-transcribed)."
},
    "Mortage Lending and FICO Scores (Change in Lending Standards)": {
        "status": "proxy",
        "source": "Federal Reserve, Senior Loan Officer Opinion Survey (SLOOS) via FRED",
        "source_url": "https://fred.stlouisfed.org/series/DRTSPM",
        "series_id": [
                "DRTSPM"
        ],
        "definition": "Net percent of banks tightening standards for prime residential mortgages (SLOOS), quarterly survey value.",
        "frequency": "Quarterly",
        "note": "Series starts 2007Q2: the April 2007 survey split the single all-residential question into prime/nontraditional/subprime and discontinued the aggregate; the prime-only concept did not exist before."
},
    "Investor Appetite for Risky Corporate Debt (Change in Bank Lending Standards C&I Loans)": {
        "status": "replicated",
        "source": "Federal Reserve, Senior Loan Officer Opinion Survey (SLOOS) via FRED",
        "source_url": "https://fred.stlouisfed.org/series/DRTSCILM",
        "series_id": [
                "DRTSCILM"
        ],
        "definition": "Net percent of banks tightening standards for C&I loans to large and middle-market firms (SLOOS), quarterly survey value.",
        "frequency": "Quarterly",
        "note": ""
},
    "Indicators of Volitility (VIX)": {
        "status": "replicated",
        "source": "Federal Reserve Bank of St. Louis (FRED)",
        "source_url": "https://fred.stlouisfed.org/series/VIXCLS",
        "series_id": [
                "VIXCLS"
        ],
        "definition": "CBOE Volatility Index (VIX), quarterly mean of daily closes; the pipeline applies a log transform at index stage.",
        "frequency": "Quarterly (quarterly mean of daily)",
        "note": ""
},
    "SRISK (Total capital shortfall relative to total assets)": {
        "status": "proxy",
        "source": "Built series (NYU Stern V-LAB method) from Y-9C book data, Yahoo Finance prices, SEC EDGAR XBRL",
        "source_url": None,
        "series_id": [],
        "definition": "Sum of positive firm-level SRISK over 20 large U.S. BHCs divided by their summed book assets; firm SRISK = max(0, 8% x book liabilities - 92% x market cap x (1 - LRMES)).",
        "frequency": "Quarterly",
        "note": "V-LAB's downloadable SRISK requires login, so the series is rebuilt from public inputs using V-LAB's documented method. Series truncated at 2000Q1 on input reliability; survivors-only panel (delisted crisis-era firms excluded), so the 2008 peak is understated vs V-LAB. No single landing-page URL is documented in the pipeline code, so no link is claimed."
},
    "Granger-causality connections among large financial institutions": {
        "status": "proxy",
        "source": "Built series from Yahoo Finance monthly returns (Billio et al. 2012 method)",
        "source_url": None,
        "series_id": [],
        "definition": "Share of statistically significant (5%, HAC-robust) pairwise linear Granger-causality edges among monthly log returns of 30 large listed U.S. financial firms, 36-month rolling windows stepped quarterly.",
        "frequency": "Quarterly",
        "note": "Billio et al. use 100 firms with GARCH pre-filtering; hedge funds have no public return data and delisted firms no public price history, so this replication uses 30 listed firms without GARCH filtering. No single landing-page URL is documented in the pipeline code, so no link is claimed."
},
    "Hedge Fund Leverage (Gross assets to net assets, qualifying hedge funds)": {
        "status": "proxy",
        "source": "Office of Financial Research (OFR) Hedge Fund Monitor, SEC Form PF aggregates",
        "source_url": "https://data.financialresearch.gov/hf/v1/series/multifull?mnemonics=FPF-ALLQHF_GAV_SUM,FPF-ALLQHF_NAV_SUM&output=csv",
        "series_id": [
                "FPF-ALLQHF_GAV_SUM",
                "FPF-ALLQHF_NAV_SUM"
        ],
        "definition": "Ratio of published Form PF aggregates: gross assets / net assets of qualifying hedge funds, equal to the net-asset-weighted mean fund-level leverage ratio.",
        "frequency": "Quarterly",
        "note": "Series begins 2013Q1 (Form PF quarterly reporting start); aggregate ratio, not a fund-level distribution; published with roughly a one-quarter lag."
},
    "Hedge Fund Leverage (SCOOS net percent reporting increased use of leverage)": {
        "status": "proxy",
        "source": "Federal Reserve, Senior Credit Officer Opinion Survey (SCOOS) via FRED",
        "source_url": "https://fred.stlouisfed.org/series/EXHE2C1Q8NP",
        "series_id": [
                "EXHE2C1Q8NP"
        ],
        "definition": "Net percent of dealers reporting increased use of leverage by hedge funds, quarterly survey value (quarter-start dates).",
        "frequency": "Quarterly",
        "note": "Series begins 2011Q3; a diffusion index of changes in leverage use, not a leverage level (bounded -100..100)."
},
}

# Full SOURCES table keyed by exact panel column name
SOURCES = {
    col: {
        "title": col,
        "short": SHORT_NAMES[col],
        "component": SPEC[col][0],
        "status": meta["status"],
        "source": meta["source"],
        "source_url": meta["source_url"],
        "series_id": list(meta["series_id"]),
        "definition": meta["definition"],
        "frequency": meta["frequency"],
        "note": meta["note"],
    }
    for col, meta in _SOURCES_RAW.items()
}


def build_indicators_meta(data_dir: Path | str) -> dict:
    """Compute indicator provenance metadata using Polars."""
    data_dir = Path(data_dir)
    p_comp_path = data_dir / "panel_complete.csv"
    p_q_path = data_dir / "panel_quarterly.csv"
    p_now_path = data_dir / "nowcast_provenance.csv"

    if not p_comp_path.exists():
        raise FileNotFoundError(f"Missing required panel file: {p_comp_path}")
    if not p_q_path.exists():
        raise FileNotFoundError(f"Missing required panel file: {p_q_path}")
    if not p_now_path.exists():
        raise FileNotFoundError(f"Missing required nowcast file: {p_now_path}")

    # Polars, never pandas
    df_comp = pl.read_csv(p_comp_path)
    df_q = pl.read_csv(p_q_path)
    df_now = pl.read_csv(p_now_path)

    comp_cols = [c for c in df_comp.columns if c != "quarter"]
    if len(comp_cols) != 48:
        raise ValueError(f"Expected exactly 48 panel indicators, found {len(comp_cols)}")

    sources_keys = set(SOURCES.keys())
    panel_cols_set = set(comp_cols)
    missing_in_sources = panel_cols_set - sources_keys
    if missing_in_sources:
        raise ValueError(f"Panel indicators missing from SOURCES: {missing_in_sources}")
    missing_in_panel = sources_keys - panel_cols_set
    if missing_in_panel:
        raise ValueError(f"SOURCES entries missing from panel: {missing_in_panel}")

    q_cols = [c for c in df_q.columns if c != "quarter"]
    if set(q_cols) != panel_cols_set:
        raise ValueError("panel_quarterly columns do not match panel_complete columns")

    comp_quarters = df_comp["quarter"].to_list()
    q_quarters = df_q["quarter"].to_list()
    panel_last_quarter = comp_quarters[-1]

    # Map nowcasts: (indicator, quarter) -> value_source
    nowcast_map = {}
    for row in df_now.iter_rows(named=True):
        nowcast_map[(row["indicator"], row["quarter"])] = row["value_source"]

    indicators = []
    for col in comp_cols:
        src = SOURCES[col]

        # panel_complete observations
        comp_vals = df_comp[col].to_list()
        comp_obs = [(q, v) for q, v in zip(comp_quarters, comp_vals) if v is not None]
        if not comp_obs:
            raise ValueError(f"No non-null observations for {col} in panel_complete.csv")

        first_obs = comp_obs[0][0]
        last_obs, last_val = comp_obs[-1]
        n_obs = len(comp_obs)

        if not isinstance(last_val, (int, float)) or not math.isfinite(last_val):
            raise ValueError(f"Non-finite last_value for {col}: {last_val}")
        last_val = float(last_val)

        # panel_quarterly observations
        q_vals = df_q[col].to_list()
        q_obs = [(q, v) for q, v in zip(q_quarters, q_vals) if v is not None]
        if not q_obs:
            raise ValueError(f"No non-null observations for {col} in panel_quarterly.csv")

        last_observed_quarter, last_observed_val = q_obs[-1]
        n_observed = len(q_obs)

        if not isinstance(last_observed_val, (int, float)) or not math.isfinite(last_observed_val):
            raise ValueError(f"Non-finite last_observed_value for {col}: {last_observed_val}")
        last_observed_val = float(last_observed_val)

        # Nowcast cross-reference rule
        if (col, last_obs) in nowcast_map:
            last_value_status = "nowcast"
            nowcast_method = nowcast_map[(col, last_obs)]
        else:
            last_value_status = "observed"
            nowcast_method = None

        entry = {
            "title": src["title"],
            "short": src["short"],
            "component": src["component"],
            "status": src["status"],
            "source": src["source"],
            "source_url": src["source_url"],
            "series_id": list(src["series_id"]),
            "definition": src["definition"],
            "frequency": src["frequency"],
            "note": src["note"],
            "first_observation": first_obs,
            "last_observation": last_obs,
            "last_value": last_val,
            "last_value_status": last_value_status,
            "nowcast_method": nowcast_method,
            "last_observed_quarter": last_observed_quarter,
            "last_observed_value": last_observed_val,
            "n_observations": n_obs,
            "n_observed": n_observed,
        }
        indicators.append(entry)

    return {
        "_comment": "GENERATED — DO NOT HAND-EDIT: python3 src/indicators_meta.py",
        "generated_by": "python3 src/indicators_meta.py",
        "generated_from": [
            "panel_complete.csv",
            "panel_quarterly.csv",
            "nowcast_provenance.csv",
        ],
        "panel_last_quarter": panel_last_quarter,
        "indicators": indicators,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"), help="Path to data directory")
    ap.add_argument("--out", default=None, help="Path to output JSON (defaults to <data>/indicators_meta.json)")
    args = ap.parse_args()

    data_dir = Path(args.data)
    out_path = Path(args.out) if args.out else data_dir / "indicators_meta.json"

    meta = build_indicators_meta(data_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_strict(meta, indent=1) + "\n")
    print(f"Wrote {out_path} ({len(meta['indicators'])} indicators, panel end {meta['panel_last_quarter']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

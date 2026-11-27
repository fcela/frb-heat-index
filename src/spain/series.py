"""Indicator registry for the Spain heat index.

Every indicator carries the same metadata contract as the U.S. panel
(title, short, component, status, source, source_url, series_id,
definition, frequency) plus the construction fields this build needs:
`sign` (+1: a higher raw value is more vulnerable; -1: lower is more
vulnerable) and a `route` describing how the series is fetched and
reduced to quarterly frequency.

Statuses:
- "live": fetched from a free public source and in the panel.
- "proxy": live, but a labelled stand-in for the concept (the
  definition says so).
- "source-pending": the concept belongs in the index but no free,
  machine-readable route was resolved at build time. Pending
  indicators are listed on the page with the reason; they are never
  silently substituted with a different concept.

Routes are exercised by src/spain/fetch.py; raw responses are archived
under data/spain/, one file per fetched series.
"""

# ---------------------------------------------------------------- BIS

BIS_GAP_URL = ("https://stats.bis.org/api/v1/data/WS_CREDIT_GAP/"
               "Q.ES.P.A.C?format=csv")
BIS_DSR_URL = "https://stats.bis.org/api/v1/data/WS_DSR/Q.ES.P?format=csv"
BIS_TC_URL = "https://stats.bis.org/api/v1/data/WS_TC/Q.ES.{sector}.A?format=csv"

# ---------------------------------------------------------------- FRED

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# ---------------------------------------------------------------- BdE

BDE_TABLE_URL = ("https://www.bde.es/webbe/es/estadisticas/compartido/"
                 "datos/csv/{table}.csv")
BDE_PAGE_URL = ("https://www.bde.es/webbe/es/estadisticas/temas/"
                "boletin-estadistico.html")

# ---------------------------------------------------------------- ECB

ECB_CISS_URL = ("https://data-api.ecb.europa.eu/service/data/CISS/"
                "D.ES.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata")

# ---------------------------------------------------------------- INE

INE_IPV_URL = ("https://servicios.ine.es/wstempus/js/ES/"
               "DATOS_SERIE/IPV948?tip=AM&date=19990101:20990101")

INDICATORS = [
    # ---------------------------------------------------------- Credit cycle
    {
        "id": "credit_gap",
        "title": "Credit-to-GDP Gap (BIS, private non-financial sector, Spain)",
        "short": "Credit-to-GDP gap",
        "component": "Credit cycle",
        "status": "live",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), credit-to-GDP gap statistics (BIS Data Portal, SDMX)",
        "source_url": BIS_GAP_URL,
        "series_id": ["Q.ES.P.A.C (WS_CREDIT_GAP)"],
        "definition": "Credit-to-GDP gap for Spain as published by the BIS: total credit to the private non-financial sector from all lenders, percent of GDP, minus its long-run trend (one-sided HP filter, lambda 400,000), percentage points. The Basel III countercyclical capital buffer guide; the same source family as the U.S. pair in the main index.",
        "frequency": "Quarterly",
        "route": {"type": "bis_sdmx", "url": BIS_GAP_URL, "archive": "bis_credit_gap.csv"},
    },
    {
        "id": "dsr",
        "title": "Debt Service Ratio (BIS, private non-financial sector, Spain)",
        "short": "Debt service ratio",
        "component": "Credit cycle",
        "status": "live",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), debt service ratio statistics (BIS Data Portal, SDMX)",
        "source_url": BIS_DSR_URL,
        "series_id": ["Q.ES.P (WS_DSR)"],
        "definition": "Debt service ratio of the Spanish private non-financial sector as published by the BIS: interest payments plus amortisation as a percent of income, percent. Published from 1999Q1.",
        "frequency": "Quarterly",
        "route": {"type": "bis_sdmx", "url": BIS_DSR_URL, "archive": "bis_dsr.csv"},
    },
    # ---------------------------------------------------------- Bank leverage
    {
        "id": "equity_ratio",
        "title": "Deposit Institutions' Capital and Reserves to Total Liabilities (Banco de España)",
        "short": "Capital & reserves / balance sheet",
        "component": "Bank leverage",
        "status": "live",
        "sign": -1,
        "source": "Banco de España, Boletín Estadístico, table 8.32 (deposit institutions' balance sheet, liabilities)",
        "source_url": BDE_TABLE_URL.format(table="be0832"),
        "series_id": ["BE_8_32.9", "BE_8_32.1"],
        "definition": "100 x (capital and reserves of Spanish deposit institutions — credit institutions and EFCs, BE_8_32.9) / (their total liabilities, BE_8_32.1), percent, quarter-end month. The balance-sheet leverage counterpart of the U.S. Y-9C equity ratios; oriented so that a thinner capital share reads as more vulnerable.",
        "frequency": "Monthly (quarter-end)",
        "route": {"type": "derived", "archive": "equity_ratio.csv",
                  "inputs": [{"table": "be0832", "alias": "BE_8_32.9", "archive": "bde_be0832_capital.csv"},
                             {"table": "be0832", "alias": "BE_8_32.1", "archive": "bde_be0832_total_liabilities.csv"}],
                  "formula": "100 * capital / total_liabilities"},
    },
    {
        "id": "cet1",
        "title": "CET1 Ratio of Spanish Deposit Institutions",
        "short": "CET1 ratio",
        "component": "Bank leverage",
        "status": "source-pending",
        "sign": -1,
        "source": "Banco de España / ECB supervisory statistics",
        "source_url": None,
        "series_id": [],
        "definition": "Common Equity Tier 1 capital as a percent of risk-weighted assets for Spanish deposit institutions.",
        "frequency": "Quarterly",
        "pending_reason": "Published in the Banco de España and ECB supervisory statistics portals as report tables; no stable machine-readable public series route was resolved for this build. The balance-sheet capital share above carries the leverage component meanwhile.",
    },
    # ---------------------------------------------------------- Maturity transformation
    {
        "id": "loans_deposits",
        "title": "Deposit Institutions' Loans to Non-Bank Residents / Deposits of Other Resident Sectors (Banco de España)",
        "short": "Loans-to-deposits ratio",
        "component": "Maturity transformation",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, tables 8.33 and 8.34 (deposit institutions' balance sheet)",
        "source_url": BDE_TABLE_URL.format(table="be0833"),
        "series_id": ["BE_8_33.8", "BE_8_34.7"],
        "definition": "100 x (deposit institutions' loans and credits to non-bank residents in Spain — general government and other resident sectors, BE_8_33.8) / (deposits of other resident sectors at deposit institutions, BE_8_34.7), percent, quarter-end month. The funding gap that defined Spanish bank balance sheets before 2012: a ratio well above 100 means lending funded by wholesale markets and the Eurosystem rather than by deposits. The Banco de España's own stability-report variant, which adds securitised loans back to credit and strips repos from deposits, prints higher; this is the balance-sheet-tables variant, computed from two published aggregates with no adjustment.",
        "frequency": "Monthly (quarter-end)",
        "route": {"type": "derived", "archive": "loans_deposits.csv",
                  "inputs": [{"table": "be0833", "alias": "BE_8_33.8", "archive": "bde_be0833_loans_nonbank.csv"},
                             {"table": "be0834", "alias": "BE_8_34.7", "archive": "bde_be0834_deposits_osr.csv"}],
                  "formula": "100 * loans / deposits"},
    },
    # ---------------------------------------------------------- Size / concentration
    {
        "id": "fin_liab_gdp",
        "title": "Financial Institutions' Total Liabilities to GDP (Banco de España, financial accounts)",
        "short": "Financial-sector liabilities / GDP",
        "component": "Size/concentration",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Cuentas Financieras de la Economía Española (Boletín Estadístico, tables 2.5 and 2.1)",
        "source_url": BDE_TABLE_URL.format(table="be0205"),
        "series_id": ["BE_2_5.2.19", "BE_2_1.28"],
        "definition": "100 x (total liabilities of the financial institutions sector, all instruments, CFEE balance sheet, BE_2_5.2.19, published in thousands of euros) / (nominal GDP, trailing four quarters, BE_2_1.28, millions of euros), percent. The financial-accounts counterpart of the U.S. Z.1 size measures.",
        "frequency": "Quarterly",
        "route": {"type": "derived", "archive": "fin_liab_gdp.csv",
                  "inputs": [{"table": "be0205", "alias": "BE_2_5.2.19", "archive": "bde_be0205_fin_liabilities.csv", "scale": 0.001},
                             {"table": "be0201", "alias": "BE_2_1.28", "archive": "bde_be0201_gdp.csv"}],
                  "formula": "100 * fin_liabilities / sum4(gdp)"},
    },
    {
        "id": "top5_share",
        "title": "Top-5 Banking Groups' Share of System Assets",
        "short": "Top-5 asset share",
        "component": "Size/concentration",
        "status": "source-pending",
        "sign": 1,
        "source": "Banco de España, supervision and financial stability reports",
        "source_url": None,
        "series_id": [],
        "definition": "100 x (total assets of the five largest Spanish banking groups) / (total assets of the banking system), percent.",
        "frequency": "Annual",
        "pending_reason": "Published in Banco de España supervision and financial stability reports as report tables, not as a downloadable time series; no stable machine-readable public route was resolved for this build.",
    },
    # ---------------------------------------------------------- Home mortgages
    {
        "id": "mortgage_gdp",
        "title": "Housing Loans to Households to GDP (Banco de España)",
        "short": "Mortgage credit / GDP",
        "component": "Home mortgages",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, tables 8.19 and 2.1",
        "source_url": BDE_TABLE_URL.format(table="be0819"),
        "series_id": ["BE_8_19.15", "BE_2_1.28"],
        "definition": "100 x (MFI loans to households for house purchase, BE_8_19.15, quarter-end month) / (nominal GDP, trailing four quarters, BE_2_1.28), percent.",
        "frequency": "Monthly (quarter-end) / Quarterly",
        "route": {"type": "derived", "archive": "mortgage_gdp.csv",
                  "inputs": [{"table": "be0819", "alias": "BE_8_19.15", "archive": "bde_be0819_mortgage_loans.csv"},
                             {"table": "be0201", "alias": "BE_2_1.28", "archive": "bde_be0201_gdp.csv"}],
                  "formula": "100 * mortgage_loans / sum4(gdp)"},
    },
    {
        "id": "hh_credit_gdp",
        "title": "Total Credit to Households to GDP (BIS)",
        "short": "Household credit / GDP",
        "component": "Home mortgages",
        "status": "live",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), total credit statistics (BIS Data Portal, SDMX)",
        "source_url": BIS_TC_URL.format(sector="H"),
        "series_id": ["Q.ES.H.A (WS_TC), unit: percent of GDP"],
        "definition": "Total credit to Spanish households and NPISH from all lenders (loans and debt securities), percent of GDP, as published by the BIS.",
        "frequency": "Quarterly",
        "route": {"type": "bis_tc", "url": BIS_TC_URL.format(sector="H"), "archive": "bis_tc_households.csv"},
    },
    {
        "id": "var_rate_share",
        "title": "Variable-Rate Share of New Housing Loans",
        "short": "Variable-rate share, new mortgages",
        "component": "Home mortgages",
        "status": "source-pending",
        "sign": 1,
        "source": "ECB MFI interest rate (MIR) statistics / Banco de España",
        "source_url": None,
        "series_id": [],
        "definition": "100 x (new housing loans with an initial rate fixation of up to one year) / (all new housing loans), percent. The Spanish mortgage-risk gauge that replaces the U.S. GSE/FICO block: most Spanish mortgages reset off 12-month Euribor, so the fixation share measures how much of the stock reprices within a year of a rate rise.",
        "frequency": "Monthly",
        "pending_reason": "The fixation breakdown is published in the ECB MIR statistics and Banco de España new-business volumes; the SDMX key for the Spanish fixation split was not resolved at build time.",
    },
    # ---------------------------------------------------------- Consumer credit
    {
        "id": "consumer_gdp",
        "title": "Consumer Credit to Households to GDP (Banco de España)",
        "short": "Consumer credit / GDP",
        "component": "Consumer credit",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, tables 8.19 and 2.1",
        "source_url": BDE_TABLE_URL.format(table="be0819"),
        "series_id": ["BE_8_19.11", "BE_2_1.28"],
        "definition": "100 x (MFI consumer credit to households, BE_8_19.11, quarter-end month) / (nominal GDP, trailing four quarters, BE_2_1.28), percent.",
        "frequency": "Monthly (quarter-end) / Quarterly",
        "route": {"type": "derived", "archive": "consumer_gdp.csv",
                  "inputs": [{"table": "be0819", "alias": "BE_8_19.11", "archive": "bde_be0819_consumer_loans.csv"},
                             {"table": "be0201", "alias": "BE_2_1.28", "archive": "bde_be0201_gdp.csv"}],
                  "formula": "100 * consumer_loans / sum4(gdp)"},
    },
    # ---------------------------------------------------------- Nonfinancial business debt
    {
        "id": "nfc_credit_gdp",
        "title": "Total Credit to Non-Financial Corporations to GDP (BIS)",
        "short": "NFC credit / GDP",
        "component": "Nonfinancial business debt",
        "status": "live",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), total credit statistics (BIS Data Portal, SDMX)",
        "source_url": BIS_TC_URL.format(sector="N"),
        "series_id": ["Q.ES.N.A (WS_TC), unit: percent of GDP"],
        "definition": "Total credit to Spanish non-financial corporations from all lenders (loans and debt securities), percent of GDP, as published by the BIS.",
        "frequency": "Quarterly",
        "route": {"type": "bis_tc", "url": BIS_TC_URL.format(sector="N"), "archive": "bis_tc_nfc.csv"},
    },
    # ---------------------------------------------------------- Net saving
    {
        "id": "hh_net_saving",
        "title": "Household Net Financial Saving, Percent of GDP (Banco de España, financial accounts)",
        "short": "Household net financial saving / GDP",
        "component": "Net saving",
        "status": "proxy",
        "sign": -1,
        "source": "Banco de España, Cuentas Financieras de la Economía Española (Boletín Estadístico, table 2.7)",
        "source_url": BDE_TABLE_URL.format(table="be0207"),
        "series_id": ["BE_2_7.7"],
        "definition": "Households' and NPISH net financial transactions (net acquisition of financial assets minus net incurrence of liabilities) as a percent of GDP, as published by the Banco de España (BE_2_7.7, 'ratio sobre el PIB'). Labelled a proxy: it is the financial-accounts counterpart of the U.S. NIPA net saving rate, not the national-accounts saving rate itself; oriented so that lower (more negative) saving reads as more vulnerable. Spanish households were net borrowers of 5-7% of GDP at the 2007 boom peak and net lenders of nearly 9% in late 2020.",
        "frequency": "Quarterly",
        "route": {"type": "bde_column", "table": "be0207", "alias": "BE_2_7.7",
                  "archive": "bde_be0207_hh_net_fin.csv"},
    },
    # ---------------------------------------------------------- Residential real estate
    {
        "id": "rpp_yoy",
        "title": "Residential Property Prices, Annual Change (BIS via FRED)",
        "short": "House prices, y/y",
        "component": "Residential real estate",
        "status": "live",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), residential property price statistics, via FRED (Federal Reserve Bank of St. Louis)",
        "source_url": FRED_URL.format(sid="QESN628BIS"),
        "series_id": ["QESN628BIS"],
        "definition": "Four-quarter percent change of the BIS nominal residential property price index for Spain. A price-growth gauge: the level of the index trends, so the vulnerability signal is the boom rate, as in the U.S. panel's price measures.",
        "frequency": "Quarterly",
        "route": {"type": "fred_yoy", "url": FRED_URL.format(sid="QESN628BIS"), "archive": "fred_rpp_es.csv"},
    },
    {
        "id": "ipv_yoy",
        "title": "House Price Index, Annual Change (INE)",
        "short": "INE house prices, y/y",
        "component": "Residential real estate",
        "status": "live",
        "sign": 1,
        "source": "Instituto Nacional de Estadística (INE), Índice de Precios de Vivienda (IPV)",
        "source_url": INE_IPV_URL,
        "series_id": ["IPV948"],
        "definition": "Annual rate of change of the INE house price index (national, all dwellings), percent, as published. The IPV starts in 2007Q1; earlier quarters are missing values, never back-filled from another index.",
        "frequency": "Quarterly",
        "route": {"type": "ine", "url": INE_IPV_URL, "archive": "ine_ipv_yoy.csv"},
    },
    {
        "id": "price_income",
        "title": "House Price to Household Income Ratio (Banco de España)",
        "short": "Price-to-income",
        "component": "Residential real estate",
        "status": "source-pending",
        "sign": 1,
        "source": "Banco de España, housing market indicators",
        "source_url": None,
        "series_id": [],
        "definition": "Price of a standard dwelling as a multiple of gross annual household income, as published in the Banco de España housing market indicators.",
        "frequency": "Quarterly",
        "pending_reason": "Published in the Banco de España housing-market indicators workbook; no stable machine-readable public series route was resolved for this build. The two price-growth gauges above carry the component meanwhile.",
    },
    # ---------------------------------------------------------- Business credit
    {
        "id": "bls_standards",
        "title": "Bank Lending Survey: Credit Standards for Enterprises, Spain (ECB)",
        "short": "BLS credit standards (enterprises)",
        "component": "Business credit",
        "status": "source-pending",
        "sign": 1,
        "source": "European Central Bank, Bank Lending Survey (Spain breakdown)",
        "source_url": None,
        "series_id": [],
        "definition": "Net percentage of banks reporting a tightening of credit standards on loans to enterprises in Spain, ECB Bank Lending Survey — the SLOOS counterpart in the U.S. panel.",
        "frequency": "Quarterly",
        "pending_reason": "The ECB Data Portal serves the Bank Lending Survey under the BLS dataflow; the key for the Spain enterprise-standards question was not resolved at build time. The component ships empty rather than substituting a U.S. or euro-area aggregate.",
    },
    {
        "id": "tedr_spread",
        "title": "New-Lending Rate Spread on Loans to Non-Financial Corporations (Banco de España, TEDR)",
        "short": "New-lending spread (NFC)",
        "component": "Business credit",
        "status": "source-pending",
        "sign": 1,
        "source": "Banco de España, TEDR interest rate statistics (Boletín Estadístico, chapter 19)",
        "source_url": None,
        "series_id": [],
        "definition": "Average rate on new loans to non-financial corporations (TEDR) minus the matched-maturity euro swap rate, basis points: the price of new business credit.",
        "frequency": "Monthly",
        "pending_reason": "The TEDR tables are published in chapter 19 of the Boletín Estadístico; the new-business series codes and a free matched swap benchmark were not both resolved at build time.",
    },
    # ---------------------------------------------------------- Short-term wholesale funding
    {
        "id": "wholesale_share",
        "title": "MFI Debt Securities Issued to Total Liabilities (Banco de España)",
        "short": "Market wholesale funding share",
        "component": "Short-term wholesale funding",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, table 8.3 (MFI balance sheet, liabilities)",
        "source_url": BDE_TABLE_URL.format(table="be0803"),
        "series_id": ["BE_8_3.8", "BE_8_3.1"],
        "definition": "100 x (debt securities issued by Spanish MFIs, BE_8_3.8) / (total MFI liabilities, BE_8_3.1), percent, quarter-end month: the market-funded share of the balance sheet (cédulas, senior and securitisation bonds).",
        "frequency": "Monthly (quarter-end)",
        "route": {"type": "derived", "archive": "wholesale_share.csv",
                  "inputs": [{"table": "be0803", "alias": "BE_8_3.8", "archive": "bde_be0803_securities_issued.csv"},
                             {"table": "be0803", "alias": "BE_8_3.1", "archive": "bde_be0803_total_liabilities.csv"}],
                  "formula": "100 * securities_issued / total_liabilities"},
    },
    {
        "id": "eurosystem_share",
        "title": "Net Borrowing from the Eurosystem to Total MFI Liabilities (Banco de España)",
        "short": "Eurosystem borrowing share",
        "component": "Short-term wholesale funding",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, tables 7.3 and 8.3",
        "source_url": BDE_TABLE_URL.format(table="be0703"),
        "series_id": ["BE_7_3.4", "BE_8_3.1"],
        "definition": "100 x (Banco de España loans and credits to MFIs resident in Spain — monetary-policy lending, BE_7_3.4) / (total MFI liabilities, BE_8_3.1), percent, quarter-end month. The Spanish twist on the U.S. wholesale-funding component: when market funding closed in 2011-12 and again in 2020, the Eurosystem replaced it, so central-bank borrowing is part of the funding-fragility measure, not a separate story.",
        "frequency": "Monthly (quarter-end)",
        "route": {"type": "derived", "archive": "eurosystem_share.csv",
                  "inputs": [{"table": "be0703", "alias": "BE_7_3.4", "archive": "bde_be0703_lending_to_mfis.csv"},
                             {"table": "be0803", "alias": "BE_8_3.1", "archive": "bde_be0803_total_liabilities.csv"}],
                  "formula": "100 * eurosystem_lending / total_liabilities"},
    },
    # ---------------------------------------------------------- Sovereign–bank nexus
    {
        "id": "bono_bund_spread",
        "title": "10-Year Bono–Bund Spread (OECD via FRED)",
        "short": "Bono–Bund spread, 10y",
        "component": "Sovereign–bank nexus",
        "status": "live",
        "sign": 1,
        "source": "OECD long-term interest rates via FRED (Federal Reserve Bank of St. Louis)",
        "source_url": FRED_URL.format(sid="IRLTLT01ESM156N"),
        "series_id": ["IRLTLT01ESM156N", "IRLTLT01DEM156N"],
        "definition": "Quarterly mean of the monthly spread between the Spanish 10-year government bond yield (IRLTLT01ESM156N) and the German 10-year yield (IRLTLT01DEM156N), in basis points. The price leg of the sovereign–bank nexus: the U.S. index has no counterpart because the Treasury is the safe asset.",
        "frequency": "Monthly (quarterly mean)",
        "route": {"type": "derived", "archive": "bono_bund_spread.csv",
                  "inputs": [{"fred": "IRLTLT01ESM156N", "archive": "fred_es_10y.csv"},
                             {"fred": "IRLTLT01DEM156N", "archive": "fred_de_10y.csv"}],
                  "formula": "100 * (es_10y - de_10y), quarterly mean of monthly"},
    },
    {
        "id": "nexus_holdings",
        "title": "MFIs' Holdings of Spanish General Government Debt Securities to Total Assets (Banco de España)",
        "short": "Banks' sovereign holdings share",
        "component": "Sovereign–bank nexus",
        "status": "live",
        "sign": 1,
        "source": "Banco de España, Boletín Estadístico, tables 8.5 and 8.3",
        "source_url": BDE_TABLE_URL.format(table="be0805"),
        "series_id": ["BE_8_5.6", "BE_8_3.1"],
        "definition": "100 x (debt securities issued by Spanish general government held by MFIs resident in Spain, BE_8_5.6) / (total MFI balance sheet, BE_8_3.1), percent, quarter-end month. The quantity leg of the nexus: how much of the banking system's balance sheet is the sovereign's paper. Loans to government are excluded; this is the securities book that marks to market.",
        "frequency": "Monthly (quarter-end)",
        "route": {"type": "derived", "archive": "nexus_holdings.csv",
                  "inputs": [{"table": "be0805", "alias": "BE_8_5.6", "archive": "bde_be0805_gov_securities.csv"},
                             {"table": "be0803", "alias": "BE_8_3.1", "archive": "bde_be0803_total_liabilities.csv"}],
                  "formula": "100 * gov_securities_held / total_balance"},
    },
    # ---------------------------------------------------------- Price volatility
    {
        "id": "ciss",
        "title": "Composite Indicator of Systemic Stress for Spain (ECB CISS)",
        "short": "CISS, Spain",
        "component": "Price volatility",
        "status": "live",
        "sign": 1,
        "source": "European Central Bank, Composite Indicator of Systemic Stress (CISS), ECB Data Portal",
        "source_url": ECB_CISS_URL,
        "series_id": ["CISS.D.ES.Z0Z.4F.EC.SS_CIN.IDX"],
        "definition": "Quarterly mean of the daily ECB Composite Indicator of Systemic Stress for Spain (new CISS), index 0-1. A market-price stress composite across money, bond, equity, financial-intermediary and foreign-exchange subindices for Spain specifically — not the euro-area aggregate.",
        "frequency": "Daily (quarterly mean)",
        "route": {"type": "ecb_ciss", "url": ECB_CISS_URL, "archive": "ecb_ciss_es.csv"},
    },
    # ---------------------------------------------------------- Equity valuation
    {
        "id": "ibex_yield",
        "title": "IBEX 35 Earnings / Dividend Yield",
        "short": "IBEX yield",
        "component": "Equity valuation",
        "status": "source-pending",
        "sign": -1,
        "source": "Bolsas y Mercados Españoles (BME) / ECB",
        "source_url": None,
        "series_id": [],
        "definition": "Earnings (or dividend) yield of the IBEX 35, percent. No Shiller CAPE exists for Spain; a yield is the available valuation gauge, oriented so that a lower yield (richer pricing) reads as more vulnerable.",
        "frequency": "Monthly",
        "pending_reason": "No stable free machine-readable series for an IBEX earnings or dividend yield was resolved at build time (BME publishes index statistics as report files; the ECB does not carry a Spanish earnings yield).",
    },
    # ---------------------------------------------------------- Corporate spreads
    {
        "id": "corp_spread",
        "title": "Corporate Bond Spread (euro-area proxy, labelled)",
        "short": "Corporate spread (EA proxy)",
        "component": "Corporate spreads",
        "status": "source-pending",
        "sign": 1,
        "source": "ICE BofA euro-area corporate indices / ECB",
        "source_url": None,
        "series_id": [],
        "definition": "Option-adjusted spread on euro-area corporate bonds, basis points, to be labelled a euro-area proxy on the page: Spain's corporate bond market is too thin for a clean domestic OAS, and Spanish issuers fund at euro-area spreads plus a sovereign-linked premium already measured by the Bono–Bund leg.",
        "frequency": "Daily",
        "pending_reason": "No free machine-readable route for an ICE BofA euro-area OAS was resolved at build time; the series ships pending rather than substituting a U.S. spread.",
    },
    # ---------------------------------------------------------- Commercial real estate
    {
        "id": "cre_prices",
        "title": "Commercial Property Prices, Spain (BIS)",
        "short": "Commercial property prices",
        "component": "Commercial real estate",
        "status": "source-pending",
        "sign": 1,
        "source": "Bank for International Settlements (BIS), commercial property price statistics",
        "source_url": None,
        "series_id": [],
        "definition": "Four-quarter percent change of the BIS commercial property price index for Spain.",
        "frequency": "Quarterly",
        "pending_reason": "The BIS commercial property price dataset is distributed as portal downloads rather than a stable SDMX series for Spain; no free machine-readable route was resolved at build time.",
    },
]

COMPONENTS = [
    "Credit cycle",
    "Bank leverage",
    "Maturity transformation",
    "Size/concentration",
    "Home mortgages",
    "Consumer credit",
    "Nonfinancial business debt",
    "Net saving",
    "Residential real estate",
    "Business credit",
    "Short-term wholesale funding",
    "Sovereign–bank nexus",
    "Price volatility",
    "Equity valuation",
    "Corporate spreads",
    "Commercial real estate",
]

PANEL_START = "1999Q1"

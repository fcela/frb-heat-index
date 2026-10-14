#!/usr/bin/env python3
"""Assemble fixed panel: replace 6-fix series, extend to 2026Q2. Polars only."""
import polars as pl
import numpy as np

REPO = "/home/hatch/workspace/frb-work/repo"
FIX = "/home/hatch/workspace/frb-work/fix"

panel = pl.read_csv(f"{REPO}/data/panel_quarterly.csv")
y9c = pl.read_csv(f"{FIX}/y9c_panel_full.csv")
z1 = pl.read_csv(f"{FIX}/z1_extracted.csv")

# --- Y-9C replacements (full history) ---
COLMAP = {
    "tangible": "Bank Leverage (Total common equity to total assets)",
    "t1c_cet1": "Bank Leverage (Tier 1 common equity ratio)",
    "total_cap": "Bank Leverage (Total Capital Ratio)",
    "loans_dep": "Maturity Mismatch (Loans as a percent of deposits)",
    "top5": "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets",
    "st_money": "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)",
}

# extend quarter index to 2026Q2
quarters = [f"{y}Q{q}" for y in range(1990, 2027) for q in range(1,5) if not (y==2026 and q>2)]
base = pl.DataFrame({"quarter": quarters})
panel = base.join(panel, on="quarter", how="left")

for src, dst in COLMAP.items():
    m = dict(zip(y9c["quarter"].to_list(), y9c[src].to_list()))
    panel = panel.with_columns(
        pl.col("quarter").replace_strict(m, default=None).alias("_new")
    ).with_columns(
        pl.when(pl.col("_new").is_not_null()).then(pl.col("_new")).otherwise(pl.col(dst)).alias(dst)
    ).drop("_new")

# --- SLOOS mortgage splice ---
def qmean(fn):
    df = pl.read_csv(fn)
    dcol, vcol = df.columns[0], df.columns[1]
    return (df.with_columns(
        (pl.col(dcol).str.slice(0,4)+"Q"+((pl.col(dcol).str.slice(5,2).cast(pl.Int32)-1)//3+1).cast(pl.String)).alias("quarter"),
        pl.col(vcol).cast(pl.Float64).alias("v"))
        .group_by("quarter").agg(pl.col("v").mean()).sort("quarter"))
old = qmean("/tmp/sloos_old.csv")
gov = qmean("/tmp/sloos_gov.csv")
sloos = pl.concat([old.filter(pl.col("quarter") <= "2014Q4"),
                   gov.filter(pl.col("quarter") >= "2015Q1")]).sort("quarter")
m = dict(zip(sloos["quarter"].to_list(), sloos["v"].to_list()))
dst = "Mortage Lending and FICO Scores (Change in Lending Standards)"
panel = panel.with_columns(pl.col("quarter").replace_strict(m, default=None).alias("_new")
    ).with_columns(pl.when(pl.col("_new").is_not_null()).then(pl.col("_new")).otherwise(pl.col(dst)).alias(dst)
    ).drop("_new")

# --- Z.1 derived (recompute from bulk, replaces dead-FRED-code inputs) ---
gdp = pl.read_csv("/tmp/gdp.csv").with_columns(
    (pl.col("observation_date").str.slice(0,4)+"Q"+((pl.col("observation_date").str.slice(5,2).cast(pl.Int32)-1)//3+1).cast(pl.String)).alias("quarter"),
    pl.col("GDP").cast(pl.Float64)).select(["quarter","GDP"]).rename({"GDP":"gdp"})
j = z1.join(gdp, on="quarter", how="left")
S, O = "BOGZ1FL794141005Q", "BOGZ1FL793169175Q"
RA, OA = "BOGZ1FL792050005Q", "BOGZ1FL793069175Q"
R, MMF = "BOGZ1FL792150005Q", "BOGZ1FL634090005Q"
CB, LN, TA = "BOGZ1FL793063005Q", "BOGZ1FL794035005Q", "BOGZ1FL794090005Q"
derived = j.select([
    "quarter",
    (100.0*(pl.col(S)+pl.col(O))/pl.col("gdp")).alias("gross"),
    (100.0*(pl.col(S)+pl.col(O)-pl.col(RA).fill_null(0)-pl.col(OA).fill_null(0))/pl.col("gdp")).alias("net"),
    ((pl.col(R)+pl.col(O)+pl.col(MMF))/pl.col("gdp")).alias("run"),
    ((pl.col(CB)+pl.col(LN))/pl.col(TA)).alias("illiq"),
])
# NFCB: debt/TA — fetch BCNSDODNS
import subprocess
subprocess.run(["curl","-s","--max-time","20","-A","curl/8.0",
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BCNSDODNS","-o","/tmp/nfcb_debt.csv"], check=False)
debt = pl.read_csv("/tmp/nfcb_debt.csv").with_columns(
    (pl.col("observation_date").str.slice(0,4)+"Q"+((pl.col("observation_date").str.slice(5,2).cast(pl.Int32)-1)//3+1).cast(pl.String)).alias("quarter"),
    pl.col("BCNSDODNS").cast(pl.Float64)).select(["quarter","BCNSDODNS"])
nfcb = z1.select(["quarter","BOGZ1FL104090103Q"]).join(debt, on="quarter", how="left").with_columns(
    (100.0*pl.col("BCNSDODNS")/pl.col("BOGZ1FL104090103Q")).alias("nfcb"))

ZCOL = {
    "gross": "Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)",
    "net": "Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)",
    "run": "Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)",
    "illiq": "Illiquidity Conventration Indicator",
}
for src, dst in ZCOL.items():
    m = dict(zip(derived["quarter"].to_list(), derived[src].to_list()))
    panel = panel.with_columns(pl.col("quarter").replace_strict(m, default=None).alias("_new")
        ).with_columns(pl.when(pl.col("_new").is_not_null()).then(pl.col("_new")).otherwise(pl.col(dst)).alias(dst)
        ).drop("_new")
m = dict(zip(nfcb["quarter"].to_list(), nfcb["nfcb"].to_list()))
dst = "Risky Debt of Nonfinancial Businesses"
panel = panel.with_columns(pl.col("quarter").replace_strict(m, default=None).alias("_new")
    ).with_columns(pl.when(pl.col("_new").is_not_null()).then(pl.col("_new")).otherwise(pl.col(dst)).alias(dst)
    ).drop("_new")

panel.write_csv(f"{FIX}/panel_quarterly_fixed.csv")
print("wrote", panel.shape)
# coverage report
for c in list(COLMAP.values()) + [dst, "Mortage Lending and FICO Scores (Change in Lending Standards)"]:
    s = panel.select([pl.col("quarter"), pl.col(c).alias("v")]).filter(pl.col("v").is_not_null())
    print(f"{c[:50]}: {s.height} obs, {s['quarter'][0]} -> {s['quarter'][-1]}")

#!/usr/bin/env python3
"""Y-9C panel 1990Q1-2026Q2 with Fix 1 (CET1 splice) and Fix 2 (total capital from Y-9C).
Polars only. Old format (csv) 1990Q1-2021Q1, NPW (^) 2021Q2-2026Q2.
Capital ratios restricted to HC-R reporters (non-missing BHCAP859 post-2014).

NPW files are caret-delimited with NO quoting convention: embedded double
quotes in free-text memo items are literal data. Reading them with a CSV
quote character makes the parser swallow carets/newlines inside a "..." span,
which corrupts the row structure -- this is what silently dropped 2023Q4
(the only quarter whose memo items contain a leading-quote field). All NPW
reads therefore pass quote_char=None.
"""
import re
from pathlib import Path

import polars as pl

Y9C_RAW = "/home/hatch/workspace/fedheat_r2_work/y9c_raw"
Y9C_NPW = "/home/hatch/workspace/fedheat_r2_work/y9c_npw"
Y9C_NEW = "/home/hatch/workspace/fedheat_r2_work/y9c_new"
OUT = str(Path(__file__).resolve().parent / "y9c_panel_full.csv")

def pq(s):
    m = re.match(r"(\d{4})Q([1-4])", s); return int(m.group(1)), int(m.group(2))
def qrange(a, b):
    y0,q0 = pq(a); y1,q1 = pq(b); y,q = y0,q0; out=[]
    while (y,q) <= (y1,q1):
        out.append(f"{y}Q{q}"); q+=1
        if q>4: q,y=1,y+1
    return out

ITEMS_OLD = ["bhck2170","bhck2122","bhck3217","bhck3163","bhck8274","bhck3283",
             "bhck3000","bhckc502","bhckb704","bhck8275","bhck2800","bhckb995",
             "bhckb996","bhck2332","bhdm6631","bhdm6636","bhfn6631","bhfn6636",
             "bhca8274","bhcap859","bhcaa223","bhcap863"]
ITEMS_NPW = ["bhck2170","bhck2122","bhck3217","bhck3163","bhckb995","bhckb996",
             "bhck2332","bhdm6631","bhdm6636","bhfn6631","bhfn6636",
             "bhca8274","bhcap859","bhcaa223","bhcap863"]
CAP_ITEMS = {"bhca8274","bhcap859","bhcaa223","bhcap863","bhck8274","bhck8275",
             "bhckb704","bhck3283","bhck3000","bhckc502"}

def to_num(s):
    return (pl.col(s).cast(pl.String).str.strip_chars()
            .replace("--------", None).replace("", None)
            .cast(pl.Float64, strict=False).fill_null(0))

def agg_old(q):
    y,qq = pq(q)
    import os
    fn = f"{Y9C_RAW}/bhcf{y%100:02d}{qq*3:02d}.csv"
    if not os.path.exists(fn):
        fn = f"{Y9C_NEW}/bhcf{y%100:02d}{qq*3:02d}.csv"
    try: hdr = pl.read_csv(fn, n_rows=0, encoding="utf8-lossy").columns
    except Exception: return None
    cmap = {c.lower(): c for c in hdr}
    if "rssd9001" not in cmap: return None
    use = [cmap[i] for i in ITEMS_OLD if i in cmap]
    try:
        d = pl.read_csv(fn, columns=[cmap["rssd9001"]]+use, encoding="utf8-lossy",
                        infer_schema_length=8000, truncate_ragged_lines=True)
    except Exception: return None
    d = d.filter(to_num(use[0]) > 0)  # has assets
    rec = {"quarter": q, "n_filers": float(d.height), "src": "old"}
    sums = {}
    for item in ITEMS_OLD:
        col = cmap.get(item)
        sums[item] = float(d.select(to_num(col).sum()).item()) if col in d.columns else float("nan")
    rec.update(sums)
    # HC-R reporter subset for capital ratios (post-2014)
    if "bhcap859" in cmap:
        dh = d.filter(pl.col(cmap["bhcap859"]).cast(pl.String).str.strip_chars() != "--------")
        for item in ["bhca8274","bhcap859","bhcaa223","bhcap863"]:
            col = cmap.get(item)
            rec[item+"_hcr"] = float(dh.select(to_num(col).sum()).item()) if col in dh.columns else float("nan")
        rec["n_hcr"] = float(dh.height)
    a = d.select(to_num(cmap["bhck2170"]).alias("a"))["a"] if "bhck2170" in cmap else None
    if a is not None:
        rec["top5_assets"] = float(a.top_k(5).sum()); rec["tot_assets"] = float(a.sum())
    return rec

def agg_npw(q):
    fn = f"{Y9C_NPW}/bhcf_npw_{q}.txt"
    try: hdr = pl.read_csv(fn, n_rows=0, separator="^", encoding="utf8-lossy",
                           truncate_ragged_lines=True, quote_char=None).columns
    except Exception: return None
    cmap = {c.lower(): c for c in hdr}
    use = [cmap[i] for i in ITEMS_NPW if i in cmap]
    try:
        d = pl.read_csv(fn, columns=[cmap["rssd9001"]]+use, separator="^",
                        encoding="utf8-lossy", infer_schema_length=8000,
                        truncate_ragged_lines=True, quote_char=None)
    except Exception: return None
    d = d.filter(to_num(use[0]) > 0)
    rec = {"quarter": q, "n_filers": float(d.height), "src": "npw"}
    for item in ITEMS_NPW:
        col = cmap.get(item)
        rec[item] = float(d.select(to_num(col).sum()).item()) if col in d.columns else float("nan")
    # HC-R reporters
    dh = d.filter(pl.col(cmap["bhcap859"]).cast(pl.String).str.strip_chars().is_not_null() &
                  (pl.col(cmap["bhcap859"]).cast(pl.String).str.strip_chars() != ""))
    for item in ["bhca8274","bhcap859","bhcaa223","bhcap863"]:
        col = cmap.get(item)
        rec[item+"_hcr"] = float(dh.select(to_num(col).sum()).item()) if col in dh.columns else float("nan")
    rec["n_hcr"] = float(dh.height)
    a = d.select(to_num(cmap["bhck2170"]).alias("a"))["a"] if "bhck2170" in cmap else None
    if a is not None:
        rec["top5_assets"] = float(a.top_k(5).sum()); rec["tot_assets"] = float(a.sum())
    return rec

rows = []
for q in qrange("1990Q1","2026Q2"):
    y,qq = pq(q)
    rec = agg_npw(q) if (y,qq) >= (2021,2) else agg_old(q)
    if rec is None and (y,qq) >= (2021,2): rec = agg_old(q)
    if rec: rows.append(rec); print(q, rec["src"], int(rec["n_filers"]), flush=True)
    else: print(q, "MISSING", flush=True)

df = pl.DataFrame(rows).sort("quarter")
# capital ratios: pre-2014 all filers (old concept), post-2014 HC-R reporters
df = df.with_columns([
    (100.0*(pl.col("bhck3217")-pl.col("bhck3163"))/(pl.col("bhck2170")-pl.col("bhck3163"))).alias("tangible"),
    (100.0*pl.col("bhck2122")/(pl.col("bhdm6631")+pl.col("bhdm6636")+pl.col("bhfn6631")+pl.col("bhfn6636"))).alias("loans_dep"),
    (100.0*pl.col("top5_assets")/pl.col("tot_assets")).alias("top5"),
    pl.when(pl.col("quarter") < "2014Q1")
      .then(100.0*(pl.col("bhck8274")-pl.col("bhck3283")-pl.col("bhck3000")-pl.col("bhckc502").fill_nan(0))/pl.col("bhckb704"))
      .otherwise(100.0*pl.col("bhcap859_hcr")/pl.col("bhcaa223_hcr")).alias("t1c_cet1"),
    pl.when(pl.col("quarter") < "2014Q1")
      .then(100.0*(pl.col("bhck8274")+pl.col("bhck8275"))/pl.col("bhckb704"))
      .otherwise(100.0*(pl.col("bhca8274_hcr")+pl.col("bhcap863_hcr"))/pl.col("bhcaa223_hcr")).alias("total_cap"),
    (100.0*(pl.when(pl.col("bhckb995").is_not_nan()).then(pl.col("bhckb995")+pl.col("bhckb996")).otherwise(pl.col("bhck2800"))+pl.col("bhck2332"))/pl.col("bhck2170")).alias("st_money"),
])
df.write_csv(OUT)
print("wrote", OUT, df.shape)
for q in ["2013Q4","2014Q1","2021Q1","2021Q2","2026Q1","2026Q2"]:
    r = df.filter(pl.col("quarter")==q).to_dicts()
    if r: print(q, {k: round(r[0][k],2) for k in ["tangible","loans_dep","top5","t1c_cet1","total_cap","st_money"]})

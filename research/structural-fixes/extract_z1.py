#!/usr/bin/env python3
"""Extract Z.1 series from the Fed bulk CSV (fixes dead FRED codes).
Maps old FRED BOGZ1 codes -> Z.1 mnemonics, extracts quarterly levels.
Polars only.
"""
import zipfile, csv, io, re
import polars as pl

Z1ZIP = "/home/hatch/workspace/frb-work/z1.zip"
OUT = "/home/hatch/workspace/frb-work/fix/z1_extracted.csv"

# mnemonic -> (fred_code, description)
MNEMONICS = {
    "FL794141005": ("BOGZ1FL794141005Q", "ST loans liabilities"),
    "FL793169175": ("BOGZ1FL793169175Q", "open market paper liabilities"),
    "FL793069175": ("BOGZ1FL793069175Q", "open market paper assets"),
    "FL634090005": ("BOGZ1FL634090005Q", "MMF total financial assets"),
    "FL793063005": ("BOGZ1FL793063005Q", "corp bonds asset"),
    "FL794035005": ("BOGZ1FL794035005Q", "loans asset"),
    "FL794090005": ("BOGZ1FL794090005Q", "total financial assets"),
    "FL104090103": ("BOGZ1FL104090103Q", "NFCB total assets"),
    # working codes also extracted for extension to 2026Q2
    "FL664090005": ("BOGZ1FL664090005Q", "BD total financial assets"),
    "FL664190005": ("BOGZ1FL664190005Q", "BD total liabilities"),
    "FL662051003": ("BOGZ1FL662051003Q", "BD repo assets"),
    "FL662151003": ("BOGZ1FL662151003Q", "BD repo liabilities"),
    "FL792050005": ("BOGZ1FL792050005Q", "fed funds repo assets"),
    "FL792150005": ("BOGZ1FL792150005Q", "fed funds repo liabilities"),
    "FL794190005": ("BOGZ1FL794190005Q", "financial sector liabilities"),
    "FA674190005": ("BOGZ1FA674190005Q", "non-agency securitization"),
}

def qlabel(date_str):
    # Z.1 dates like "2026-04-01" -> 2026Q2
    m = re.match(r"(\d{4})-(\d{2})", date_str)
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y}Q{(mo-1)//3+1}"

series = {m: {} for m in MNEMONICS}
with zipfile.ZipFile(Z1ZIP) as z:
    with z.open("Z1.csv") as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
        hdr = next(reader)
        si = hdr.index("series_name")
        # find date/value columns
        di = next(i for i,h in enumerate(hdr) if "date" in h.lower())
        vi = next(i for i,h in enumerate(hdr) if "value" in h.lower() and "unit" not in h.lower())
        print("date col:", hdr[di], "| value col:", hdr[vi])
        for row in reader:
            s = row[si]
            if not s.endswith(".Q"): continue
            core = s.split("/")[-1].replace(".Q", "")
            if core in series:
                try:
                    q = qlabel(row[di])
                    v = float(row[vi]) if row[vi].strip() not in ("", ".") else None
                    if v is not None:
                        series[core][q] = v
                except (ValueError, IndexError):
                    pass

# build frame
all_q = sorted({q for s in series.values() for q in s})
print("quarters:", all_q[0], "->", all_q[-1], f"({len(all_q)})")
df = pl.DataFrame({"quarter": all_q})
for m, vals in series.items():
    fred = MNEMONICS[m][0]
    df = df.with_columns(pl.Series(fred, [vals.get(q) for q in all_q], dtype=pl.Float64))
df.write_csv(OUT)
print("wrote", OUT, df.shape)
for m, (fred, desc) in MNEMONICS.items():
    n = df[fred].drop_nulls().len()
    print(f"  {fred}: {n} obs, last={df.filter(pl.col(fred).is_not_null())['quarter'][-1] if n else 'none'}")

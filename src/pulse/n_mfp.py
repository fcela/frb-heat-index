"""MMF repo counterparty concentration (top-3 dealer share).

Source: SEC DERA Form N-MFP monthly data sets
(https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets).
Requires an identifying User-Agent per SEC fair-access rules; requests are
paced (~2s apart). Each monthly ZIP (~11MB) holds TSVs; repo positions come
from NMFP_SCHPORTFOLIOSECURITIES (INVESTMENTCATEGORY contains "Repurchase"),
dated by modal REPORTDATE from NMFP_SUBMISSION.

Frequency: monthly. Unit: percent (top-3 dealers' repo / total MMF repo).

Methodology choices (documented):
- Counterparty names are normalized via a keyword map (dealer abbreviations
  like MS/BOA/MIZ/WFB/CAG/NAT/NOM plus full names; several JPMorgan
  spellings merged). Residual unmapped volume sits in an OTHER bucket that
  is EXCLUDED from the dealer ranking (it is a residual, not a dealer).
- Sponsored repo rows naming FICC plus a dealer ("FICC - <dealer>",
  "Fixed Income Clearing Corp. - <dealer>", "<dealer> - Fixed Income
  Clearing Corp.", "TRI-PARTY ...-<dealer>", "<abbr> FIG ... RP" tri-party
  trade codes) are attributed to the named sponsor dealer, on the grounds
  that the sponsor is the economic counterparty in the sponsored trade.
  Pure-FICC rows with no named dealer stay as "FICC (unsponsored)".
- Denominator is total MMF repo across all funds filing for the report
  month (filings restricted to the modal REPORTDATE).
"""

import io
import re
import subprocess
import time
import zipfile
from urllib.parse import urljoin

import polars as pl

DERA_URL = "https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets"
UA = "frb-heat-index research bot (financial-vulnerability-monitor; contact: research@example.com)"

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# (regex, canonical dealer). Order matters: specific before general.
DEALER_MAP = [
    (r"\bJ\.?P\.?\s*MORGAN\b|\bJPM(ORGAN)?\b", "JPMorgan"),
    (r"\bMORGAN\s*STANLEY\b|\bMS\b", "Morgan Stanley"),
    (r"\bCITI(GROUP|BANK|GR)?\b|\bCITIGR\w*|\bCITG\b", "Citi"),
    (r"\bWELLS\s*FARGO\b|\bWFB\b|\bWF\b", "Wells Fargo"),
    (r"\bBOFA\b|\bBOA\b|\bBANK\s+OF\s+AMERICA\b|\bBANK\s*OF\s*AMER\b", "Bank of America"),
    (r"\bGOLDMAN\s*SACHS\b|\bGS\b", "Goldman Sachs"),
    (r"\bBANK\s+OF\s+NEW\s+YORK(\s+MELLON)?\b|\bBNY\s?M\b|\bBNY\b|\bBONY\b", "BNY Mellon"),
    (r"\bSTATE\s*STREET\b|\bSSB\b|\bSSBT\b", "State Street"),
    (r"\bBARCLAYS\b|\bBARC\w*", "Barclays"),
    (r"\bNORTHERN\s*TRUST\b", "Northern Trust"),
    (r"\bMIZUHO\b|\bMIZ\b|\bNOMU\b", "Mizuho"),
    (r"\bBNP(\s*PARIBAS)?\b", "BNP Paribas"),
    (r"\bUBS\b", "UBS"),
    (r"\bCREDIT\s*AGRICOLE\b|\bCREDITAG\b|\bCAG\b", "Credit Agricole"),
    (r"\bHSBC\b", "HSBC"),
    (r"\bNATIXIS\b|\bNAT\b", "Natixis"),
    (r"\bNOMURA\b|\bNOM\b", "Nomura"),
    (r"\bDEUTSCHE\s*BANK\b|\bDB\b", "Deutsche Bank"),
    (r"\bCIBC\b|\bCIB\b|\bCANADIAN\s*IMPERIAL\b", "CIBC"),
    (r"\bSTANDARD\s*CHARTERED\b", "Standard Chartered"),
    (r"\bROYAL\s*BANK\s*OF\s*CANADA\b|\bRBC\w*", "RBC"),
    (r"\bBANK\s*OF\s*MONTREAL\b|\bBMO\b", "Bank of Montreal"),
    (r"\bBBVA\b|\bBANCO\s*BILBAO\b", "BBVA"),
    (r"\bU\.?S\.?\s*BANK\b|\bUSB\b", "US Bank"),
    (r"\bSCOTIA(BANK)?\b|\bBANK\s*OF\s*NOVA\s*SCOTIA\b", "Scotiabank"),
    (r"\bMUFG\b", "MUFG"),
    (r"\bING\b", "ING"),
    (r"\bSOCIETE\s*GENERALE\b|\bSOCGEN\b", "Societe Generale"),
    (r"\bSUMITOMO\b|\bSMBC\b", "SMBC"),
    (r"\bSANTANDER\b", "Santander"),
    (r"\bTD\s*(SECURITIES|BANK)?\b", "TD"),
    (r"\bLLOYDS\b", "Lloyds"),
    (r"\bNATWEST\b|\bNTW\b|\bRBS\b", "NatWest"),
    (r"\bCREDIT\s*SUISSE\b", "Credit Suisse"),
    (r"\bDANSKE\b", "Danske"),
    (r"\bNORDEA\b", "Nordea"),
    (r"\bUNICREDIT\b", "UniCredit"),
    (r"\bINTESA\b", "Intesa"),
    (r"\bRABOBANK\b", "Rabobank"),
    (r"\bABN\s*AMRO\b", "ABN Amro"),
]


def normalize_counterparty(name: str) -> str:
    """Map a raw TITLEOFISSUER to a canonical dealer (or OTHER/FICC buckets)."""
    u = (name or "").upper()
    u = re.sub(r"\s+\d+\.\d+%.*$", "", u)   # trailing coupon " 3.890000%"
    u = re.sub(r"\s+SOFR.*$", "", u)          # " SOFR+0.02% ..."
    u = re.sub(r"\s+\d+/\d+/\d+.*$", "", u)   # trailing settlement date
    u = u.strip()
    for pat, canon in DEALER_MAP:
        if re.search(pat, u):
            return canon
    if re.search(r"\bFICC\b|FIXED\s*INCOME\s*CLEAR", u):
        return "FICC (unsponsored)"
    return "OTHER"


def _get(url: str, timeout: int = 300) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or len(r.stdout) < 1000:
        raise RuntimeError(f"N-MFP fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def discover_archives() -> list[tuple[str, str]]:
    """List (start_yyyymmdd, url) of monthly N-MFP archives, oldest first."""
    html = _get(DERA_URL, timeout=60).decode("utf-8", errors="replace")
    found = re.findall(r'href="([^"]*(\d{8})-(\d{8})_nmfp\.zip)"', html)
    if not found:
        raise RuntimeError("N-MFP: no monthly archives found on DERA page")
    archives = sorted({(m[1], urljoin(DERA_URL, m[0])) for m in found})
    return archives


def _parse_reportdate(s: str) -> str:
    # "30-SEP-2026" -> "2026-09-30"
    d, mon, y = s.strip().split("-")
    return f"{int(y):04d}-{_MONTHS[mon.upper()]:02d}-{int(d):02d}"


def concentration_for_zip(zip_bytes: bytes) -> tuple[str, float, float]:
    """Return (report_date, top3_share_pct, total_repo_usd) for one archive."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        sched = next(n for n in names if "SCHPORTFOLIOSECURITIES" in n)
        subm = next(n for n in names if "SUBMISSION" in n)
        df = pl.read_csv(
            z.open(sched), separator="\t", infer_schema_length=10000,
            schema_overrides={"COUPON": pl.String},
        )
        sub = pl.read_csv(z.open(subm), separator="\t", infer_schema_length=10000)
    mode_rd = (
        sub.group_by("REPORTDATE").len().sort("len", descending=True)["REPORTDATE"][0]
    )
    report_date = _parse_reportdate(mode_rd)
    keep = sub.filter(pl.col("REPORTDATE") == mode_rd)["ACCESSION_NUMBER"].unique()
    val = "INCLUDINGVALUEOFANYSPONSORSUPP"
    repo = df.filter(pl.col("INVESTMENTCATEGORY").str.contains("Repurchase"))
    repo = repo.with_columns(
        pl.col(val).str.strip_chars().cast(pl.Float64, strict=False).alias("v")
    )
    repo = repo.filter(pl.col("v").is_not_null() & (pl.col("v") > 0))
    repo = repo.filter(pl.col("ACCESSION_NUMBER").is_in(keep.implode()))
    total = repo["v"].sum()
    if not total or total <= 0:
        raise RuntimeError(f"N-MFP: no repo volume for {report_date}")
    shares = (
        repo.with_columns(
            pl.col("TITLEOFISSUER")
            .map_elements(normalize_counterparty, return_dtype=pl.String)
            .alias("dealer")
        )
        .group_by("dealer")
        .agg(pl.col("v").sum().alias("vv"))
        .sort("vv", descending=True)
    )
    # Rank real dealers only: OTHER is a residual, not a dealer.
    ranked = shares.filter(
        ~pl.col("dealer").is_in(["OTHER", "FICC (unsponsored)"])
    ).head(3)
    if ranked.height < 3:
        raise RuntimeError(f"N-MFP: fewer than 3 dealers for {report_date}")
    top3 = ranked["vv"].sum() / total * 100.0
    if not (0.0 < top3 <= 100.0) or top3 != top3:
        raise RuntimeError(f"N-MFP: bad concentration {top3} for {report_date}")
    return report_date, float(top3), float(total)


def fetch_concentration(months: int = 12, pace: float = 2.0) -> pl.DataFrame:
    """Fetch monthly top-3 dealer share of MMF repo for the last `months`.

    Returns date (REPORTDATE month-end) / value (percent).
    """
    archives = discover_archives()
    if len(archives) < months:
        raise RuntimeError(
            f"N-MFP: only {len(archives)} monthly archives found, need {months}"
        )
    recs = []
    for start, url in archives[-months:]:
        time.sleep(pace)  # SEC fair-access pacing
        report_date, top3, total = concentration_for_zip(_get(url))
        recs.append((report_date, top3))
    if not recs:
        raise RuntimeError("N-MFP: no monthly observations")
    df = pl.DataFrame({"date": [d for d, _ in recs], "value": [v for _, v in recs]})
    df = df.with_columns(
        pl.col("date").str.to_date(), pl.col("value").cast(pl.Float64)
    ).sort("date")
    return df.select("date", "value")

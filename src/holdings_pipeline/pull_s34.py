"""Layer 1: pull Thomson Reuters 13F holdings (S34) and supporting CRSP tables.

Downloads the source tables as delivered, without filters, merges, or new
columns (design/01_data_ingestion.md). The only restrictions are the report
date and the requested columns. All processing happens in clean_s34.py, which
reads these tables only through the load_ functions below.

For each report date (rdate), in DATA_DIR/raw/s34/<YYYYQn>/:
    s34type1.parquet  every vintage (fdate) of every manager report with this
                      rdate: mgrno, mgrname, typecode, fdate, rdate
    s34type3.parquet  the holdings of those report vintages: mgrno, fdate,
                      cusip, shares
    msf_v2.parquet    CRSP monthly prices, cumulative split factors, and
                      primary exchanges for the rdate month and every fdate
                      month of those reports
Once for all quarters:
    DATA_DIR/raw/crsp/stocknames_v2.parquet  CRSP security names, CUSIPs,
                      and security attributes with their date ranges

TR carries reports forward for several quarters, so a manager-rdate pair can
appear under several fdates; keeping all of them here lets the cleaning step
choose the vintage.

Usage (from the project root). The default is the 2022Q4 pilot:

    uv run python src/holdings_pipeline/pull_s34.py

Full history:

    uv run python src/holdings_pipeline/pull_s34.py \\
        --S34_START=2005-03-31 --S34_END=2025-12-31

Then run clean_s34.py with the same dates. S34_SKIP_EXISTING=True skips
quarters already downloaded, which is safe because raw files do not depend on
any cleaning choice. CRSP currently ends in 2025-12.
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import wrds

from settings import config

DATA_DIR = Path(config("DATA_DIR"))
S34_START = config("S34_START", default="2022-12-31", cast=str)
S34_END = config("S34_END", default="2022-12-31", cast=str)
S34_SKIP_EXISTING = config(
    "S34_SKIP_EXISTING", default="False", cast=lambda s: str(s).lower() == "true"
)
RAW_DIR = DATA_DIR / "raw" / "s34"
NAMES_PATH = DATA_DIR / "raw" / "crsp" / "stocknames_v2.parquet"
RAW_TABLES = ("s34type1", "s34type3", "msf_v2")


def raw_quarter_dir(rdate, raw_dir=RAW_DIR):
    """Raw folder for one report date, e.g. .../raw/s34/2022Q4."""
    return Path(raw_dir) / str(pd.Period(rdate, freq="Q"))


def raw_file(rdate, table, raw_dir=RAW_DIR):
    """Path of one raw table: s34type1, s34type3, or msf_v2."""
    if table not in RAW_TABLES:
        raise ValueError(f"Unknown raw S34 table: {table}")
    return raw_quarter_dir(rdate, raw_dir) / f"{table}.parquet"


def list_quarters(db, start, end):
    """Report dates in TR S34 between start and end (inclusive), as 'YYYY-MM-DD'."""
    q = db.raw_sql(
        f"""
        SELECT DISTINCT rdate FROM tr_13f.s34type1
        WHERE rdate BETWEEN '{start}' AND '{end}'
        ORDER BY rdate
        """,
        date_cols=["rdate"],
    )
    return q["rdate"].dt.strftime("%Y-%m-%d").tolist()


def pull_names(db):
    """CRSP stock names: one row per security and name-history interval."""
    return db.raw_sql(
        """
        SELECT permno, permco, cusip, hdrcusip, namedt, nameenddt, sharetype,
               securitytype, securitysubtype, usincflg, issuertype
        FROM crsp.stocknames_v2
        """,
        date_cols=["namedt", "nameenddt"],
    )


def pull_quarter(db, rdate):
    """Raw S34 and CRSP tables for one report date."""
    type1 = db.raw_sql(
        f"""
        SELECT mgrno, mgrname, typecode, fdate, rdate
        FROM tr_13f.s34type1 WHERE rdate = '{rdate}'
        """,
        date_cols=["fdate", "rdate"],
    )
    # The join only restricts type3 to the report vintages of this rdate
    type3 = db.raw_sql(
        f"""
        SELECT h.mgrno, h.fdate, h.cusip, h.shares
        FROM tr_13f.s34type3 AS h
        INNER JOIN (
            SELECT DISTINCT mgrno, fdate
            FROM tr_13f.s34type1 WHERE rdate = '{rdate}'
        ) AS m ON h.mgrno = m.mgrno AND h.fdate = m.fdate
        """,
        date_cols=["fdate"],
    )
    months = {pd.Timestamp(rdate).strftime("%Y%m")}
    months |= set(type1["fdate"].dt.strftime("%Y%m"))
    msf = db.raw_sql(
        f"""
        SELECT permno, yyyymm, mthprc, mthcumfacshr, primaryexch
        FROM crsp.msf_v2 WHERE yyyymm IN ({", ".join(sorted(months))})
        """
    )
    return {"s34type1": type1, "s34type3": type3, "msf_v2": msf}


def pull_s34(db, quarters, raw_dir=RAW_DIR, skip_existing=False):
    """Download and save the raw tables for each report date."""
    for q in quarters:
        if skip_existing and all(raw_file(q, t, raw_dir).exists() for t in RAW_TABLES):
            continue
        tables = pull_quarter(db, q)
        raw_quarter_dir(q, raw_dir).mkdir(parents=True, exist_ok=True)
        for name, frame in tables.items():
            frame.to_parquet(raw_file(q, name, raw_dir), index=False)
        print(q, {name: len(frame) for name, frame in tables.items()})


def load_raw_s34(rdate, table, data_dir=DATA_DIR):
    """Load one raw table for one report date."""
    return pd.read_parquet(raw_file(rdate, table, Path(data_dir) / "raw" / "s34"))


def load_crsp_names(data_dir=DATA_DIR):
    return pd.read_parquet(Path(data_dir) / "raw" / "crsp" / "stocknames_v2.parquet")


if __name__ == "__main__":
    db = wrds.Connection(wrds_username=config("WRDS_USERNAME"))

    NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    pull_names(db).to_parquet(NAMES_PATH, index=False)

    quarters = list_quarters(db, S34_START, S34_END)
    pull_s34(db, quarters, skip_existing=S34_SKIP_EXISTING)
    db.close()

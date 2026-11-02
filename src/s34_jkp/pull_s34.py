"""Pull and clean Thomson Reuters 13F institutional holdings (S34) from WRDS.

For each report date (rdate), this script:

1. Keeps the first vintage (earliest fdate) of each manager's report.
   TR carries reports forward for several quarters, so a manager-rdate
   pair can appear under several fdates.
2. Links TR CUSIPs to CRSP PERMNOs in two passes:
   a. the CUSIP that is valid in CRSP on rdate;
   b. for CUSIPs that are still unmatched, any historical CUSIP of a CRSP
      security that is active on rdate. From 2011, and heavily from 2017Q4,
      TR records some holdings under a CUSIP the security only received
      later (e.g. GE in 2017Q4 uses its post-2021 CUSIP 36960430).
   Security type and PERMCO are always taken from the CRSP record valid
   on rdate.
3. Keeps CRSP common stocks: securitytype EQTY, securitysubtype COM,
   sharetype NS (excludes ADRs), issuertype CORP or ACOR (excludes REITs).
   Foreign-incorporated firms are kept, matching JKP (legacy share codes
   10, 11, 12).
4. Adjusts shares for splits between rdate and fdate with CRSP cumulative
   factors, and values holdings at CRSP month-end prices on rdate.
5. Aggregates PERMNO to PERMCO.

Outputs (in DATA_DIR/s34/), one file per quarter:
    holdings_<rdate>.pkl   quarter, investor_id, permco, shares, dollar_holding (USD)
    investors_<rdate>.pkl  quarter, investor_id, source, source_id, name,
                           investor_type, n_holdings, aum (USD)
    log_<rdate>.pkl        quarter, step, n_obs, n_investors, n_firms
Plus DATA_DIR/crsp_cusip_map.pkl and DATA_DIR/filter_log_s34.pkl.

Note: investor_type is TR's typecode ("13f_typecode_<n>"). It does not
identify hedge funds, and S34 overlaps with S12 fund families (e.g.
Vanguard), so S34 must not be merged with S12 as is.

Usage (from the project root). The default is the 2022Q4 pilot:

    uv run python src/s34_jkp/pull_s34.py

Full history (about 15-20 minutes):

    uv run python src/s34_jkp/pull_s34.py --S34_START=2005-03-31 --S34_END=2025-12-31

CRSP currently ends in 2025-12, so quarters after 2025Q4 cannot be linked.
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import wrds

from settings import config

DATA_DIR = Path(config("DATA_DIR"))
WRDS_USERNAME = config("WRDS_USERNAME")
S34_START = config("S34_START", default="2022-12-31", cast=str)
S34_END = config("S34_END", default="2022-12-31", cast=str)
S34_SKIP_EXISTING = config(
    "S34_SKIP_EXISTING", default="False", cast=lambda s: str(s).lower() == "true"
)
S34_DIR = DATA_DIR / "s34"


def pull_cusip_map(db):
    """All (cusip, permno) pairs in CRSP, using both historical and header CUSIPs."""
    cm = db.raw_sql("SELECT DISTINCT permno, cusip, hdrcusip FROM crsp.stocknames_v2")
    cusip_map = pd.concat(
        [
            cm[["cusip", "permno"]],
            cm[["hdrcusip", "permno"]].rename(columns={"hdrcusip": "cusip"}),
        ]
    )
    return cusip_map.dropna().drop_duplicates().reset_index(drop=True)


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


def build_s34_quarter(db, rdate, cusip_map):
    """Build S34 holdings and investors for one report date.

    Returns (holdings, investors, log, adj), where adj lists the holdings
    whose shares were split-adjusted (for checking).
    """
    rd = pd.Timestamp(rdate)
    ym_r = int(rd.strftime("%Y%m"))
    log = []

    def add(step, df):
        log.append(
            {
                "quarter": rd,
                "step": step,
                "n_obs": len(df),
                "n_investors": df["mgrno"].nunique(),
                "n_firms": df["permco"].nunique() if "permco" in df else np.nan,
            }
        )

    # 1. Manager info: first vintage per manager
    mgr = db.raw_sql(
        f"""
        SELECT mgrno, mgrname, typecode, fdate
        FROM tr_13f.s34type1 WHERE rdate = '{rdate}'
        """,
        date_cols=["fdate"],
    )
    mgr = mgr.sort_values("fdate").drop_duplicates("mgrno")

    # 2. Holdings from the first vintage only
    h = db.raw_sql(
        f"""
        WITH m AS (
            SELECT mgrno, MIN(fdate) AS fdate
            FROM tr_13f.s34type1 WHERE rdate = '{rdate}'
            GROUP BY mgrno
        )
        SELECT h.mgrno, h.fdate, h.cusip, h.shares
        FROM tr_13f.s34type3 AS h
        INNER JOIN m ON h.mgrno = m.mgrno AND h.fdate = m.fdate
        WHERE h.shares > 0
        """,
        date_cols=["fdate"],
    )
    add("1_download", h)

    # 3. CUSIP -> PERMNO: CUSIP valid on rdate first, then historical CUSIPs
    names = db.raw_sql(
        f"""
        SELECT permno, permco, cusip, sharetype, securitytype,
               securitysubtype, usincflg, issuertype
        FROM crsp.stocknames_v2
        WHERE namedt <= '{rdate}' AND nameenddt >= '{rdate}'
        """
    )
    active = set(names["permno"])

    direct = names[["cusip", "permno"]].drop_duplicates("cusip")
    h = h.merge(direct, on="cusip", how="left")
    add("2a_match_direct", h[h["permno"].notna()])

    fb = cusip_map[cusip_map["permno"].isin(active)]
    fb = fb[~fb["cusip"].duplicated(keep=False)]  # drop ambiguous CUSIPs
    miss = h["permno"].isna()
    h.loc[miss, "permno"] = h.loc[miss, "cusip"].map(fb.set_index("cusip")["permno"])
    h = h[h["permno"].notna()].copy()
    h["permno"] = h["permno"].astype(int)

    # Security type and PERMCO from the record valid on rdate
    attr = names.drop(columns="cusip").drop_duplicates("permno")
    h = h.merge(attr, on="permno", how="inner")
    add("2_match_cusip", h)

    h = h[
        (h["securitytype"] == "EQTY")
        & (h["securitysubtype"] == "COM")
        & (h["sharetype"] == "NS")
        & (h["issuertype"].isin(["CORP", "ACOR"]))
    ]
    add("3_common_stock", h)

    # 4. Prices on rdate and split adjustment from fdate to rdate
    h["ym_f"] = h["fdate"].dt.strftime("%Y%m").astype(int)
    yms = sorted(set(h["ym_f"]) | {ym_r})
    msf = db.raw_sql(
        f"""
        SELECT permno, yyyymm, mthprc, mthcumfacshr
        FROM crsp.msf_v2 WHERE yyyymm IN ({", ".join(map(str, yms))})
        """
    )
    r = msf[msf["yyyymm"] == ym_r][["permno", "mthprc", "mthcumfacshr"]].rename(
        columns={"mthcumfacshr": "fac_r"}
    )
    f = msf[["permno", "yyyymm", "mthcumfacshr"]].rename(
        columns={"yyyymm": "ym_f", "mthcumfacshr": "fac_f"}
    )
    h = h.merge(r, on="permno", how="left").merge(f, on=["permno", "ym_f"], how="left")

    ratio = (h["fac_f"] / h["fac_r"]).where(h["ym_f"] != ym_r, 1.0)
    h["shares_adj"] = h["shares"] * ratio
    h["dollar_holding"] = h["shares_adj"] * h["mthprc"]
    adj = h[ratio.ne(1) & ratio.notna()][
        ["mgrno", "permno", "fdate", "shares", "shares_adj", "fac_f", "fac_r"]
    ]
    h = h[h["dollar_holding"] > 0]
    add("4_price", h)

    # 5. Aggregate PERMNO -> PERMCO
    hold = h.groupby(["mgrno", "permco"], as_index=False).agg(
        shares=("shares_adj", "sum"), dollar_holding=("dollar_holding", "sum")
    )
    add("5_aggregate_permco", hold)

    hold["quarter"] = rd
    hold["investor_id"] = "s34_" + hold["mgrno"].astype(int).astype(str)
    hold["permco"] = hold["permco"].astype(int)

    # 6. Investors
    inv = hold.groupby(["quarter", "investor_id", "mgrno"], as_index=False).agg(
        n_holdings=("permco", "nunique"), aum=("dollar_holding", "sum")
    )
    inv = inv.merge(mgr[["mgrno", "mgrname", "typecode"]], on="mgrno", how="left")
    inv["source"] = "s34"
    inv["source_id"] = inv["mgrno"].astype(int)
    inv["name"] = inv["mgrname"]
    inv["investor_type"] = inv["typecode"].map(
        lambda x: f"13f_typecode_{int(x)}" if pd.notna(x) else "13f_typecode_missing"
    )

    hold = hold[["quarter", "investor_id", "permco", "shares", "dollar_holding"]]
    inv = inv[
        [
            "quarter", "investor_id", "source", "source_id", "name",
            "investor_type", "n_holdings", "aum",
        ]
    ]
    return hold, inv, pd.DataFrame(log), adj


def pull_s34(db, quarters, cusip_map, out_dir=S34_DIR, skip_existing=False):
    """Build and save S34 files for each quarter."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for q in quarters:
        path_h = out_dir / f"holdings_{q}.pkl"
        if skip_existing and path_h.exists():
            continue
        hold_q, inv_q, log_q, adj_q = build_s34_quarter(db, q, cusip_map)
        hold_q.to_pickle(path_h)
        inv_q.to_pickle(out_dir / f"investors_{q}.pkl")
        log_q.to_pickle(out_dir / f"log_{q}.pkl")
        print(q, len(hold_q), len(inv_q), len(adj_q))


def load_s34(kind="holdings", data_dir=DATA_DIR):
    """Load saved S34 files. kind is 'holdings', 'investors', or 'log'."""
    files = sorted((Path(data_dir) / "s34").glob(f"{kind}_*.pkl"))
    if not files:
        raise FileNotFoundError(f"No S34 {kind} files in {Path(data_dir) / 's34'}")
    return pd.concat([pd.read_pickle(f) for f in files], ignore_index=True)


def load_cusip_map(data_dir=DATA_DIR):
    return pd.read_pickle(Path(data_dir) / "crsp_cusip_map.pkl")


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = wrds.Connection(wrds_username=WRDS_USERNAME)

    cusip_map = pull_cusip_map(db)
    cusip_map.to_pickle(DATA_DIR / "crsp_cusip_map.pkl")

    quarters = list_quarters(db, S34_START, S34_END)
    pull_s34(db, quarters, cusip_map, skip_existing=S34_SKIP_EXISTING)

    load_s34("log").to_pickle(DATA_DIR / "filter_log_s34.pkl")
    db.close()

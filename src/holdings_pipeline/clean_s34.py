"""Layer 2: clean Thomson Reuters 13F holdings (S34).

Reads only the raw tables saved by pull_s34.py, through its load_ functions,
and needs no WRDS connection. For each report date (rdate):

1. Keeps the first vintage (earliest fdate) of each manager's report and its
   positions with positive shares. TR carries reports forward for several
   quarters, so a manager-rdate pair can appear under several fdates.
2. Links TR CUSIPs to CRSP PERMNOs using a unique, date-valid CUSIP on rdate.
   An optional sensitivity run also tries any historical CUSIP of a CRSP
      security that is active on rdate. From 2011, and heavily from 2017Q4,
      TR records some holdings under a CUSIP the security only received
      later (e.g. GE in 2017Q4 uses its post-2021 CUSIP 36960430).
   Security type and PERMCO are always taken from the CRSP record valid
   on rdate. Two vendor CUSIPs that point one manager at one security are
   quarantined (step 2b) rather than summed.
3. Keeps exchange-listed CRSP common stocks: securitytype EQTY, securitysubtype
   COM, sharetype NS (excludes ADRs), issuertype CORP or ACOR (excludes REITs).
   Foreign-incorporated firms are kept, matching JKP (legacy share codes
   10, 11, 12).
4. Adjusts shares for splits between rdate and fdate with CRSP cumulative
   factors, and values holdings at absolute CRSP month-end prices on rdate.
   Holdings with a missing price or split factor are dropped (step 4_price).
5. Aggregates PERMNO to PERMCO.

Outputs are Parquet files (see MERGE_NOTES.md, section 7).
In DATA_DIR/clean/s34/<YYYYQn>/, one folder per quarter:
    holdings_s34.parquet           quarter, investor_id, permco, shares,
                                   dollar_holding (USD)
    investors_s34.parquet          quarter, investor_id, source, source_id, name,
                                   investor_type, n_holdings, aum (USD)
    holdings_s34_security.parquet  accepted security-level links
    filter_log_s34.parquet         quarter, step, n_obs, n_investors, n_firms
Plus DATA_DIR/clean/s34/filter_log_s34.parquet (all quarters). Use
quarter_file() to locate them.

Usage (from the project root), after pull_s34.py with the same dates. The
default is the 2022Q4 pilot:

    uv run python src/holdings_pipeline/clean_s34.py

Full history:

    uv run python src/holdings_pipeline/clean_s34.py \\
        --S34_START=2005-03-31 --S34_END=2025-12-31
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from holdings_pipeline.pull_s34 import RAW_DIR, load_crsp_names, load_raw_s34
from settings import config

DATA_DIR = Path(config("DATA_DIR"))
S34_START = config("S34_START", default="2022-12-31", cast=str)
S34_END = config("S34_END", default="2022-12-31", cast=str)
S34_HISTORICAL_FALLBACK = config(
    "S34_HISTORICAL_FALLBACK", default="False", cast=lambda s: str(s).lower() == "true"
)
S34_DIR = DATA_DIR / "clean" / "s34"
LOG_PATH = S34_DIR / "filter_log_s34.parquet"
FILES = {
    "holdings": "holdings_s34",
    "investors": "investors_s34",
    "security": "holdings_s34_security",
    "log": "filter_log_s34",
}


def quarter_dir(rdate, out_dir=S34_DIR):
    """Folder for one quarter, named like S12's: e.g. .../2022Q4."""
    return Path(out_dir) / str(pd.Period(rdate, freq="Q"))


def quarter_file(rdate, kind="holdings", out_dir=S34_DIR):
    """Path of one saved table: kind is holdings, investors, security, or log."""
    return quarter_dir(rdate, out_dir) / f"{FILES[kind]}.parquet"


def cusip_map_from_names(names):
    """All (cusip, permno) pairs in CRSP, using both historical and header CUSIPs."""
    cusip_map = pd.concat(
        [
            names[["cusip", "permno"]],
            names[["hdrcusip", "permno"]].rename(columns={"hdrcusip": "cusip"}),
        ]
    )
    return cusip_map.dropna().drop_duplicates().reset_index(drop=True)


def build_s34_quarter(
    raw, rdate, cusip_map=None, historical_fallback=False, include_security=False
):
    """Build S34 holdings and investors for one report date from raw tables.

    raw is a dict with the raw tables s34type1, s34type3, and msf_v2 for this
    rdate and the CRSP names table under "names". cusip_map defaults to all
    CUSIPs in the names table.

    Returns (holdings, investors, log, adj), where adj lists split-adjusted
    positions. With include_security, also returns matched security-level rows.
    """
    rd = pd.Timestamp(rdate)
    ym_r = int(rd.strftime("%Y%m"))
    if cusip_map is None:
        cusip_map = cusip_map_from_names(raw["names"])
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

    # 1. First vintage of each manager's report, positions with positive shares
    type1 = raw["s34type1"]
    type1 = type1[pd.to_datetime(type1["rdate"]) == rd]
    mgr = type1.sort_values("fdate").drop_duplicates("mgrno")
    first = mgr[["mgrno", "fdate"]]
    h = raw["s34type3"].merge(first, on=["mgrno", "fdate"], how="inner")
    h = h[h["shares"] > 0].copy()
    add("1_download", h)

    # 2. CUSIP -> PERMNO: require a unique link valid on the report date.
    h["cusip"] = h["cusip"].astype("string").str.strip().str.upper()
    names = raw["names"]
    names = names.loc[
        (names["namedt"] <= rd) & (names["nameenddt"] >= rd),
        [
            "permno",
            "permco",
            "cusip",
            "sharetype",
            "securitytype",
            "securitysubtype",
            "usincflg",
            "issuertype",
        ],
    ].copy()
    names["cusip"] = names["cusip"].astype("string").str.strip().str.upper()
    active = set(names["permno"])

    direct = names[["cusip", "permno"]].drop_duplicates()
    ambiguous_direct = direct.loc[direct.duplicated("cusip", keep=False), "cusip"]
    direct = direct.loc[~direct.cusip.isin(ambiguous_direct)]
    h = h.merge(direct, on="cusip", how="left")
    h["link_method"] = "date_valid"
    add("2a_match_direct", h[h["permno"].notna()])

    if historical_fallback:
        fb = cusip_map[cusip_map["permno"].isin(active)].copy()
        fb["cusip"] = fb["cusip"].astype("string").str.strip().str.upper()
        fb = fb.drop_duplicates()
        fb = fb[~fb["cusip"].duplicated(keep=False)]
        miss = h["permno"].isna() & ~h["cusip"].isin(ambiguous_direct)
        h.loc[miss, "permno"] = h.loc[miss, "cusip"].map(
            fb.set_index("cusip")["permno"]
        )
        h.loc[miss & h["permno"].notna(), "link_method"] = "historical_fallback"
    h = h[h["permno"].notna()].copy()
    h["permno"] = h["permno"].astype(int)

    # Security type and PERMCO from the record valid on rdate
    attr = names.drop(columns="cusip").drop_duplicates()
    if attr.duplicated("permno").any():
        raise ValueError("CRSP has conflicting active security attributes on rdate")
    h = h.merge(attr, on="permno", how="inner")
    add("2_match_cusip", h)

    # Two vendor CUSIPs pointing at one manager/security may be aliases.
    # Quarantine both positions instead of summing a possibly duplicated holding.
    aliases = h.duplicated(["mgrno", "permno"], keep=False)
    h = h.loc[~aliases].copy()
    add("2b_aliases_quarantined", h)

    h = h[
        (h["securitytype"] == "EQTY")
        & (h["securitysubtype"] == "COM")
        & (h["sharetype"] == "NS")
        & (h["issuertype"].isin(["CORP", "ACOR"]))
    ]
    add("3_common_stock", h)

    # 4. Prices on rdate and split adjustment from fdate to rdate
    h["ym_f"] = h["fdate"].dt.strftime("%Y%m").astype(int)
    msf = raw["msf_v2"]
    r = msf[msf["yyyymm"] == ym_r][
        ["permno", "mthprc", "mthcumfacshr", "primaryexch"]
    ].rename(columns={"mthcumfacshr": "fac_r"})
    f = msf[["permno", "yyyymm", "mthcumfacshr"]].rename(
        columns={"yyyymm": "ym_f", "mthcumfacshr": "fac_f"}
    )
    h = h.merge(r, on="permno", how="left").merge(f, on=["permno", "ym_f"], how="left")

    ratio = (h["fac_f"] / h["fac_r"]).where(h["ym_f"] != ym_r, 1.0)
    h["shares_adj"] = h["shares"] * ratio
    h["dollar_holding"] = h["shares_adj"] * h["mthprc"].abs()
    adj = h[ratio.ne(1) & ratio.notna()][
        ["mgrno", "permno", "fdate", "shares", "shares_adj", "fac_f", "fac_r"]
    ]
    h = h[h["dollar_holding"].gt(0) & h["primaryexch"].isin(["N", "A", "Q"])]
    add("4_price", h)

    security = h[
        [
            "mgrno",
            "fdate",
            "cusip",
            "permno",
            "permco",
            "shares_adj",
            "dollar_holding",
            "link_method",
        ]
    ].copy()
    security["quarter"] = rd
    security["rdate"] = rd
    security["investor_id"] = "s34_" + security.mgrno.astype(int).astype(str)

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
            "quarter",
            "investor_id",
            "source",
            "source_id",
            "name",
            "investor_type",
            "n_holdings",
            "aum",
        ]
    ]
    result = (hold, inv, pd.DataFrame(log), adj)
    return (*result, security) if include_security else result


def raw_quarters(start, end, data_dir=DATA_DIR):
    """Report dates with raw files between start and end, as 'YYYY-MM-DD'."""
    folders = (Path(data_dir) / "raw" / "s34").glob("*/s34type1.parquet")
    dates = sorted(
        pd.Period(p.parent.name, freq="Q").end_time.normalize() for p in folders
    )
    return [
        f"{d:%Y-%m-%d}" for d in dates if pd.Timestamp(start) <= d <= pd.Timestamp(end)
    ]


def clean_s34(quarters, historical_fallback=False, out_dir=S34_DIR, data_dir=DATA_DIR):
    """Clean the raw tables of each quarter and save the S34 files."""
    names = load_crsp_names(data_dir)
    cusip_map = cusip_map_from_names(names)
    for q in quarters:
        raw = {
            t: load_raw_s34(q, t, data_dir) for t in ("s34type1", "s34type3", "msf_v2")
        }
        raw["names"] = names
        hold_q, inv_q, log_q, adj_q, security_q = build_s34_quarter(
            raw,
            q,
            cusip_map,
            historical_fallback=historical_fallback,
            include_security=True,
        )
        quarter_dir(q, out_dir).mkdir(parents=True, exist_ok=True)
        tables = {
            "holdings": hold_q,
            "investors": inv_q,
            "security": security_q,
            "log": log_q,
        }
        for kind, frame in tables.items():
            frame.to_parquet(quarter_file(q, kind, out_dir), index=False)
        print(q, len(hold_q), len(inv_q), len(adj_q))


def load_s34(kind="holdings", data_dir=DATA_DIR):
    """Load saved S34 files for all quarters.

    kind is 'holdings', 'investors', 'security', or 'log'.
    """
    s34_dir = Path(data_dir) / "clean" / "s34"
    files = sorted(s34_dir.glob(f"*/{FILES[kind]}.parquet"))
    if not files:
        raise FileNotFoundError(f"No S34 {kind} files in {s34_dir}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


if __name__ == "__main__":
    quarters = raw_quarters(S34_START, S34_END)
    if not quarters:
        raise FileNotFoundError(f"No raw S34 files in {RAW_DIR}; run pull_s34.py first")
    clean_s34(quarters, historical_fallback=S34_HISTORICAL_FALLBACK)
    load_s34("log").to_parquet(LOG_PATH, index=False)

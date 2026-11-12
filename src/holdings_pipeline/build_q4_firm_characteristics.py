"""Build the provisional 2022Q4 quarter x permco characteristic table.

Run steps (from the repository root, in the existing finm environment):
1. Set WRDS_USERNAME, START_DATE=2022-01-01, and END_DATE=2022-12-31 in the
   ignored .env file. Keep S34_HISTORICAL_FALLBACK=False for the common pilot.
2. Run ``conda run -n finm python -m doit holdings_provisional jkp:pull``.
   This creates the matched S12/S34 holdings union, the S12 CRSP monthly cache,
   and the raw JKP characteristics. Existing valid caches are reused.
3. Run ``conda run -n finm python -m doit firm_characteristics`` to build the
   company table. The task checks and reuses the inputs from step 2.
4. Review ``_output/firm_characteristics/2022Q4/coverage_audit.json`` and
   ``firm_coverage.parquet``. Use the eligible table at
   ``_data/clean/firm_characteristics/2022Q4/firm_quarterly_provisional.parquet``.

The table covers firms in the matched holdings union only. CRSP quarter-end
common-share market equity is authoritative, in USD millions. JKP book_equity
(Compustat-derived) is counted once per gvkey and expressed in USD millions.
JKP characteristics are security market-equity weighted within a permco.
No cross-source economic investor deduplication, final 20x20 filter, or final
sample winsorization is applied here. Rows failing the provisional JKP/CRSP
eligibility screen remain visible in the coverage audit, not the output table.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from holdings_pipeline.clean_s12 import common_stock_mask
from holdings_pipeline.pull_s12 import raw_directory, sample_dates
from settings import config

CORE = ["at_gr1", "gp_at", "ret_12_1", "div_at", "beta_60m"]
SIZE_ORDER = {"nano": 0, "micro": 1, "small": 2, "large": 3, "mega": 4}
KEY = ["quarter", "permco"]


def _quarter_rows(frame, date_column, quarter, align_month_end=False):
    qdate = pd.Period(quarter, freq="Q").end_time.normalize()
    dates = pd.to_datetime(frame[date_column])
    if align_month_end:
        dates = dates + pd.offsets.MonthEnd(0)
    rows = frame.loc[dates.eq(qdate)].copy()
    return rows, qdate


def crsp_firm_equity(monthly, quarter):
    """Sum all eligible CRSP share classes at quarter end; CRSP shrout is 1,000s."""
    required = {
        "permno",
        "permco",
        "mthcaldt",
        "mthprc",
        "shrout",
        "securitytype",
        "securitysubtype",
        "sharetype",
        "issuertype",
        "primaryexch",
    }
    if required - set(monthly):
        raise ValueError(f"CRSP missing columns: {sorted(required - set(monthly))}")
    rows, qdate = _quarter_rows(monthly, "mthcaldt", quarter, align_month_end=True)
    if rows.duplicated("permno").any():
        raise ValueError("Duplicate CRSP security-month rows")
    rows = rows.loc[common_stock_mask(rows)].copy()
    rows["mthprc"] = pd.to_numeric(rows.mthprc, errors="coerce")
    rows["shrout"] = pd.to_numeric(rows.shrout, errors="coerce")
    rows["security_me_millions"] = rows.mthprc.abs() * rows.shrout / 1000
    rows = rows.loc[
        rows.security_me_millions.gt(0) & np.isfinite(rows.security_me_millions)
    ].copy()
    out = rows.groupby("permco", as_index=False).agg(
        me=("security_me_millions", "sum"),
        n_crsp_share_classes=("permno", "nunique"),
    )
    out["quarter"] = qdate
    out["permco"] = out.permco.astype("int64")
    return out[KEY + ["me", "n_crsp_share_classes"]]


def jkp_firm_characteristics(jkp, quarter):
    """Aggregate JKP security rows; quarantine inconsistent gvkey book equity."""
    required = {
        "permno",
        "permco",
        "gvkey",
        "eom",
        "size_grp",
        "me",
        "book_equity",
        *CORE,
    }
    if required - set(jkp):
        raise ValueError(f"JKP missing columns: {sorted(required - set(jkp))}")
    rows, qdate = _quarter_rows(jkp, "eom", quarter)
    if rows.duplicated(["permno", "eom"]).any():
        raise ValueError("Duplicate JKP security-month rows")
    rows["me"] = pd.to_numeric(rows.me, errors="coerce")
    rows["book_equity"] = pd.to_numeric(rows.book_equity, errors="coerce")
    rows["size_rank"] = rows.size_grp.map(SIZE_ORDER)
    keys = ["permco", "gvkey"]
    book = rows.dropna(subset=["gvkey"]).groupby(keys, dropna=False).book_equity
    book_stats = book.agg(["min", "max", "first", "count"]).reset_index()
    inconsistent = book_stats["count"].gt(1) & ~np.isclose(
        book_stats["min"], book_stats["max"], rtol=1e-8, atol=1e-8, equal_nan=True
    )
    bad_firms = set(book_stats.loc[inconsistent, "permco"])
    missing_gvkey = set(rows.loc[rows.gvkey.isna(), "permco"])
    book_firm = book_stats.groupby("permco")["first"].sum(min_count=1).rename("be")
    base = rows.groupby("permco").agg(
        jkp_me=("me", lambda s: s.sum(min_count=1)),
        size_rank=("size_rank", "max"),
        n_jkp_securities=("permno", "nunique"),
    )
    base = base.join(book_firm)
    base.loc[base.index.isin(bad_firms | missing_gvkey), "be"] = np.nan
    base["book_equity_conflict"] = base.index.isin(bad_firms)
    base["missing_gvkey"] = base.index.isin(missing_gvkey)
    for col in CORE:
        value = pd.to_numeric(rows[col], errors="coerce")
        valid = value.notna() & rows.me.gt(0) & np.isfinite(rows.me)
        weighted = (value * rows.me).where(valid).groupby(rows.permco).sum(min_count=1)
        weights = rows.me.where(valid).groupby(rows.permco).sum(min_count=1)
        base[col] = weighted / weights
    base["size_grp"] = base.size_rank.map({v: k for k, v in SIZE_ORDER.items()})
    base = base.drop(columns="size_rank").reset_index()
    base["quarter"] = qdate
    base["permco"] = base.permco.astype("int64")
    return base


def build_firm_table(holdings, jkp, monthly, quarter="2022Q4"):
    """Return eligible company rows, full holdings-firm coverage, and audit."""
    period = pd.Period(quarter, freq="Q")
    qdate = period.end_time.normalize()
    required = {"quarter", "permco", "source", "sample_status"}
    if required - set(holdings):
        raise ValueError(
            f"Holdings missing columns: {sorted(required - set(holdings))}"
        )
    if holdings.empty or not pd.to_datetime(holdings.quarter).eq(qdate).all():
        raise ValueError(f"Expected nonempty {quarter} holdings only")
    if set(holdings.sample_status) != {"provisional_overlap_unresolved"}:
        raise ValueError("Expected the source-union provisional holdings artifact")
    firms = (
        holdings.groupby("permco")
        .source.agg(lambda s: ",".join(sorted(set(s))))
        .rename("holdings_sources")
        .reset_index()
    )
    firms["permco"] = firms.permco.astype("int64")
    firms["quarter"] = qdate
    coverage = firms.merge(
        crsp_firm_equity(monthly, period), on=KEY, how="left", validate="one_to_one"
    )
    coverage = coverage.merge(
        jkp_firm_characteristics(jkp, period), on=KEY, how="left", validate="one_to_one"
    )
    coverage["missing_crsp_me"] = coverage.me.isna() | coverage.me.le(0)
    coverage["missing_jkp_firm"] = coverage.n_jkp_securities.isna()
    coverage["ineligible_size"] = ~coverage.size_grp.isin(["small", "large", "mega"])
    coverage["nonpositive_or_missing_be"] = coverage.be.isna() | coverage.be.le(0)
    coverage["missing_core_characteristic"] = coverage[CORE].isna().any(axis=1)
    flags = [
        "missing_crsp_me",
        "missing_jkp_firm",
        "ineligible_size",
        "nonpositive_or_missing_be",
        "missing_core_characteristic",
    ]
    coverage["eligible"] = ~coverage[flags].any(axis=1)
    coverage["log_me"] = np.log(coverage.me.where(coverage.me.gt(0)))
    coverage["log_be"] = np.log(coverage.be.where(coverage.be.gt(0)))
    coverage["log_be_me"] = coverage.log_be - coverage.log_me
    coverage["crsp_jkp_me_ratio"] = coverage.me / coverage.jkp_me.where(
        coverage.jkp_me.gt(0)
    )
    cols = KEY + [
        "me",
        "log_me",
        "be",
        "log_be",
        "log_be_me",
        *CORE,
        "size_grp",
        "n_crsp_share_classes",
        "n_jkp_securities",
        "jkp_me",
        "crsp_jkp_me_ratio",
        "holdings_sources",
    ]
    table = (
        coverage.loc[coverage.eligible, cols].sort_values(KEY).reset_index(drop=True)
    )
    if (
        table.duplicated(KEY).any()
        or not np.isfinite(
            table[["me", "be", "log_be_me", *CORE]].to_numpy(dtype=float)
        ).all()
    ):
        raise ValueError("Firm table has duplicate keys or nonfinite required values")
    if not set(table.permco).issubset(set(holdings.permco)):
        raise ValueError("Firm table is not a subset of matched holdings firms")
    coverage = coverage.sort_values(KEY).reset_index(drop=True)
    audit = {
        "quarter": str(period),
        "status": "provisional_overlap_unresolved",
        "holdings_firms": int(len(firms)),
        "eligible_firms": int(len(table)),
        "excluded_firms": int(len(firms) - len(table)),
        "exclusion_flags_nonexclusive": {
            flag: int(coverage[flag].sum()) for flag in flags
        },
        "book_equity_conflict_firms": int(
            coverage.book_equity_conflict.fillna(False).sum()
        ),
        "missing_gvkey_firms": int(coverage.missing_gvkey.fillna(False).sum()),
        "me_unit": "USD millions; CRSP quarter-end price x shrout",
        "be_unit": "USD millions; JKP Compustat-derived book_equity",
        "winsorized": False,
        "final_sample": False,
        "note": (
            "Firm coverage is provisional; S12/S34 economic overlap "
            "and final joint filters remain unresolved."
        ),
    }
    return table, coverage, audit


def run(quarter="2022Q4", data_dir=None, output_dir=None, start=None, end=None):
    data_dir = Path(data_dir or config("DATA_DIR"))
    output_dir = Path(output_dir or config("OUTPUT_DIR"))
    start, end = sample_dates(start, end)
    period = pd.Period(quarter, freq="Q")
    raw_crsp = raw_directory(start, end, data_dir) / "raw_crsp_monthly.parquet"
    paths = {
        "holdings": data_dir
        / "clean"
        / "combined"
        / str(period)
        / "holdings_provisional.parquet",
        "jkp": data_dir / "jkp_raw.pkl",
        "crsp": raw_crsp,
    }
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(
            f"Missing Q4 inputs: {missing}. Run steps 1-2 in the module header."
        )
    holdings = pd.read_parquet(paths["holdings"])
    jkp = pd.read_pickle(paths["jkp"])
    monthly = pd.read_parquet(paths["crsp"])
    table, coverage, audit = build_firm_table(holdings, jkp, monthly, period)
    target = data_dir / "clean" / "firm_characteristics" / str(period)
    report = output_dir / "firm_characteristics" / str(period)
    target.mkdir(parents=True, exist_ok=True)
    report.mkdir(parents=True, exist_ok=True)
    table.to_parquet(target / "firm_quarterly_provisional.parquet", index=False)
    coverage.to_parquet(report / "firm_coverage.parquet", index=False)
    (report / "coverage_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default="2022Q4")
    args = parser.parse_args()
    print(json.dumps(run(args.quarter), indent=2))

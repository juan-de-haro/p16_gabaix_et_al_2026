"""Combine the source-level S12 and S34 firm holdings for a provisional pilot.

Both source pipelines must use unique, report-date-valid CRSP CUSIP links and
the same listed common-stock eligibility rule. This step preserves source
investors and does not resolve economic overlap between funds and managers or
apply the final firm universe and 20-by-20 filters.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from settings import config


KEY = ["quarter", "investor_id", "permco"]
VALUES = ["shares", "dollar_holding"]


def _prepare_source(frame, source, quarter):
    """Keep only the shared contract and reject incompatible source outputs."""
    required = KEY + VALUES
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{source}: missing columns {sorted(missing)}")
    out = frame[required].copy()
    out["quarter"] = pd.to_datetime(out["quarter"])
    expected = pd.Period(quarter, freq="Q").end_time.normalize()
    if out.empty or not out["quarter"].eq(expected).all():
        raise ValueError(f"{source}: expected nonempty {quarter} holdings only")
    if out[KEY].isna().any().any() or out.duplicated(KEY).any():
        raise ValueError(f"{source}: null or duplicate firm-holding keys")
    if not out["investor_id"].str.startswith(f"{source}_").all():
        raise ValueError(f"{source}: investor IDs must retain source prefixes")
    if not np.isfinite(out[VALUES].to_numpy(dtype=float)).all():
        raise ValueError(f"{source}: non-finite position values")
    if not out[VALUES].gt(0).all().all():
        raise ValueError(f"{source}: non-positive position values")
    out["permco"] = out["permco"].astype("int64")
    out["source"] = source
    return out


def combine_source_holdings(s12, s34, quarter):
    """Return provisional combined holdings and source/firm coverage metrics."""
    left = _prepare_source(s12, "s12", quarter)
    right = _prepare_source(s34, "s34", quarter)
    combined = pd.concat([left, right], ignore_index=True)
    if combined.duplicated(KEY).any():
        raise ValueError("Source holdings have colliding investor-firm keys")
    firms_left, firms_right = set(left.permco), set(right.permco)
    combined["firm_in_both_sources"] = combined.permco.isin(
        firms_left & firms_right
    )
    combined["sample_status"] = "provisional_overlap_unresolved"
    audit = {
        "quarter": str(pd.Period(quarter, freq="Q")),
        "status": "provisional_overlap_unresolved",
        "s12_positions": len(left),
        "s34_positions": len(right),
        "combined_positions": len(combined),
        "s12_investors": int(left.investor_id.nunique()),
        "s34_investors": int(right.investor_id.nunique()),
        "s12_firms": len(firms_left),
        "s34_firms": len(firms_right),
        "firms_in_both_sources": len(firms_left & firms_right),
        "firms_in_union": len(firms_left | firms_right),
        "warning": (
            "Shared firms are expected and do not identify overlapping economic "
            "investors. S12/S34 fund-manager overlap has not been resolved. "
            "Portfolio weights and ownership shares must be recomputed after "
            "the final sample is selected."
        ),
    }
    return combined.sort_values(KEY).reset_index(drop=True), audit


def _matching_rate(log, numerator, denominator):
    counts = log.set_index("step")["n_obs"]
    if numerator not in counts or denominator not in counts:
        return None
    n, d = int(counts[numerator]), int(counts[denominator])
    return {"matched": n, "eligible_input": d, "rate": n / d if d else None}


def compare_security_links(s12_security, s34_security):
    """Find same-date, same-CUSIP links that disagree across the sources."""
    keys = ["rdate", "cusip"]
    values = ["permno", "permco"]
    maps = []
    for source, frame in (("s12", s12_security), ("s34", s34_security)):
        missing = set(keys + values) - set(frame.columns)
        if missing:
            raise ValueError(f"{source}: missing security link fields {sorted(missing)}")
        mapping = frame[keys + values].drop_duplicates().copy()
        mapping["rdate"] = pd.to_datetime(mapping["rdate"])
        mapping["cusip"] = mapping["cusip"].astype("string").str.strip().str.upper()
        if mapping.duplicated(keys).any():
            raise ValueError(f"{source}: one date/CUSIP maps to multiple securities")
        maps.append(mapping)
    joined = maps[0].merge(maps[1], on=keys, suffixes=("_s12", "_s34"))
    conflict = joined.permno_s12.ne(joined.permno_s34) | joined.permco_s12.ne(
        joined.permco_s34
    )
    return joined, joined.loc[conflict].copy()


def build_pilot(quarter="2022Q4", data_dir=None, output_dir=None):
    """Load source files and write a provisional Parquet table and JSON audit."""
    data_dir = Path(data_dir or config("DATA_DIR"))
    output_dir = Path(output_dir or config("OUTPUT_DIR"))
    period = pd.Period(quarter, freq="Q")
    qdate = period.end_time.normalize().strftime("%Y-%m-%d")
    s12_dir = data_dir / "clean" / "s12" / str(period)
    s34_dir = data_dir / "s34"
    s12 = pd.read_parquet(s12_dir / "holdings_s12.parquet")
    s34 = pd.read_pickle(s34_dir / f"holdings_{qdate}.pkl")
    s12_security = pd.read_parquet(s12_dir / "holdings_s12_security.parquet")
    s34_security = pd.read_pickle(s34_dir / f"security_{qdate}.pkl")
    combined, audit = combine_source_holdings(s12, s34, period)
    checked, conflicts = compare_security_links(s12_security, s34_security)
    audit["same_date_cusips_compared"] = len(checked)
    audit["cross_source_link_conflicts"] = len(conflicts)
    s12_log = pd.read_parquet(s12_dir / "filter_log_s12.parquet")
    s34_log = pd.read_pickle(s34_dir / f"log_{qdate}.pkl")
    audit["s12_date_valid_match"] = _matching_rate(
        s12_log, "unique_cusip_at_report_date", "positive_shares_valid_cusip"
    )
    audit["s34_date_valid_match"] = _matching_rate(
        s34_log, "2a_match_direct", "1_download"
    )
    target_dir = data_dir / "clean" / "combined" / str(period)
    report_dir = output_dir / "combined" / str(period)
    target_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    conflicts.to_parquet(report_dir / "link_conflicts.parquet", index=False)
    (report_dir / "matching_audit.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    if not conflicts.empty:
        raise ValueError("S12/S34 date-valid security links conflict; review audit")
    combined.to_parquet(target_dir / "holdings_provisional.parquet", index=False)
    return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default="2022Q4")
    args = parser.parse_args()
    print(json.dumps(build_pilot(args.quarter), indent=2))

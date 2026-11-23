"""Summarize quarterly S12 holdings and data checks over the paper's sample."""

import json
import sys
from pathlib import Path

import pandas as pd
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import config

YEARS = range(2005, 2023)
QUARTERS = [f"{y}Q{q}" for y in YEARS for q in range(1, 5)]
CHECKS = (
    "nonempty",
    "unique_fund_firm",
    "unique_fund_security",
    "positive_finite_values",
    "portfolio_weights_sum_to_one",
    "aggregation_preserves_value",
    "investor_coverage",
    "firm_coverage",
    "filter_rows_nonincreasing",
    "quarter_keys_are_quarter_end_dates",
    "reports_in_quarter",
    "vintage_not_before_report",
    "firm_ownership_at_most_one",
    "annual_rows_match_wrds_count",
    "quarter_rows_match_wrds_count",
)
OUTPUT_FILES = ("quarterly_coverage.csv", "validation_by_quarter.csv")


def quarter_result(quarter, holdings_path, report_path):
    """Reconcile the reported coverage counts with the written holdings table."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = report["checks"]
    if report["quarter"] != quarter or checks.keys() != set(CHECKS):
        raise ValueError(f"{quarter}: wrong quarter or unexpected validation checks")
    failed = [k for k in CHECKS if not checks[k]["passed"]]
    if set(failed) - {"firm_ownership_at_most_one"}:
        raise ValueError(f"{quarter}: failed structural checks: {failed}")
    status = "passed_with_warnings" if failed else "passed"
    s = report["summary"]
    actual = (
        pl.scan_parquet(holdings_path)
        .select(
            pl.len(),
            pl.col("investor_id").n_unique(),
            pl.col("permco").n_unique(),
            pl.col("permco").filter(pl.col("ownership_flag")).n_unique().alias("flags"),
        )
        .collect()
        .row(0)
    )
    expected = (
        s["final_fund_firm_positions"],
        s["final_funds"],
        s["final_firms"],
        s["firms_with_s12_ownership_above_100pct"],
    )
    if (
        actual != expected
        or report["status"] != status
        or bool(actual[3]) != bool(failed)
    ):
        raise ValueError(
            f"{quarter}: inconsistent holdings counts or validation status"
        )
    positions, funds, firms, flags = actual
    coverage = {
        "quarter": quarter,
        "raw_rows": s["raw_quarter_rows_all_vintages"],
        "funds": funds,
        "firms": firms,
        "fund_firm_positions": positions,
        "cusip_match_pct": round(100 * s["cusip_match_rate"], 2),
        "checks_passed": len(checks) - len(failed),
        "checks_run": len(checks),
        "ownership_flags": flags,
        "max_ownership_pct": round(100 * s["max_firm_s12_ownership"], 2),
        "off_quarter_end_funds": s["non_quarter_end_report_funds_retained"],
        "later_vintage_funds": s["first_vintage_after_quarter_funds_retained"],
        "status": status,
    }
    return coverage, {k: "Flag" if k in failed else "Pass" for k in CHECKS}


def summarize():
    """Write both aggregate CSVs once all 72 quarter reports have been verified."""
    data = Path(config("DATA_DIR")) / "clean/s12"
    output = Path(config("OUTPUT_DIR"))
    coverage, matrix = [], {}
    for q in QUARTERS:
        row, matrix[q] = quarter_result(
            q, data / q / "holdings_s12.parquet", output / "s12" / q / "validation.json"
        )
        coverage.append(row)
    dest = output / "s12_history"
    dest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(coverage).to_csv(dest / OUTPUT_FILES[0], index=False)
    pd.DataFrame(matrix).rename_axis("check").to_csv(dest / OUTPUT_FILES[1])


if __name__ == "__main__":
    summarize()

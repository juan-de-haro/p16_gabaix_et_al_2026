"""Exercise the historical summary's cross-file reconciliation on small inputs."""

import json

import polars as pl
import pytest

from holdings_pipeline.s12_history import CHECKS, quarter_result


@pytest.fixture
def quarter_files(tmp_path):
    holdings = tmp_path / "holdings.parquet"
    report = tmp_path / "validation.json"
    pl.DataFrame(
        {"investor_id": ["a", "b"], "permco": [10, 10], "ownership_flag": [True, True]}
    ).write_parquet(holdings)
    checks = {name: {"passed": name != "firm_ownership_at_most_one"} for name in CHECKS}
    payload = {
        "quarter": "2005Q2",
        "status": "passed_with_warnings",
        "checks": checks,
        "summary": {
            "final_fund_firm_positions": 2,
            "final_funds": 2,
            "final_firms": 1,
            "firms_with_s12_ownership_above_100pct": 1,
            "cusip_match_rate": 0.5,
            "raw_quarter_rows_all_vintages": 5,
            "max_firm_s12_ownership": 1.2,
            "non_quarter_end_report_funds_retained": 0,
            "first_vintage_after_quarter_funds_retained": 1,
        },
    }
    report.write_text(json.dumps(payload), encoding="utf-8")
    return holdings, report, payload


def test_shared_flagged_company_counts_once(quarter_files):
    holdings, report, _ = quarter_files
    row, checks = quarter_result("2005Q2", holdings, report)
    assert (row["ownership_flags"], row["checks_passed"]) == (1, 14)
    assert checks["firm_ownership_at_most_one"] == "Flag"


def test_rejects_mismatched_holdings(quarter_files):
    holdings, report, _ = quarter_files
    pl.read_parquet(holdings).head(1).write_parquet(holdings)
    with pytest.raises(ValueError, match="inconsistent holdings"):
        quarter_result("2005Q2", holdings, report)


def test_requires_independent_quarter_receipt(quarter_files):
    holdings, report, payload = quarter_files
    del payload["checks"]["quarter_rows_match_wrds_count"]
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected validation checks"):
        quarter_result("2005Q2", holdings, report)

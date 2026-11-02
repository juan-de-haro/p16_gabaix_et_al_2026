"""Check provisional S12/S34 integration and its economic-overlap boundary."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from holdings_pipeline.integrate_holdings import (
    build_pilot,
    combine_source_holdings,
    compare_security_links,
)


def _source(source, investor, firms):
    return pd.DataFrame({
        "quarter": pd.Timestamp("2022-12-31"),
        "investor_id": [f"{source}_{investor}"] * len(firms),
        "permco": firms,
        "shares": [10.0] * len(firms),
        "dollar_holding": [100.0] * len(firms),
    })


def test_shared_company_does_not_imply_duplicate_investor():
    combined, audit = combine_source_holdings(
        _source("s12", 1, [10, 20]), _source("s34", 1, [20, 30]), "2022Q4"
    )
    assert len(combined) == 4
    assert audit["firms_in_both_sources"] == 1
    assert audit["firms_in_union"] == 3
    assert set(combined.loc[combined.permco.eq(20), "source"]) == {"s12", "s34"}
    assert combined.sample_status.eq("provisional_overlap_unresolved").all()


def test_duplicate_or_wrong_quarter_fails_before_combining():
    s12 = _source("s12", 1, [10])
    s34 = _source("s34", 1, [20])
    with pytest.raises(ValueError, match="duplicate"):
        combine_source_holdings(pd.concat([s12, s12]), s34, "2022Q4")
    with pytest.raises(ValueError, match="expected nonempty"):
        combine_source_holdings(
            s12.assign(quarter=pd.Timestamp("2022-09-30")), s34, "2022Q4"
        )


def test_same_date_cusip_conflict_is_detected():
    s12 = pd.DataFrame({
        "rdate": ["2022-12-31", "2022-11-30"],
        "cusip": ["AAA11111", "BBB22222"],
        "permno": [10, 20],
        "permco": [100, 200],
    })
    s34 = pd.DataFrame({
        "rdate": ["2022-12-31", "2022-12-31"],
        "cusip": ["AAA11111", "BBB22222"],
        "permno": [11, 30],
        "permco": [101, 300],
    })
    checked, conflicts = compare_security_links(s12, s34)
    assert len(checked) == 1  # November and December links are not conflated.
    assert conflicts[["permno_s12", "permno_s34"]].iloc[0].tolist() == [10, 11]


def test_pilot_reads_both_source_contracts_and_writes_audit(tmp_path):
    data = tmp_path / "data"
    output = tmp_path / "output"
    s12_dir = data / "clean" / "s12" / "2022Q4"
    s34_dir = data / "s34"
    s12_dir.mkdir(parents=True)
    s34_dir.mkdir(parents=True)
    _source("s12", 1, [10]).to_parquet(s12_dir / "holdings_s12.parquet")
    _source("s34", 2, [10]).to_pickle(s34_dir / "holdings_2022-12-31.pkl")
    security = pd.DataFrame({
        "rdate": [pd.Timestamp("2022-12-31")],
        "cusip": ["AAA11111"], "permno": [100], "permco": [10],
    })
    security.to_parquet(s12_dir / "holdings_s12_security.parquet")
    security.to_pickle(s34_dir / "security_2022-12-31.pkl")
    pd.DataFrame({
        "step": ["positive_shares_valid_cusip", "unique_cusip_at_report_date"],
        "n_obs": [2, 1],
    }).to_parquet(s12_dir / "filter_log_s12.parquet")
    pd.DataFrame({
        "step": ["1_download", "2a_match_direct"], "n_obs": [3, 1],
    }).to_pickle(s34_dir / "log_2022-12-31.pkl")

    audit = build_pilot("2022Q4", data, output)
    combined = pd.read_parquet(
        data / "clean" / "combined" / "2022Q4" / "holdings_provisional.parquet"
    )
    assert len(combined) == 2
    assert audit["same_date_cusips_compared"] == 1
    assert audit["cross_source_link_conflicts"] == 0
    assert audit["s12_date_valid_match"]["rate"] == 0.5
    assert (output / "combined" / "2022Q4" / "matching_audit.json").exists()

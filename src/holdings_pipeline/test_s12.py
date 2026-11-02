"""Economic and timing edge cases for the S12 contribution pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from holdings_pipeline.clean_s12 import (
    aggregate_firms,
    link_by_report_date,
    ownership_exception_details,
    prepare_market,
    remove_source_conflicts,
    select_reports,
    value_positions,
)


def test_snapshot_reads_decimal_and_date_raw_types(tmp_path, monkeypatch):
    from datetime import date
    from decimal import Decimal

    from holdings_pipeline import clean_s12

    raw = pa.table(
        {
            "fundno": pa.array([1, 1, 1, 2], type=pa.int32()),
            "rdate": pa.array([date(2022, 12, 31)] * 3 + [date(2022, 9, 30)]),
            "fdate": pa.array(
                [
                    date(2023, 3, 31),
                    date(2023, 3, 31),
                    date(2023, 6, 30),
                    date(2022, 12, 31),
                ]
            ),
            "shares": pa.array(
                [Decimal("10"), Decimal("20"), Decimal("10"), Decimal("1")],
                type=pa.decimal128(12, 0),
            ),
            "cusip": ["00000001", "00000002", "00000001", "00000003"],
        }
    )
    pq.write_table(raw, tmp_path / "raw_s12_holdings.parquet")
    monkeypatch.setattr(clean_s12, "raw_directory", lambda *args: tmp_path)
    snapshot, _, _, counts = clean_s12.snapshot_from_cache(
        "2022-01-01", "2022-12-31", "2022Q4"
    )
    assert counts == (3, 1)
    assert sorted(snapshot.shares) == [10, 20]


def test_latest_report_earliest_vintage_and_no_stale_carry():
    reports = pd.DataFrame(
        {
            "fundno": [1, 1, 1, 2, 3],
            "rdate": [
                "2022-10-31",
                "2022-12-31",
                "2022-12-31",
                "2022-09-30",
                "2023-01-31",
            ],
            "fdate": [
                "2022-12-31",
                "2023-03-31",
                "2023-06-30",
                "2022-12-31",
                "2023-03-31",
            ],
        }
    )
    chosen = select_reports(reports, "2022Q4")
    assert len(chosen) == 1
    assert chosen.iloc[0].fundno == 1
    assert chosen.iloc[0].rdate == pd.Timestamp("2022-12-31")
    assert chosen.iloc[0].fdate == pd.Timestamp("2023-03-31")


def test_source_conflicts_are_not_summed_or_arbitrarily_kept():
    positions = pd.DataFrame(
        {
            "fundno": [1, 1, 2, 2],
            "rdate": ["2022-12-31"] * 4,
            "fdate": ["2022-12-31"] * 4,
            "cusip": ["00000001"] * 4,
            "shares": [10, 10, 20, 30],
        }
    )
    clean, rejected = remove_source_conflicts(positions)
    assert clean.shares.tolist() == [10]
    assert set(rejected.shares) == {20, 30}


def test_cusip_link_uses_report_date_not_future_name_and_rejects_ambiguity():
    positions = pd.DataFrame(
        {
            "fundno": [1, 2, 3],
            "cusip": ["00000001", "00000002", "00000003"],
            "rdate": pd.to_datetime(["2022-12-31"] * 3),
        }
    )
    names = pd.DataFrame(
        {
            "ncusip": ["00000001", "00000001", "00000002", "00000002"],
            "permno": [11, 12, 21, 22],
            "permco": [1, 2, 3, 4],
            "namedt": ["2020-01-01", "2023-01-01", "2020-01-01", "2020-01-01"],
            "nameendt": ["2022-12-31", "2024-12-31", "2024-12-31", "2024-12-31"],
        }
    )
    linked, unmatched, ambiguous = link_by_report_date(positions, names)
    assert linked.permno.tolist() == [11]
    assert unmatched.fundno.tolist() == [3]
    assert set(ambiguous.permno) == {21, 22}


def test_later_vintage_split_is_reversed_before_valuing_at_quarter_end():
    # A 2-for-1 split happens after Q4. The vendor's later vintage says 200
    # shares, but the quarter-end position was 100 shares at $10, worth $1000.
    linked = pd.DataFrame(
        {
            "fundno": [1],
            "permno": [11],
            "fdate": pd.to_datetime(["2023-03-31"]),
            "shares": [200.0],
        }
    )
    monthly = pd.DataFrame(
        {"permno": [11], "month": pd.to_datetime(["2023-03-31"]), "mthcumfacshr": [1.0]}
    )
    market = pd.DataFrame(
        {
            "permno": [11],
            "permco": [1],
            "quarter_price": [-10.0],
            "quarter_factor": [2.0],
            "security_me": [100_000.0],
            "issuernm": ["Example"],
            "mthprcdt": pd.to_datetime(["2022-12-30"]),
        }
    )
    valued, rejected = value_positions(linked, monthly, market)
    assert rejected.empty
    assert valued.iloc[0].shares_adjusted == 100
    assert valued.iloc[0].dollar_holding == 1000
    assert valued.iloc[0].security_ownership_share == 0.01


def test_company_cap_includes_unheld_share_class_and_thousand_share_units():
    monthly = pd.DataFrame(
        {
            "permno": [11, 12, 13],
            "permco": [1, 1, 2],
            "mthcaldt": pd.to_datetime(["2022-12-30"] * 3),
            "mthprc": [10, 20, 99],
            "shrout": [1, 2, 100],
            "mthcumfacshr": [1, 1, 1],
            "mthret": [0, 0, 0],
            "securitytype": ["EQTY"] * 3,
            "securitysubtype": ["COM", "COM", "PFD"],
            "sharetype": ["NS"] * 3,
            "usincflg": ["Y"] * 3,
            "issuertype": ["CORP"] * 3,
            "primaryexch": ["N"] * 3,
        }
    )
    _, market, caps = prepare_market(monthly, "2022Q4")
    assert len(market) == 2
    assert caps.to_dict("records") == [{"permco": 1, "me": 50_000}]


def test_share_class_aggregation_preserves_dollars_and_flagged_ownership():
    securities = pd.DataFrame(
        {
            "fundno": [1, 1, 1, 2],
            "permno": [11, 12, 21, 11],
            "permco": [1, 1, 2, 1],
            "shares_adjusted": [10.0, 20.0, 30.0, 40.0],
            "dollar_holding": [100.0, 200.0, 300.0, 400.0],
            "rdate": pd.to_datetime(["2022-12-31"] * 4),
            "fdate": pd.to_datetime(["2022-12-31"] * 4),
        }
    )
    caps = pd.DataFrame({"permco": [1, 2], "me": [600.0, 1000.0]})
    holdings, _ = aggregate_firms(securities, caps, "2022Q4")
    assert len(holdings) == 3
    assert holdings.dollar_holding.sum() == 1000
    assert np.allclose(holdings.groupby("investor_id").portfolio_weight.sum(), 1)
    first = holdings.query("investor_id == 's12_1' and permco == 1").iloc[0]
    assert first.dollar_holding == 300
    assert first.n_share_classes == 2
    assert first.ownership_flag
    assert np.isclose(first.ownership_total_s12, 700 / 600)


def test_ownership_review_decomposes_dates_using_firm_value_not_share_units():
    # Two differently priced classes, including an earlier report. The
    # quarter-end-only numerator must use firm dollars, not pooled shares.
    quarter = pd.Timestamp("2022-12-31")
    securities = pd.DataFrame(
        {
            "fundno": [1, 2, 3],
            "investor_id": ["s12_1", "s12_2", "s12_3"],
            "permno": [11, 12, 11],
            "permco": [1, 1, 1],
            "quarter": [quarter] * 3,
            "rdate": pd.to_datetime(["2022-12-31", "2022-12-31", "2022-11-30"]),
            "shares_adjusted": [110.0, 20.0, 5.0],
            "dollar_holding": [1100.0, 400.0, 50.0],
            "quarter_price": [10.0, 20.0, 10.0],
            "security_me": [1000.0, 400.0, 1000.0],
            "security_ownership_share": [1.1, 1.0, 0.05],
        }
    )
    holdings = securities[["quarter", "investor_id", "permco"]].copy()
    holdings["me"] = 1400.0
    holdings["ownership_total_s12"] = 1550 / 1400
    holdings["ownership_flag"] = True
    monthly = pd.DataFrame(
        {
            "permno": [11, 12, 11],
            "month": pd.to_datetime(["2022-12-31", "2022-12-31", "2022-11-30"]),
            "shrout": [1.0, 0.02, 10.0],
        }
    )
    original = securities.copy(deep=True)
    rows, totals, by_date = ownership_exception_details(holdings, securities, monthly)
    pd.testing.assert_frame_equal(securities, original)
    assert rows.individual_security_above_one.tolist() == [True, False, False]
    assert rows.report_age_days.tolist() == [0, 0, 31]
    assert rows.quarter_shrout_shares.tolist() == [100.0, 20.0, 100.0]
    assert np.isclose(totals.iloc[0].ownership_from_quarter_end_reports, 1500 / 1400)
    assert np.isclose(totals.iloc[0].ownership_from_earlier_reports, 50 / 1400)
    assert totals.iloc[0].quarter_end_reports_still_above_one
    assert totals.iloc[0].n_earlier_report_positions == 1
    assert np.isclose(by_date.firm_ownership_contribution.sum(), 1550 / 1400)

    # Missing contextual history does not impute a share count or drop a row.
    rows, _, _ = ownership_exception_details(holdings, securities, monthly.iloc[:2])
    assert len(rows) == 3
    assert pd.isna(rows.iloc[2].report_month_shrout_thousands)

    holdings["ownership_flag"] = False
    rows, totals, by_date = ownership_exception_details(holdings, securities, monthly)
    assert rows.empty and totals.empty and by_date.empty

"""Tests for pull_s34.py (raw layer) and clean_s34.py (clean layer).

Unit tests pull raw tables from a small fake WRDS connection and clean them
with build_s34_quarter, so they need no network access. Output tests check
the saved S34 files in DATA_DIR and are skipped when those files do not exist
yet.

    uv run pytest src/holdings_pipeline/test_s34.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from holdings_pipeline.clean_s34 import (
    S34_DIR,
    build_s34_quarter,
    clean_s34,
    cusip_map_from_names,
    load_s34,
    quarter_dir,
    quarter_file,
)
from holdings_pipeline.pull_s34 import (
    load_raw_s34,
    pull_names,
    pull_quarter,
    pull_s34,
    raw_file,
)

RDATE = "2022-12-31"


def _common(
    permno,
    permco,
    cusip,
    sharetype="NS",
    usincflg="Y",
    issuertype="CORP",
    namedt="2000-01-01",
    nameenddt="2099-12-31",
):
    return {
        "permno": permno,
        "permco": permco,
        "cusip": cusip,
        "hdrcusip": cusip,
        "namedt": namedt,
        "nameenddt": nameenddt,
        "sharetype": sharetype,
        "securitytype": "EQTY",
        "securitysubtype": "COM",
        "usincflg": usincflg,
        "issuertype": issuertype,
    }


class FakeDB:
    """Return canned tables for the queries made by pull_quarter and pull_names."""

    def __init__(self):
        self.managers = pd.DataFrame(
            {
                "mgrno": [1.0, 2.0],
                "mgrname": ["MANAGER ONE", "MANAGER TWO"],
                "typecode": [4.0, np.nan],
                "fdate": ["2022-12-31", "2023-03-31"],
                "rdate": [RDATE, RDATE],
            }
        )
        self.holdings = pd.DataFrame(
            [
                # Direct CUSIP match
                {
                    "mgrno": 1.0,
                    "fdate": "2022-12-31",
                    "cusip": "AAA11111",
                    "shares": 100.0,
                },
                # Only matches through a historical CUSIP (the GE case); foreign issuer
                {
                    "mgrno": 1.0,
                    "fdate": "2022-12-31",
                    "cusip": "BBB22222",
                    "shares": 50.0,
                },
                # ADR: matched, then removed by the common-stock filter
                {
                    "mgrno": 1.0,
                    "fdate": "2022-12-31",
                    "cusip": "CCC33333",
                    "shares": 10.0,
                },
                # Later vintage with a 2-for-1 split between rdate and fdate
                {
                    "mgrno": 2.0,
                    "fdate": "2023-03-31",
                    "cusip": "AAA11111",
                    "shares": 200.0,
                },
                # Two share classes of one firm
                {
                    "mgrno": 2.0,
                    "fdate": "2023-03-31",
                    "cusip": "DDD44444",
                    "shares": 10.0,
                },
                {
                    "mgrno": 2.0,
                    "fdate": "2023-03-31",
                    "cusip": "DDD55555",
                    "shares": 20.0,
                },
                # Unknown CUSIP
                {
                    "mgrno": 2.0,
                    "fdate": "2023-03-31",
                    "cusip": "ZZZ99999",
                    "shares": 5.0,
                },
                # Ambiguous historical CUSIP: maps to two active securities
                {
                    "mgrno": 2.0,
                    "fdate": "2023-03-31",
                    "cusip": "AMB00000",
                    "shares": 5.0,
                },
            ]
        )
        self.names = pd.DataFrame(
            [
                _common(10, 100, "AAA11111"),
                _common(20, 200, "BBBOLD00", usincflg="N"),
                _common(30, 300, "CCC33333", sharetype="AD"),
                _common(41, 400, "DDD44444"),
                _common(42, 400, "DDD55555"),
                _common(50, 500, "EEE11111"),
                _common(51, 510, "EEE22222"),
            ]
        )
        self.msf = pd.DataFrame(
            [
                {"permno": 10, "yyyymm": 202212, "mthprc": 10.0, "mthcumfacshr": 2.0},
                {"permno": 10, "yyyymm": 202303, "mthprc": 5.0, "mthcumfacshr": 1.0},
                {"permno": 20, "yyyymm": 202212, "mthprc": -5.0, "mthcumfacshr": 1.0},
                {"permno": 30, "yyyymm": 202212, "mthprc": 7.0, "mthcumfacshr": 1.0},
                {"permno": 41, "yyyymm": 202212, "mthprc": 2.0, "mthcumfacshr": 1.0},
                {"permno": 41, "yyyymm": 202303, "mthprc": 2.0, "mthcumfacshr": 1.0},
                {"permno": 42, "yyyymm": 202212, "mthprc": 3.0, "mthcumfacshr": 1.0},
                {"permno": 42, "yyyymm": 202303, "mthprc": 3.0, "mthcumfacshr": 1.0},
            ]
        )

    def raw_sql(self, query, date_cols=None):
        if "s34type3" in query:
            df = self.holdings
        elif "s34type1" in query:
            df = self.managers
        elif "stocknames_v2" in query:
            df = self.names
        elif "msf_v2" in query:
            df = self.msf
        else:
            raise ValueError(f"Unexpected query: {query}")
        df = df.copy()
        if "msf_v2" in query:
            df["primaryexch"] = "N"
        for c in date_cols or []:
            df[c] = pd.to_datetime(df[c])
        return df


CUSIP_MAP = pd.DataFrame(
    {
        "cusip": ["AAA11111", "BBB22222", "AMB00000", "AMB00000"],
        "permno": [10, 20, 50, 51],
    }
)


def _build(db, cusip_map=CUSIP_MAP, **kwargs):
    """Pull raw tables from the fake connection, then clean them."""
    raw = pull_quarter(db, RDATE)
    raw["names"] = pull_names(db)
    return build_s34_quarter(raw, RDATE, cusip_map, **kwargs)


@pytest.fixture(scope="module")
def result():
    return _build(FakeDB())


def test_holdings_values(result):
    hold, _, _, _ = result
    got = hold.set_index(["investor_id", "permco"])["dollar_holding"].to_dict()
    assert got == {
        ("s34_1", 100): 1000.0,  # 100 shares * 10
        ("s34_2", 100): 1000.0,  # 200 shares halved for the split, * 10
        ("s34_2", 400): 80.0,  # 10 * 2 + 20 * 3, two classes summed
    }


def test_holdings_key_unique_and_positive(result):
    hold, _, _, _ = result
    assert not hold.duplicated(["quarter", "investor_id", "permco"]).any()
    assert np.isfinite(hold["dollar_holding"]).all()
    assert (hold["dollar_holding"] > 0).all()
    assert (hold["quarter"] == pd.Timestamp(RDATE)).all()


def test_excluded_positions(result):
    hold, _, _, _ = result
    assert 300 not in set(hold["permco"])  # ADR
    assert not set(hold["permco"]) & {500, 510}  # ambiguous CUSIP


def test_historical_fallback_is_explicit_sensitivity():
    hold, _, _, _ = _build(FakeDB(), historical_fallback=True)
    assert (
        hold.set_index(["investor_id", "permco"]).loc[("s34_1", 200), "dollar_holding"]
        == 250
    )


def test_ambiguous_direct_cusip_is_quarantined():
    db = FakeDB()
    db.names = pd.concat(
        [
            db.names,
            pd.DataFrame([_common(60, 600, "AAA11111")]),
        ],
        ignore_index=True,
    )
    hold, _, _, _ = _build(db)
    assert 100 not in set(hold.permco)
    assert 600 not in set(hold.permco)


def test_split_adjustment_reported(result):
    _, _, _, adj = result
    assert len(adj) == 1
    row = adj.iloc[0]
    assert row["permno"] == 10 and row["shares_adj"] == 0.5 * row["shares"]


def test_investors(result):
    hold, inv, _, _ = result
    assert not inv.duplicated(["quarter", "investor_id"]).any()
    assert set(inv["investor_id"]) == set(hold["investor_id"])
    inv = inv.set_index("investor_id")
    aum = hold.groupby("investor_id")["dollar_holding"].sum()
    assert np.allclose(inv.loc[aum.index, "aum"], aum)
    assert inv.loc["s34_1", "investor_type"] == "13f_typecode_4"
    assert inv.loc["s34_2", "investor_type"] == "13f_typecode_missing"
    assert inv.loc["s34_2", "n_holdings"] == 2


def test_filter_log(result):
    _, _, log, _ = result
    n = log.set_index("step")["n_obs"]
    assert n["1_download"] == 8
    assert n["2a_match_direct"] == 5
    assert n["2_match_cusip"] == 5
    later = [
        "2_match_cusip",
        "2b_aliases_quarantined",
        "3_common_stock",
        "4_price",
        "5_aggregate_permco",
    ]
    assert n[later].is_monotonic_decreasing


def test_quarter_paths_follow_the_s12_layout(tmp_path):
    assert quarter_dir("2022-12-31", tmp_path) == tmp_path / "2022Q4"
    assert quarter_file("2022-12-31", "security", tmp_path).name == (
        "holdings_s34_security.parquet"
    )


def test_raw_then_clean_round_trip(tmp_path):
    """pull_s34 saves raw tables; clean_s34 turns them into the S34 files."""
    db = FakeDB()
    pull_s34(db, [RDATE], raw_dir=tmp_path / "raw" / "s34")
    names_path = tmp_path / "raw" / "crsp" / "stocknames_v2.parquet"
    names_path.parent.mkdir(parents=True)
    pull_names(db).to_parquet(names_path, index=False)
    raw = load_raw_s34(RDATE, "s34type3", data_dir=tmp_path)
    assert len(raw) == len(db.holdings)  # raw layer keeps every row

    out_dir = tmp_path / "clean" / "s34"
    clean_s34([RDATE], out_dir=out_dir, data_dir=tmp_path)
    hold, inv, log, _, security = _build(
        FakeDB(), cusip_map=None, include_security=True
    )
    expected = {"holdings": hold, "investors": inv, "log": log, "security": security}
    for kind, frame in expected.items():
        saved = pd.read_parquet(quarter_file(RDATE, kind, out_dir))
        pd.testing.assert_frame_equal(
            saved, frame.reset_index(drop=True), check_dtype=False
        )
    assert len(load_s34("holdings", data_dir=tmp_path)) == len(hold)
    assert raw_file(RDATE, "msf_v2", tmp_path).name == "msf_v2.parquet"


def test_clean_keeps_first_vintage_and_positive_shares(result):
    """Rows the raw layer keeps but cleaning must drop leave the result unchanged."""
    db = FakeDB()
    # A later vintage of manager 1's report, carried forward by TR
    db.managers = pd.concat(
        [
            db.managers,
            pd.DataFrame(
                [
                    {
                        "mgrno": 1.0,
                        "mgrname": "MANAGER ONE",
                        "typecode": 4.0,
                        "fdate": "2023-06-30",
                        "rdate": RDATE,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    extra = [
        {"mgrno": 1.0, "fdate": "2023-06-30", "cusip": "AAA11111", "shares": 999.0},
        {"mgrno": 1.0, "fdate": "2022-12-31", "cusip": "DDD44444", "shares": 0.0},
    ]
    db.holdings = pd.concat([db.holdings, pd.DataFrame(extra)], ignore_index=True)
    hold, inv, log, _ = _build(db)
    pd.testing.assert_frame_equal(hold, result[0])
    pd.testing.assert_frame_equal(inv, result[1])
    assert log.set_index("step").loc["1_download", "n_obs"] == 8


def test_cusip_must_be_valid_on_report_date():
    db = FakeDB()
    # This name record ended before the report date, so its CUSIP cannot link
    db.names = pd.concat(
        [
            db.names,
            pd.DataFrame([_common(70, 700, "OLD00000", nameenddt="2010-12-31")]),
        ],
        ignore_index=True,
    )
    db.holdings = pd.concat(
        [
            db.holdings,
            pd.DataFrame(
                [
                    {
                        "mgrno": 1.0,
                        "fdate": "2022-12-31",
                        "cusip": "OLD00000",
                        "shares": 10.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    hold, _, _, _ = _build(db)
    assert 700 not in set(hold["permco"])


def test_cusip_map_uses_historical_and_header_cusips():
    names = pd.DataFrame(
        {
            "permno": [1, 1, 2],
            "cusip": ["OLD", "NEW", "XYZ"],
            "hdrcusip": ["NEW", "NEW", None],
        }
    )
    got = set(map(tuple, cusip_map_from_names(names)[["cusip", "permno"]].to_numpy()))
    assert got == {("OLD", 1), ("NEW", 1), ("XYZ", 2)}


# Checks on saved outputs --------------------------------------------------

SAVED = sorted(S34_DIR.glob("*/holdings_s34.parquet"))
# Check at most the first, middle, and last saved quarter to keep tests quick.
SAMPLE = sorted({SAVED[0], SAVED[len(SAVED) // 2], SAVED[-1]}) if SAVED else []


@pytest.mark.skipif(not SAMPLE, reason="No saved S34 holdings yet")
@pytest.mark.parametrize("path", SAMPLE, ids=[p.parent.name for p in SAMPLE])
def test_saved_quarter(path):
    q = pd.Period(path.parent.name, freq="Q").end_time.normalize()
    hold = pd.read_parquet(path)
    inv = pd.read_parquet(path.with_name("investors_s34.parquet"))

    assert not hold.duplicated(["quarter", "investor_id", "permco"]).any()
    assert np.isfinite(hold["dollar_holding"]).all()
    assert (hold["dollar_holding"] > 0).all()
    assert hold["investor_id"].str.startswith("s34_").all()
    assert (hold["quarter"] == q).all()

    assert not inv.duplicated(["quarter", "investor_id"]).any()
    assert set(inv["investor_id"]) == set(hold["investor_id"])
    aum = hold.groupby("investor_id")["dollar_holding"].sum()
    assert np.allclose(inv.set_index("investor_id").loc[aum.index, "aum"], aum)

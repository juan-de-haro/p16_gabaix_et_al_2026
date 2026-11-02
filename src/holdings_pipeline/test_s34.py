"""Tests for pull_s34.py.

Unit tests run build_s34_quarter on a small fake WRDS connection, so they need
no network access. Output tests check the saved S34 files in DATA_DIR and are
skipped when those files do not exist yet.

    uv run pytest src/holdings_pipeline/test_s34.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from holdings_pipeline.pull_s34 import build_s34_quarter
from settings import config

RDATE = "2022-12-31"


def _common(permno, permco, cusip, sharetype="NS", usincflg="Y", issuertype="CORP"):
    return {
        "permno": permno, "permco": permco, "cusip": cusip, "sharetype": sharetype,
        "securitytype": "EQTY", "securitysubtype": "COM", "usincflg": usincflg,
        "issuertype": issuertype,
    }


class FakeDB:
    """Return canned tables for the four queries made by build_s34_quarter."""

    def __init__(self):
        self.managers = pd.DataFrame({
            "mgrno": [1.0, 2.0],
            "mgrname": ["MANAGER ONE", "MANAGER TWO"],
            "typecode": [4.0, np.nan],
            "fdate": ["2022-12-31", "2023-03-31"],
        })
        self.holdings = pd.DataFrame([
            # Direct CUSIP match
            {"mgrno": 1.0, "fdate": "2022-12-31", "cusip": "AAA11111", "shares": 100.0},
            # Only matches through a historical CUSIP (the GE case); foreign issuer
            {"mgrno": 1.0, "fdate": "2022-12-31", "cusip": "BBB22222", "shares": 50.0},
            # ADR: matched, then removed by the common-stock filter
            {"mgrno": 1.0, "fdate": "2022-12-31", "cusip": "CCC33333", "shares": 10.0},
            # Later vintage with a 2-for-1 split between rdate and fdate
            {"mgrno": 2.0, "fdate": "2023-03-31", "cusip": "AAA11111", "shares": 200.0},
            # Two share classes of one firm
            {"mgrno": 2.0, "fdate": "2023-03-31", "cusip": "DDD44444", "shares": 10.0},
            {"mgrno": 2.0, "fdate": "2023-03-31", "cusip": "DDD55555", "shares": 20.0},
            # Unknown CUSIP
            {"mgrno": 2.0, "fdate": "2023-03-31", "cusip": "ZZZ99999", "shares": 5.0},
            # Ambiguous historical CUSIP: maps to two active securities
            {"mgrno": 2.0, "fdate": "2023-03-31", "cusip": "AMB00000", "shares": 5.0},
        ])
        self.names = pd.DataFrame([
            _common(10, 100, "AAA11111"),
            _common(20, 200, "BBBOLD00", usincflg="N"),
            _common(30, 300, "CCC33333", sharetype="AD"),
            _common(41, 400, "DDD44444"),
            _common(42, 400, "DDD55555"),
            _common(50, 500, "EEE11111"),
            _common(51, 510, "EEE22222"),
        ])
        self.msf = pd.DataFrame([
            {"permno": 10, "yyyymm": 202212, "mthprc": 10.0, "mthcumfacshr": 2.0},
            {"permno": 10, "yyyymm": 202303, "mthprc": 5.0, "mthcumfacshr": 1.0},
            {"permno": 20, "yyyymm": 202212, "mthprc": -5.0, "mthcumfacshr": 1.0},
            {"permno": 30, "yyyymm": 202212, "mthprc": 7.0, "mthcumfacshr": 1.0},
            {"permno": 41, "yyyymm": 202212, "mthprc": 2.0, "mthcumfacshr": 1.0},
            {"permno": 41, "yyyymm": 202303, "mthprc": 2.0, "mthcumfacshr": 1.0},
            {"permno": 42, "yyyymm": 202212, "mthprc": 3.0, "mthcumfacshr": 1.0},
            {"permno": 42, "yyyymm": 202303, "mthprc": 3.0, "mthcumfacshr": 1.0},
        ])

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
        for c in date_cols or []:
            df[c] = pd.to_datetime(df[c])
        return df


CUSIP_MAP = pd.DataFrame({
    "cusip": ["AAA11111", "BBB22222", "AMB00000", "AMB00000"],
    "permno": [10, 20, 50, 51],
})


@pytest.fixture(scope="module")
def result():
    return build_s34_quarter(FakeDB(), RDATE, CUSIP_MAP)


def test_holdings_values(result):
    hold, _, _, _ = result
    got = hold.set_index(["investor_id", "permco"])["dollar_holding"].to_dict()
    assert got == {
        ("s34_1", 100): 1000.0,  # 100 shares * 10
        ("s34_1", 200): 250.0,   # historical CUSIP, foreign issuer kept, abs(price)
        ("s34_2", 100): 1000.0,  # 200 shares halved for the split, * 10
        ("s34_2", 400): 80.0,    # 10 * 2 + 20 * 3, two classes summed
    }


def test_holdings_key_unique_and_positive(result):
    hold, _, _, _ = result
    assert not hold.duplicated(["quarter", "investor_id", "permco"]).any()
    assert np.isfinite(hold["dollar_holding"]).all()
    assert (hold["dollar_holding"] > 0).all()
    assert (hold["quarter"] == pd.Timestamp(RDATE)).all()


def test_excluded_positions(result):
    hold, _, _, _ = result
    assert 300 not in set(hold["permco"])            # ADR
    assert not set(hold["permco"]) & {500, 510}      # ambiguous CUSIP


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
    assert n["2_match_cusip"] == 6  # adds the historical-CUSIP match
    later = ["2_match_cusip", "3_common_stock", "4_price", "5_aggregate_permco"]
    assert n[later].is_monotonic_decreasing


# Checks on saved outputs --------------------------------------------------

S34_DIR = Path(config("DATA_DIR")) / "s34"
SAVED = sorted(S34_DIR.glob("holdings_*.pkl"))
# Check at most the first, middle, and last saved quarter to keep tests quick.
SAMPLE = sorted({SAVED[0], SAVED[len(SAVED) // 2], SAVED[-1]}) if SAVED else []


@pytest.mark.skipif(not SAMPLE, reason="No saved S34 holdings yet")
@pytest.mark.parametrize("path", SAMPLE, ids=lambda p: p.stem)
def test_saved_quarter(path):
    q = path.stem.replace("holdings_", "")
    hold = pd.read_pickle(path)
    inv = pd.read_pickle(S34_DIR / f"investors_{q}.pkl")

    assert not hold.duplicated(["quarter", "investor_id", "permco"]).any()
    assert np.isfinite(hold["dollar_holding"]).all()
    assert (hold["dollar_holding"] > 0).all()
    assert hold["investor_id"].str.startswith("s34_").all()
    assert (hold["quarter"] == pd.Timestamp(q)).all()

    assert not inv.duplicated(["quarter", "investor_id"]).any()
    assert set(inv["investor_id"]) == set(hold["investor_id"])
    aum = hold.groupby("investor_id")["dollar_holding"].sum()
    assert np.allclose(inv.set_index("investor_id").loc[aum.index, "aum"], aum)

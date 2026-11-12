"""Tests for clean_jkp.py.

Unit tests use a small synthetic JKP panel that covers the aggregation rules:
one gvkey with two share classes (Berkshire A/B), several gvkeys in one PERMCO
(Liberty Media tracking stocks), and the universe filters. Output tests check
the saved firm tables in DATA_DIR and are skipped when they do not exist yet.

    uv run pytest src/holdings_pipeline/test_jkp.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from holdings_pipeline.clean_jkp import build_firm_quarterly, build_jkp_full
from settings import config

Q = pd.Timestamp("2022-12-31")


def _row(permno, permco, gvkey, size_grp, me, be, **chars):
    base = {
        "at_gr1": 0.1,
        "gp_at": 0.2,
        "ret_12_1": 0.0,
        "div_at": 0.01,
        "beta_60m": 1.0,
    }
    base.update(chars)
    return {
        "permno": permno,
        "permco": permco,
        "gvkey": gvkey,
        "eom": Q,
        "size_grp": size_grp,
        "me": me,
        "book_equity": be,
        **base,
    }


@pytest.fixture(scope="module")
def jkp():
    rows = [
        _row(1, 7, "A", "mega", 1000.0, 50.0),
        # Berkshire-like: one gvkey, two classes, book equity repeated
        _row(2, 540, "B", "mega", 300.0, 200.0, beta_60m=1.0),
        _row(3, 540, "B", "large", 100.0, 200.0, beta_60m=3.0),
        # Liberty-like: three classes, two gvkeys
        _row(4, 543, "C1", "small", 20.0, 10.0),
        _row(5, 543, "C1", "micro", 10.0, 10.0),
        _row(6, 543, "C2", "small", 30.0, 15.0),
        # Only micro/nano: dropped
        _row(7, 700, "D", "micro", 5.0, 5.0),
        _row(8, 700, "D", "nano", 1.0, 5.0),
        # Negative book equity: dropped
        _row(9, 800, "E", "large", 50.0, -1.0),
        # Missing beta: dropped
        _row(10, 900, "F", "large", 50.0, 10.0, beta_60m=np.nan),
    ]
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def built(jkp):
    return build_firm_quarterly(jkp)


def test_universe(built):
    fq, _ = built
    assert set(fq["permco"]) == {7, 540, 543}
    assert not fq.duplicated(["quarter", "permco"]).any()
    assert fq["size_grp"].isin(["small", "large", "mega"]).all()


def test_aggregation(built):
    fq, _ = built
    fq = fq.set_index("permco")
    assert fq.loc[540, "me"] == 400.0
    assert fq.loc[540, "be"] == 200.0  # same gvkey: counted once
    assert fq.loc[543, "be"] == 25.0  # two gvkeys: 10 + 15
    assert fq.loc[543, "me"] == 60.0
    assert fq.loc[540, "beta_60m"] == pytest.approx((300 * 1 + 100 * 3) / 400)
    assert fq.loc[540, "size_grp"] == "mega"
    # log_be_me is winsorized at 1%/99%, so only the middle firm is unclipped
    assert fq.loc[543, "log_be_me"] == pytest.approx(np.log(25 / 60))
    raw = np.log(fq["be"] / fq["me"])
    assert fq["log_be_me"].between(raw.min(), raw.max()).all()
    assert np.allclose(fq["log_me"], np.log(fq["me"]))


def test_filter_log(built):
    _, log = built
    assert list(log.columns) == ["quarter", "step", "n_obs", "n_investors", "n_firms"]
    n = log.set_index("step")["n_firms"]
    assert n["5a_aggregate_permco"] == 6
    assert n["5b_drop_micro_nano"] == 5
    assert n["5c_drop_nonpositive_be"] == 4
    assert n["5d_drop_missing_chars"] == 3
    assert n.is_monotonic_decreasing


def test_jkp_full(jkp, built):
    fq, _ = built
    df = jkp[["permco", "eom", "me", "at_gr1"]].assign(market_equity=jkp["me"])
    full = build_jkp_full(df, fq, ["at_gr1", "market_equity"])
    assert list(full.columns) == ["quarter", "permco", "at_gr1", "market_equity"]
    assert set(zip(full["quarter"], full["permco"])) == set(
        zip(fq["quarter"], fq["permco"])
    )
    assert full.set_index("permco").loc[540, "market_equity"] == 400.0


# Checks on saved outputs --------------------------------------------------

DATA_DIR = Path(config("DATA_DIR"))
FQ_PATH = DATA_DIR / "firm_quarterly.pkl"
FULL_PATH = DATA_DIR / "firm_quarterly_jkp_full.pkl"
REQ = ["me", "be", "log_be_me", "at_gr1", "gp_at", "ret_12_1", "div_at", "beta_60m"]


@pytest.mark.skipif(not FQ_PATH.exists(), reason="No saved firm_quarterly yet")
def test_saved_firm_quarterly():
    fq = pd.read_pickle(FQ_PATH)
    assert not fq.duplicated(["quarter", "permco"]).any()
    assert fq[REQ].notna().all().all()
    assert (fq["me"] > 0).all() and (fq["be"] > 0).all()
    assert fq["size_grp"].isin(["small", "large", "mega"]).all()
    assert pd.api.types.is_datetime64_any_dtype(fq["quarter"])
    assert fq["quarter"].dt.is_quarter_end.all()


@pytest.mark.skipif(
    not (FQ_PATH.exists() and FULL_PATH.exists()), reason="No saved JKP tables yet"
)
def test_saved_jkp_full_matches_universe():
    fq = pd.read_pickle(FQ_PATH)
    full = pd.read_pickle(FULL_PATH)
    assert not full.duplicated(["quarter", "permco"]).any()
    keys_fq = set(zip(fq["quarter"], fq["permco"]))
    keys_full = set(zip(full["quarter"], full["permco"]))
    assert keys_fq == keys_full

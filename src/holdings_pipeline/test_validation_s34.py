"""Data validation for the S34 filter log.

For every quarter, checks that row counts never increase across the main
filter steps and that each step keeps at least a minimum share of the
previous step's rows.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from settings import config

LOG_PATH = Path(config("DATA_DIR")) / "filter_log_s34.pkl"

MAIN_STEPS = [
    "1_download",
    "2_match_cusip",
    "3_common_stock",
    "4_price",
    "5_aggregate_permco",
]

# Minimum share of rows each step keeps relative to the previous step.
# Set from what each step does, before computing the ratios.
MIN_KEEP = {
    # 13F securities are US-listed, so nearly all should match CRSP;
    # a large drop would signal a CUSIP or date-matching bug.
    "2_match_cusip": 0.90,
    # Removes ETFs, ADRs, funds and other non-common shares. This share
    # likely grows over time with ETF holdings, so the bound is wide.
    "3_common_stock": 0.50,
    # Only drops positions without a valid price or on non-major exchanges.
    "4_price": 0.99,
    # Only merges share classes of the same company held by the same manager.
    "5_aggregate_permco": 0.95,
}

pytestmark = pytest.mark.skipif(
    not LOG_PATH.exists(), reason="requires the S34 filter log built from WRDS data"
)


@pytest.fixture(scope="module")
def counts_by_step():
    """Return a quarter x step table of row counts for the main steps."""
    log = pd.read_pickle(LOG_PATH)
    return log[log["step"].isin(MAIN_STEPS)].pivot(
        index="quarter", columns="step", values="n_obs"
    )[MAIN_STEPS]


def test_every_quarter_has_all_main_steps(counts_by_step):
    assert counts_by_step.notna().all().all()


def test_row_counts_never_increase(counts_by_step):
    increases = counts_by_step.diff(axis=1).iloc[:, 1:] > 0
    assert not increases.any().any(), counts_by_step[increases.any(axis=1)]


@pytest.mark.parametrize("step, min_keep", MIN_KEEP.items())
def test_step_keeps_minimum_share(counts_by_step, step, min_keep):
    prev = MAIN_STEPS[MAIN_STEPS.index(step) - 1]
    keep = counts_by_step[step] / counts_by_step[prev]
    assert (keep >= min_keep).all(), keep[keep < min_keep]

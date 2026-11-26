"""Data validation for the JKP firm filter log.

For every quarter, checks that the firm table has one row per firm, that firm
counts never increase across the filter steps that each step keeps a
plausible share of the previous step's firms, and that the final firm count
is in a plausible range.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from settings import config

LOG_PATH = Path(config("DATA_DIR")) / "filter_log_firms.pkl"

STEPS = [
    "5a_aggregate_permco",
    "5b_drop_micro_nano",
    "5c_drop_nonpositive_be",
    "5d_drop_missing_chars",
]

# Plausible share of firms each step keeps relative to the previous step,
# as (lower, upper). Set from what each step does, not from observed ratios.
KEEP_RANGE = {
    # Micro and nano caps (below the NYSE 20th size percentile) are roughly
    # 60% of US stocks by count (Fama and French, 2008). The upper bound
    # catches a size filter that silently removes nothing.
    "5b_drop_micro_nano": (0.25, 0.75),
    # Negative book equity is uncommon outside micro caps, but not rare
    # (for example, firms with large buybacks).
    "5c_drop_nonpositive_be": (0.85, 1.00),
    # Firms with short return histories lack beta_60m, and new firms lack
    # asset growth, so this step can drop a noticeable share.
    "5d_drop_missing_chars": (0.60, 1.00),
}

# The paper's final sample has roughly 1,500-1,800 firms per quarter after all
# filters (Figure 2). Before the holdings filters, the count should be at
# least that large, but well below the number of non-micro US firms.
FINAL_FIRMS_RANGE = (1_500, 3_500)

pytestmark = pytest.mark.skipif(
    not LOG_PATH.exists(), reason="requires the JKP filter log built from WRDS data"
)


@pytest.fixture(scope="module")
def filter_log():
    """Return the JKP filter log restricted to the firm-level steps."""
    log = pd.read_pickle(LOG_PATH)
    return log[log["step"].isin(STEPS)]


@pytest.fixture(scope="module")
def firms_by_step(filter_log):
    """Return a quarter x step table of firm counts."""
    return filter_log.pivot(index="quarter", columns="step", values="n_firms")[STEPS]


def test_every_quarter_has_all_steps(firms_by_step):
    assert firms_by_step.notna().all().all()


def test_one_row_per_firm(filter_log):
    assert (filter_log["n_obs"] == filter_log["n_firms"]).all()


def test_firm_counts_never_increase(firms_by_step):
    increases = firms_by_step.diff(axis=1).iloc[:, 1:] > 0
    assert not increases.any().any(), firms_by_step[increases.any(axis=1)]


@pytest.mark.parametrize("step, bounds", KEEP_RANGE.items())
def test_step_keeps_plausible_share(firms_by_step, step, bounds):
    prev = STEPS[STEPS.index(step) - 1]
    keep = firms_by_step[step] / firms_by_step[prev]
    low, high = bounds
    assert keep.between(low, high).all(), keep[~keep.between(low, high)]


def test_final_firm_count_in_plausible_range(firms_by_step):
    low, high = FINAL_FIRMS_RANGE
    final = firms_by_step[STEPS[-1]]
    assert final.between(low, high).all(), final[~final.between(low, high)]

"""Tests for report_s34_coverage.py on small hand-made holdings (no data needed)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_s34_coverage import (  # noqa: E402
    LABELS,
    annual_coverage,
    coverage_figure,
    figure_note,
    filter_table,
    gap_summary,
    sample_filters,
    to_latex,
    to_markdown,
)


def _h(rows):
    return pd.DataFrame(rows, columns=["investor_id", "permco", "dollar_holding"])


def test_universe_and_concentration():
    h = _h(
        [
            ("a", 1, 50.0),
            ("a", 2, 50.0),
            ("b", 1, 50.0),
            ("b", 2, 50.0),
            ("d", 1, 80.0),  # d has 80% in one firm: concentrated
            ("d", 2, 20.0),
            ("a", 9, 10.0),  # firm 9 is outside the universe
        ]
    )
    final, steps = sample_filters(h, universe={1, 2}, n_min=2)
    assert [s["n_obs"] for s in steps] == [6, 4, 4]
    assert set(final["investor_id"]) == {"a", "b"}


def test_concentration_uses_weights_within_the_universe():
    # Outside the universe, e looks diversified; inside, 100% is in firm 1
    h = _h([("e", 1, 30.0), ("e", 9, 70.0), ("a", 1, 50.0), ("a", 2, 50.0)])
    _, steps = sample_filters(h, universe={1, 2}, n_min=1)
    assert steps[1]["n_investors"] == 1


def test_filter_iterates_until_stable():
    # n_min = 2. Firm 3 has one holder, so it goes first; c then holds only
    # firm 2 and goes in the second pass; a and b with firms 1-2 remain.
    h = _h(
        [
            ("a", 1, 1.0),
            ("a", 2, 1.0),
            ("b", 1, 1.0),
            ("b", 2, 1.0),
            ("c", 2, 1.0),
            ("c", 3, 1.0),
        ]
    )
    final, steps = sample_filters(h, universe={1, 2, 3}, n_min=2, w_max=1.0)
    assert steps[-1] == {"step": "c3", "n_obs": 4, "n_investors": 2, "n_firms": 2}
    assert set(final["investor_id"]) == {"a", "b"}


def test_duplicate_positions_raise():
    h = _h([("a", 1, 1.0), ("a", 1, 2.0)])
    with pytest.raises(ValueError, match="one row per investor"):
        sample_filters(h, universe={1})


def _counts():
    """Two quarters of counts with every step in LABELS."""
    rows = []
    for i, q in enumerate(pd.to_datetime(["2022-09-30", "2022-12-31"])):
        order = 0
        for panel, steps in LABELS.items():
            for step, label in steps.items():
                n = 1000 - 10 * order + i
                rows.append(
                    {
                        "quarter": q,
                        "panel": panel,
                        "step": step,
                        "label": label,
                        "order": order,
                        "n_obs": float("nan") if panel == "B" else n,
                        "n_investors": float("nan") if panel == "B" else n // 10,
                        "n_firms": n // 2,
                    }
                )
                order += 1
    return pd.DataFrame(rows)


def test_table_reports_quarter_and_average():
    counts = _counts()
    t = filter_table(counts, quarter=pd.Timestamp("2022-12-31"))
    assert len(t) == sum(len(s) for s in LABELS.values())
    first = t.iloc[0]
    assert first.q_n_obs == 1001
    assert first.avg_n_obs == pytest.approx(1000.5)


def test_outputs_mention_every_step_and_the_note():
    counts = _counts()
    q = pd.Timestamp("2022-12-31")
    t = filter_table(counts, quarter=q)
    tex, md = to_latex(t, counts, q), to_markdown(t, counts, q)
    for steps in LABELS.values():
        for label in steps.values():
            assert label in md
    assert r"20$\times$20" in tex and "Note." in tex
    assert "---" in tex  # Panel B has no holdings or investors


def test_averages_skip_quarters_missing_a_step():
    counts = _counts()
    q3, q4 = pd.Timestamp("2022-09-30"), pd.Timestamp("2022-12-31")
    # Older code version: 2022Q3 lacks the alias step
    counts = counts[~((counts["quarter"] == q3) & (counts["step"] == "2b"))]
    t = filter_table(counts, quarter=q4)
    # Averages now use 2022Q4 only, so they equal the 2022Q4 column
    assert (t["avg_n_firms"] == t["q_n_firms"]).all()
    assert "excluding 1 quarter whose logs lack a step" in to_markdown(t, counts, q4)
    with pytest.raises(ValueError, match="does not log every step"):
        filter_table(counts, quarter=q3)


def _gap_counts():
    """Quarterly final-sample and JKP firm counts, 2010-2018; 150 dropped in the gap."""
    rows = []
    for q in pd.date_range("2010-03-31", "2018-12-31", freq="QE"):
        in_gap = pd.Timestamp("2011-03-31") <= q <= pd.Timestamp("2017-09-30")
        final = 1600 - (150 if in_gap else 10)
        rows += [
            {
                "quarter": q,
                "panel": "B",
                "step": "5d",
                "n_obs": float("nan"),
                "n_investors": float("nan"),
                "n_firms": 1600,
            },
            {
                "quarter": q,
                "panel": "C",
                "step": "c3",
                "n_obs": 600_000,
                "n_investors": 3000,
                "n_firms": final,
            },
        ]
    return pd.DataFrame(rows)


def test_gap_summary():
    lo, hi, outside = gap_summary(_gap_counts())
    assert (lo, hi, outside) == (150, 150, 10)


def test_annual_coverage_and_figure():
    annual = annual_coverage(_gap_counts())
    assert list(annual.index) == list(range(2010, 2019))
    assert annual.loc[2010, "n_firms"] == pytest.approx(
        1590
    )  # all four outside the gap
    assert annual.loc[2011, "n_firms"] == pytest.approx(1450)  # all in the gap
    # 2017Q1-Q3 are in the gap, 2017Q4 is not
    assert annual.loc[2017, "n_firms"] == pytest.approx((3 * 1450 + 1590) / 4)
    paper = pd.DataFrame(
        {
            "year": [2010, 2011],
            "firms": [1571, 1565],
            "investors": [5712, 5939],
            "holdings_thousands": [673, 706],
        }
    )
    fig = coverage_figure(annual, paper)
    assert len(fig.axes) == 3


def test_figure_note_reports_the_gap():
    counts = _gap_counts()
    order = {k: i for i, k in enumerate((p, st) for p in LABELS for st in LABELS[p])}
    counts["order"] = [order[k] for k in zip(counts.panel, counts.step, strict=True)]
    # The note's sample period needs every table step in each quarter
    filler = [
        {
            "quarter": q,
            "panel": p,
            "step": st,
            "order": i,
            "n_obs": 1.0,
            "n_investors": 1.0,
            "n_firms": 1.0,
        }
        for q in counts["quarter"].unique()
        for (p, st), i in order.items()
        if (p, st) not in {("B", "5d"), ("C", "c3")}
    ]
    note = figure_note(pd.concat([counts, pd.DataFrame(filler)]))
    assert "drop 150-150 JKP firms per quarter, against a median of 10" in note
    assert "Sample: 2010Q1–2018Q4" in note

import numpy as np
import pandas as pd
import pytest

from valuation.managed_portfolio import compare_models, prepare_cases, score_candidates


def holdings():
    return pd.DataFrame(
        [
            ("2022-12-31", f"fund_{i:02}", p, v)
            for i in range(20)
            for p, v in [(1, 90), (2, 40), (3, 10)]
        ],
        columns=["quarter", "investor_id", "permco", "dollar_holding"],
    )


def predictions(targets, probability=0.5):
    rows = []
    for t in targets.itertuples(index=False):
        rows.extend(
            [
                (t.quarter, t.investor_id, 2, probability),
                (t.quarter, t.investor_id, 4, 1 - probability),
            ]
        )
    return pd.DataFrame(
        rows, columns=["quarter", "investor_id", "permco", "probability"]
    )


def universe():
    return pd.DataFrame({"quarter": ["2022-12-31"] * 4, "permco": [1, 2, 3, 4]})


def test_reproducible_split_mask_and_uniform_score():
    context, targets, split = prepare_cases(holdings())
    _, targets2, split2 = prepare_cases(holdings().sample(frac=1, random_state=9))
    pd.testing.assert_frame_equal(split, split2)
    pd.testing.assert_frame_equal(targets, targets2)
    assert split.split.value_counts().to_dict() == {"train": 18, "test": 2}
    assert targets.target_permco.eq(2).all()
    assert not context.loc[context.split.eq("test"), "permco"].eq(2).any()
    assert set(context.columns) == {
        "quarter",
        "investor_id",
        "permco",
        "split",
        "context_rank",
    }
    result, _ = score_candidates(targets, predictions(targets), context, universe())
    assert result["higher_is_better_candidate_normalization"] == pytest.approx(0)
    assert result["printed_equation_candidate_normalization"] == pytest.approx(0)
    result, _ = score_candidates(
        targets, predictions(targets, 0.9), context, universe()
    )
    assert result["higher_is_better_candidate_normalization"] == pytest.approx(
        1 + np.log(0.9) / np.log(2)
    )
    assert result["printed_equation_candidate_normalization"] < 0


def test_reject_leakage_bad_mass_duplicates_and_missing_targets():
    context, targets, _ = prepare_cases(holdings())
    p = predictions(targets)
    bad = p.copy()
    bad.loc[0, "permco"] = 1
    with pytest.raises(ValueError, match="context"):
        score_candidates(targets, bad, context, universe())
    with pytest.raises(ValueError, match="mass"):
        score_candidates(targets, p.assign(probability=0.9), context, universe())
    with pytest.raises(ValueError, match="Duplicate"):
        score_candidates(targets, pd.concat([p, p.iloc[[0]]]), context, universe())
    with pytest.raises(ValueError, match="strictly positive"):
        score_candidates(targets, predictions(targets, 0), context, universe())
    with pytest.raises(ValueError, match="exactly"):
        score_candidates(targets, p.iloc[:2], context, universe())


def test_holdings_validate_keys_and_ties():
    h = holdings()
    with pytest.raises(ValueError, match="unique"):
        prepare_cases(pd.concat([h, h.iloc[[0]]]))
    h.loc[h.permco.eq(2), "dollar_holding"] = 90
    _, t, _ = prepare_cases(h)
    assert t.target_permco.eq(2).all()  # deterministic permco tie-break


def test_reject_incomplete_and_replacement_candidate_sets():
    context, targets, _ = prepare_cases(holdings())
    p = predictions(targets)
    missing = p.drop(p.index[1]).copy()
    missing.loc[missing.investor_id.eq(targets.iloc[0].investor_id), "probability"] = 1
    with pytest.raises(ValueError, match="incomplete"):
        score_candidates(targets, missing, context, universe())
    wrong = p.copy()
    wrong.loc[wrong.index[1], "permco"] = 99
    with pytest.raises(ValueError, match="outside the universe"):
        score_candidates(targets, wrong, context, universe())
    with pytest.raises(ValueError, match="Duplicate universe"):
        score_candidates(
            targets, p, context, pd.concat([universe(), universe().iloc[[0]]])
        )
    missing_observation = context.loc[
        ~((context.split == "test") & (context.permco == 1))
    ]
    with pytest.raises(ValueError, match="incomplete"):
        score_candidates(targets, p, missing_observation, universe())


def test_compare_models_and_variable_candidate_counts():
    h = holdings()
    h = pd.concat(
        [h, pd.DataFrame([("2022-12-31", "fund_00", 4, 5)], columns=h.columns)]
    )
    # Select a split with fund_00 in test so its candidate count differs.
    for seed in range(100):
        context, targets, _ = prepare_cases(h, seed=seed)
        if "fund_00" in targets.investor_id.tolist():
            break
    u = pd.DataFrame({"quarter": ["2022-12-31"] * 5, "permco": [1, 2, 3, 4, 5]})
    u["quarter"] = pd.to_datetime(u.quarter)
    cases = targets[KEY_COLS].merge(u, on="quarter")
    obs = context.loc[context.split.eq("test"), KEY_COLS + ["permco"]]
    p = cases.merge(obs.assign(observed=True), how="left")
    p = p.loc[p.observed.isna()].drop(columns="observed")
    p["probability"] = 1 / p.groupby(KEY_COLS).permco.transform("size")
    table, audit = compare_models(
        targets, {"uniform": p, "same": p.sample(frac=1)}, context, u
    )
    assert audit.n_candidates.nunique() == 2
    assert (table.asmp_2024.abs() < 1e-12).all()
    assert table.case_universe_sha256.nunique() == 1
    weighted = p.copy()
    case = targets.iloc[0].investor_id
    mask = weighted.investor_id.eq(case)
    count = int(mask.sum())
    weighted.loc[mask & weighted.permco.eq(2), "probability"] = 0.8
    weighted.loc[mask & weighted.permco.ne(2), "probability"] = 0.2 / (count - 1)
    metrics, a = score_candidates(targets, weighted, context, u)
    expected = 1 + np.log(a.probability).sum() / np.log(a.n_candidates).sum()
    assert metrics["asmp_2024"] == pytest.approx(expected)
    bad = p.copy()
    bad.loc[bad.index[0], "permco"] = 99
    with pytest.raises(ValueError, match="outside the universe"):
        compare_models(targets, {"good": p, "bad": bad}, context, u)


KEY_COLS = ["quarter", "investor_id"]

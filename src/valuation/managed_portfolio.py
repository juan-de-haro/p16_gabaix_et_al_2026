"""Prepare masked holdings cases and score model probabilities, not train models.

This is a pilot framework; see design/managed_portfolio_benchmark.md for the
2024/2026 paper versions and requirements for comparable model inputs.
"""

import hashlib
import json

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

KEY = ["quarter", "investor_id"]


def prepare_cases(holdings, seed=32800):
    """Split investors once and mask the second-largest positive holding.

    Return contexts, targets, and split. Test targets must not be sent to models.
    Test contexts omit the masked company and all weights; models receive only
    identities/ranks and must insert the mask token at the specified position.
    """
    required = KEY + ["permco", "dollar_holding"]
    missing = set(required) - set(holdings)
    if missing:
        raise ValueError(f"Missing holdings columns: {sorted(missing)}")
    frame = holdings[required].copy()
    frame["quarter"] = pd.to_datetime(frame.quarter, errors="raise")
    if frame.empty or frame[KEY + ["permco"]].isna().any().any():
        raise ValueError("Holdings and keys must be nonempty and non-null")
    if not frame.quarter.dt.is_quarter_end.all():
        raise ValueError("quarter must be a quarter-end date")
    frame["investor_id"] = frame.investor_id.astype(str)
    if frame.investor_id.str.strip().eq("").any():
        raise ValueError("investor_id must not be blank")
    ids = pd.to_numeric(frame.permco, errors="raise")
    if not np.isfinite(ids).all() or (ids <= 0).any() or (ids % 1 != 0).any():
        raise ValueError("permco must contain positive integer identifiers")
    frame["permco"] = ids.astype("int64")
    frame["dollar_holding"] = pd.to_numeric(frame.dollar_holding, errors="raise")
    if not np.isfinite(frame.dollar_holding).all() or (frame.dollar_holding <= 0).any():
        raise ValueError("Holdings must have positive finite dollar values")
    if frame.duplicated(KEY + ["permco"]).any():
        raise ValueError("Holdings keys must be unique")
    if frame.quarter.nunique() != 1:
        raise ValueError("Prepare one quarter at a time")
    frame = frame.sort_values(
        KEY + ["dollar_holding", "permco"], ascending=[True, True, False, True]
    )
    frame["rank"] = frame.groupby(KEY).cumcount() + 1
    eligible = frame.groupby(KEY).size()
    if (eligible < 2).any():
        raise ValueError("Every investor must have at least two holdings")
    investors = sorted(frame.investor_id.unique())
    if len(investors) < 2:
        raise ValueError("At least two investors required for train/test split")
    _, test = train_test_split(investors, test_size=0.1, random_state=seed)
    split = pd.DataFrame({"investor_id": investors})
    split["split"] = np.where(split.investor_id.isin(test), "test", "train")
    split.insert(0, "quarter", frame.quarter.iloc[0])
    frame = frame.merge(split, on=KEY, validate="many_to_one")
    targets = frame.loc[
        frame.split.eq("test") & frame["rank"].eq(2), KEY + ["permco"]
    ].rename(columns={"permco": "target_permco"})
    # Never expose original rank/weights: rank gap or weights can reveal target.
    context = frame.loc[
        ~(frame.split.eq("test") & frame["rank"].eq(2)), KEY + ["permco", "split"]
    ].copy()
    context["context_rank"] = context.groupby(KEY).cumcount() + 1
    return context.reset_index(drop=True), targets.reset_index(drop=True), split


def score_candidates(targets, predictions, context, universe):
    """Score a complete candidate probability distribution per test investor.

    predictions: quarter, investor_id, permco, probability; one row for each
    allowed candidate. Context is the output of prepare_cases; universe has
    quarter and permco. Zero target probabilities are an
    error rather than silently clipping or changing the reported log score.
    """
    target_cols = KEY + ["target_permco"]
    pred_cols = KEY + ["permco", "probability"]
    if set(target_cols) - set(targets) or set(pred_cols) - set(predictions):
        raise ValueError("Missing target or prediction columns")
    t, p = targets[target_cols].copy(), predictions[pred_cols].copy()
    for df in (t, p):
        df["quarter"] = pd.to_datetime(df.quarter, errors="raise")
        if df.empty or df[KEY].isna().any().any():
            raise ValueError("Keys and tables must be nonempty")
        df["investor_id"] = df.investor_id.astype(str)
    for df, col in ((t, "target_permco"), (p, "permco")):
        ids = pd.to_numeric(df[col], errors="raise")
        if (
            ids.isna().any()
            or not np.isfinite(ids).all()
            or (ids <= 0).any()
            or (ids % 1 != 0).any()
        ):
            raise ValueError("Company identifiers must be positive integers")
        df[col] = ids.astype("int64")
    if t.duplicated(KEY).any() or p.duplicated(KEY + ["permco"]).any():
        raise ValueError("Duplicate target or candidate keys")
    if set(KEY + ["permco", "split"]) - set(context):
        raise ValueError("Context requires quarter, investor_id, permco, split")
    if context.split.isna().any() or not context.split.isin(["train", "test"]).all():
        raise ValueError("Context split must be train or test")
    if set(["quarter", "permco"]) - set(universe):
        raise ValueError("Universe requires quarter and permco")
    u = universe[["quarter", "permco"]].copy()
    u["quarter"] = pd.to_datetime(u.quarter, errors="raise")
    uid = pd.to_numeric(u.permco, errors="raise")
    if (
        u.empty
        or u.quarter.isna().any()
        or not u.quarter.dt.is_quarter_end.all()
        or uid.isna().any()
        or not np.isfinite(uid).all()
        or (uid <= 0).any()
        or (uid % 1 != 0).any()
    ):
        raise ValueError("Universe requires valid quarter-end and positive firm IDs")
    u["permco"] = uid.astype("int64")
    if u.duplicated(["quarter", "permco"]).any():
        raise ValueError("Duplicate universe keys")
    if t.quarter.nunique() != 1 or u.quarter.nunique() != 1:
        raise ValueError("Score one quarter and one universe at a time")
    if t.quarter.iloc[0] != u.quarter.iloc[0]:
        raise ValueError("Universe and targets must have the same quarter")
    observed = context.loc[context.split.eq("test"), KEY + ["permco"]].copy()
    observed["quarter"] = pd.to_datetime(observed.quarter, errors="raise")
    observed["investor_id"] = observed.investor_id.astype(str)
    if (
        observed[KEY + ["permco"]].isna().any().any()
        or observed.duplicated(KEY + ["permco"]).any()
    ):
        raise ValueError("Invalid or duplicate context keys")
    obs_ids = pd.to_numeric(observed.permco, errors="raise")
    if (
        not np.isfinite(obs_ids).all()
        or (obs_ids <= 0).any()
        or (obs_ids % 1 != 0).any()
    ):
        raise ValueError("Context company IDs must be positive integers")
    observed["permco"] = obs_ids.astype("int64")
    if observed.duplicated(KEY + ["permco"]).any():
        raise ValueError("Duplicate context keys after normalization")
    if len(observed[KEY].drop_duplicates()) != len(t):
        raise ValueError("Context must cover exactly the target cases")
    if len(observed.merge(u, on=["quarter", "permco"])) != len(observed):
        raise ValueError("Observed context contains firms outside the universe")
    target_universe = t.rename(columns={"target_permco": "permco"})
    if len(target_universe.merge(u, on=["quarter", "permco"])) != len(t):
        raise ValueError("Target contains firms outside the universe")
    if len(p.merge(u, on=["quarter", "permco"])) != len(p):
        raise ValueError("Prediction contains firms outside the universe")
    if len(target_universe.merge(observed, on=KEY + ["permco"])):
        raise ValueError("Target appears in observed context")
    if p.merge(observed, on=KEY + ["permco"]).shape[0]:
        raise ValueError("Candidate set contains an observed context holding")
    if len(observed[KEY].drop_duplicates().merge(t, on=KEY)) != len(t):
        raise ValueError("Each target case requires observed test context")
    if not t.quarter.dt.is_quarter_end.all() or not p.quarter.dt.is_quarter_end.all():
        raise ValueError("quarter must be a quarter-end date")
    p["probability"] = pd.to_numeric(p.probability, errors="raise")
    probs = p.probability.to_numpy(dtype=float)
    if not np.isfinite(probs).all() or (probs < 0).any() or (probs > 1).any():
        raise ValueError("Probabilities must be finite and between zero and one")
    groups = p.groupby(KEY, as_index=False).agg(
        n_candidates=("permco", "size"), mass=("probability", "sum")
    )
    obs_counts = (
        observed.groupby(KEY, as_index=False)
        .size()
        .rename(columns={"size": "n_observed"})
    )
    groups = groups.merge(obs_counts, on=KEY, how="left", validate="one_to_one")
    expected = len(u) - groups.n_observed
    if groups.n_observed.isna().any() or not groups.n_candidates.eq(expected).all():
        raise ValueError(
            "Candidate set is incomplete for the supplied universe and context"
        )
    if (groups.n_candidates < 2).any() or not np.allclose(
        groups.mass, 1, rtol=0, atol=1e-8
    ):
        raise ValueError(
            "Need at least two candidates and probability mass one per case"
        )
    if len(groups) != len(t) or len(groups.merge(t, on=KEY)) != len(t):
        raise ValueError("Predictions must cover exactly the target cases")
    joined = t.merge(
        p,
        left_on=KEY + ["target_permco"],
        right_on=KEY + ["permco"],
        how="left",
        validate="one_to_one",
    )
    if joined.probability.isna().any() or (joined.probability <= 0).any():
        raise ValueError(
            "Every target must have a strictly positive predicted probability"
        )
    joined = joined.merge(groups, on=KEY, validate="one_to_one")
    joined["log_probability"] = np.log(joined.probability)
    joined["log_uniform"] = -np.log(joined.n_candidates)
    # Report both signs: the printed equation and intuitive higher-is-better.
    ratio = joined.log_probability.sum() / joined.log_uniform.sum()
    return {
        "n_cases": len(joined),
        "mean_log_probability": float(joined.log_probability.mean()),
        "mean_log_uniform": float(joined.log_uniform.mean()),
        "printed_equation_candidate_normalization": float(ratio - 1),
        "higher_is_better_candidate_normalization": float(1 - ratio),
        "asmp_2024": float(1 - ratio),
        "status": "provisional_candidate_normalization_not_paper_verified",
    }, joined


def compare_models(targets, predictions_by_model, context, universe):
    """Strictly score model distributions against the same external case universe."""
    if not isinstance(predictions_by_model, dict) or not predictions_by_model:
        raise ValueError("Provide a nonempty mapping from model names to predictions")
    if any(
        not isinstance(name, str) or not name.strip() for name in predictions_by_model
    ):
        raise ValueError("Model names must be nonempty strings")
    results, audits = [], []
    for name in sorted(predictions_by_model):
        metrics, audit = score_candidates(
            targets, predictions_by_model[name], context, universe
        )
        results.append({"model": name, **metrics})
        audits.append(audit.assign(model=name))
    # The identical external universe and context determine candidate identities,
    # not just candidate counts, for every individually validated model.
    canonical = pd.concat(
        [
            universe[["quarter", "permco"]]
            .astype(str)
            .sort_values(["quarter", "permco"])
            .assign(kind="universe"),
            context.loc[context.split.eq("test"), KEY + ["permco"]]
            .astype(str)
            .sort_values(KEY + ["permco"])
            .assign(kind="context"),
            targets[KEY + ["target_permco"]]
            .astype(str)
            .sort_values(KEY)
            .rename(columns={"target_permco": "permco"})
            .assign(kind="target"),
        ],
        ignore_index=True,
    ).fillna("")
    records = canonical.to_dict("records")
    digest = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    table = pd.DataFrame(results)
    table["case_universe_sha256"] = digest
    return table, pd.concat(audits, ignore_index=True)

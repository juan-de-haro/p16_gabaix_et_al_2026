"""2022Q4 relative valuation pilot, based on the course's 2026 paper.

Run from the repository root after building the provisional company table:
    python src/valuation/relative_valuation.py
    python src/valuation/relative_valuation.py --embeddings /path/to/embeddings.parquet

The optional embedding table must have quarter, permco and numeric emb_* columns.
By default it must cover every firm. An explicit --allow-partial-embeddings
restricts all models to the audited intersection before clipping, residualizing,
and splitting. --firm-table selects an alternative company table.
Results and the firm-level split/predictions are written to
_output/relative_valuation/2022Q4/. The input sample is provisional: S12/S34
economic overlap, final joint filters and final sample winsorization are pending.

Paper procedure: estimate log(me) on log(be) across the whole quarter, then
explain its residual using an 80/20 firm split and ridge with 10-fold training
CV. Score with equation (15), 1 - Var(test errors) / Var(test residuals).
The initial valuation regression and cross-sectional winsorization use the whole
quarter, so the test firms influence target construction; this is a cross-sectional
paper-style evaluation, not a fully isolated or forward-looking forecast.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import r2_score
from sklearn.model_selection import GridSearchCV, KFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from settings import config


CHARS = ["beta_60m", "at_gr1", "gp_at", "div_at"]
KEY = ["quarter", "permco"]
ALPHAS = np.logspace(-4, 4, 25)


def prepare_inputs(firms, quarter="2022Q4", embeddings=None, allow_partial_embeddings=False):
    """Validate the firm universe and apply the paper's cross-sectional clips."""
    qdate = pd.Period(quarter, freq="Q").end_time.normalize()
    required = KEY + ["me", "be"] + CHARS
    missing = set(required) - set(firms)
    if missing:
        raise ValueError(f"Firm table missing columns: {sorted(missing)}")
    frame = firms[required].copy()
    frame["quarter"] = pd.to_datetime(frame.quarter)
    if frame.empty or not frame.quarter.eq(qdate).all():
        raise ValueError(f"Expected nonempty {quarter} firm table only")
    if frame[KEY].isna().any().any() or frame.duplicated(KEY).any():
        raise ValueError("Firm keys must be non-null and unique")
    frame["permco"] = pd.to_numeric(frame.permco, errors="raise").astype("int64")
    for col in ["me", "be"] + CHARS:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if not np.isfinite(frame[["me", "be"] + CHARS].to_numpy(dtype=float)).all():
        raise ValueError("Required firm values must be finite")
    if not frame[["me", "be"]].gt(0).all().all():
        raise ValueError("Market and book equity must be positive")
    frame = frame.sort_values(KEY).reset_index(drop=True)
    embedding_cols = []
    coverage = {"n_firms_before_embedding_match": len(frame),
                "n_firms_excluded": 0, "excluded_permcos": [],
                "n_extra_embedding_firms": 0, "partial_coverage_allowed": False}
    if embeddings is not None:
        embedding_cols = sorted(c for c in embeddings if c.startswith("emb_"))
        if not embedding_cols or set(KEY) - set(embeddings):
            raise ValueError("Embeddings require quarter, permco, and emb_* columns")
        em = embeddings[KEY + embedding_cols].copy()
        em["quarter"] = pd.to_datetime(em.quarter)
        if em[KEY].isna().any().any() or em.duplicated(KEY).any():
            raise ValueError("Embedding keys must be non-null and unique")
        em["permco"] = pd.to_numeric(em.permco, errors="raise").astype("int64")
        em = em.loc[em.quarter.eq(qdate)]
        for col in embedding_cols:
            em[col] = pd.to_numeric(em[col], errors="raise")
        coverage["n_extra_embedding_firms"] = int((~em.permco.isin(frame.permco)).sum())
        frame = frame.merge(em, on=KEY, how="left", validate="one_to_one")
        valid = np.isfinite(frame[embedding_cols].to_numpy(dtype=float)).all(axis=1)
        excluded = frame.loc[~valid, "permco"].astype(int).tolist()
        coverage.update({"n_firms_excluded": len(excluded), "excluded_permcos": excluded,
                         "partial_coverage_allowed": bool(allow_partial_embeddings)})
        if excluded and not allow_partial_embeddings:
            raise ValueError("Embeddings must be finite and cover every firm in the comparison universe; "
                             "use --allow-partial-embeddings to audit and use the common sample")
        frame = frame.loc[valid].reset_index(drop=True)
        if frame.empty:
            raise ValueError("No firms remain in the embedding comparison sample")
    clip_bounds = {}
    for col in ["at_gr1", "gp_at"]:
        low, high = frame[col].quantile([0.01, 0.99])
        frame[col] = frame[col].clip(low, high)
        clip_bounds[col] = {"low": float(low), "high": float(high)}
    high = frame.div_at.quantile(0.99)
    frame["div_at"] = frame.div_at.clip(upper=high)
    clip_bounds["div_at"] = {"low": None, "high": float(high)}
    frame["log_me"] = np.log(frame.me)
    frame["log_be"] = np.log(frame.be)
    return frame, embedding_cols, clip_bounds, coverage


def evaluate(firms, quarter="2022Q4", embeddings=None, seed=32800,
             allow_partial_embeddings=False):
    """Return fixed split, predictions, model scores, and reproducibility metadata."""
    frame, emb_cols, clip_bounds, coverage = prepare_inputs(
        firms, quarter, embeddings, allow_partial_embeddings
    )
    if len(frame) < 20:
        raise ValueError("At least 20 firms are required for an 80/20 split and 10-fold CV")
    stage1 = LinearRegression().fit(frame[["log_be"]], frame.log_me)
    frame["valuation_residual"] = frame.log_me - stage1.predict(frame[["log_be"]])
    train, test = train_test_split(np.arange(len(frame)), test_size=0.2, random_state=seed)
    if len(train) < 10:
        raise ValueError("Training sample is too small for 10-fold CV")
    frame["split"] = "train"
    frame.loc[test, "split"] = "test"
    rng = np.random.default_rng(seed)
    placebo_cols = [f"placebo_{i}" for i in range(4)]
    frame[placebo_cols] = rng.standard_normal((len(frame), len(placebo_cols)))
    models = {"placebo_4": placebo_cols, "beta": ["beta_60m"], "characteristics_4": CHARS}
    if emb_cols:
        models["embeddings"] = emb_cols
        models["embeddings_plus_characteristics"] = emb_cols + CHARS
    y = frame.valuation_residual
    cv = KFold(n_splits=10, shuffle=True, random_state=seed)
    results = []
    predictions = frame[KEY + ["split", "valuation_residual"]].copy()
    for name, features in models.items():
        model = GridSearchCV(
            Pipeline([("scale", StandardScaler()), ("ridge", Ridge())]),
            {"ridge__alpha": ALPHAS}, scoring="neg_mean_squared_error", cv=cv,
            refit=True, error_score="raise",
        )
        model.fit(frame.loc[train, features], y.iloc[train])
        pred = model.predict(frame.loc[test, features])
        observed = y.iloc[test].to_numpy()
        variance = np.var(observed)
        if variance <= 0:
            raise ValueError("Test target has zero variance")
        paper_rv = 1 - np.var(observed - pred) / variance
        predictions[name] = np.nan
        predictions.loc[test, name] = pred
        results.append({"model": name, "n_features": len(features),
                        "n_train": len(train), "n_test": len(test),
                        "ridge_alpha": float(model.best_params_["ridge__alpha"]),
                        "paper_rv_variance_ratio": float(paper_rv),
                        "test_r2_sse": float(r2_score(observed, pred)),
                        "cv_mse": float(-model.best_score_)})
    metrics = pd.DataFrame(results)
    meta = {
        "quarter": str(pd.Period(quarter, freq="Q")), "n_firms": len(frame),
        "seed": seed, "train_fraction": 0.8, "cv_folds": 10,
        "embedding_coverage": coverage,
        "alpha_grid": ALPHAS.tolist(), "embedding_columns": emb_cols,
        "stage1_log_be_slope": float(stage1.coef_[0]),
        "stage1_intercept": float(stage1.intercept_), "winsorization": clip_bounds,
        "stage1_sample": "all firms in quarter, before train/test split",
        "preprocessing": "characteristics clipped on full provisional cross-section; scaling fitted within CV training folds",
        "score": "1 - variance(test residual - prediction) / variance(test residual)",
        "status": "provisional_overlap_unresolved; no final joint sample; no temporal out-of-sample test",
        "paper": "https://finm-32800.github.io/_downloads/2bd761eb9fb9d2d6a30e958f837ff449/GabaixKoijenRichmondYogo2026AssetAndInvestorEmbeddings.pdf",
    }
    return metrics, predictions, meta


def run(quarter="2022Q4", embedding_path=None, seed=32800, data_dir=None,
        output_dir=None, firm_table=None, allow_partial_embeddings=False):
    data_dir = Path(data_dir or config("DATA_DIR"))
    output_dir = Path(output_dir or config("OUTPUT_DIR"))
    period = str(pd.Period(quarter, freq="Q"))
    path = (Path(firm_table) if firm_table else data_dir / "clean" / "firm_characteristics"
            / period / "firm_quarterly_provisional.parquet")
    firms = pd.read_parquet(path)
    embeddings = pd.read_parquet(embedding_path) if embedding_path else None
    if allow_partial_embeddings and embeddings is None:
        raise ValueError("--allow-partial-embeddings requires --embeddings")
    metrics, predictions, meta = evaluate(firms, period, embeddings, seed,
                                           allow_partial_embeddings)
    dest = output_dir / "relative_valuation" / period
    meta["firm_input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    meta["firm_input"] = str(path.resolve())
    meta["sample_source"] = "external_unverified" if firm_table else "provisional_holdings_union"
    if firm_table:
        meta["status"] = "external firm sample supplied; overlap and final-sample status unverified; no temporal out-of-sample test"
    if embedding_path:
        embedding_path = Path(embedding_path)
        digest = hashlib.sha256(embedding_path.read_bytes()).hexdigest()
        meta["embedding_input"] = str(embedding_path.resolve())
        meta["embedding_input_sha256"] = digest
    if embedding_path or firm_table or seed != 32800:
        identity = json.dumps({"firm_sha256": meta["firm_input_sha256"],
                               "embedding_sha256": meta.get("embedding_input_sha256"),
                               "seed": seed, "allow_partial_embeddings": allow_partial_embeddings},
                              sort_keys=True).encode()
        dest = dest / f"comparison_{hashlib.sha256(identity).hexdigest()[:12]}"
    meta["output_directory"] = str(dest.resolve())
    dest.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(dest / "model_metrics.csv", index=False)
    predictions.to_parquet(dest / "firm_predictions.parquet", index=False)
    (dest / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default="2022Q4")
    parser.add_argument("--embeddings", help="Parquet with quarter, permco and emb_* columns")
    parser.add_argument("--firm-table", help="Alternative quarter x permco characteristics Parquet")
    parser.add_argument("--allow-partial-embeddings", action="store_true",
                        help="Explicitly evaluate all models on the audited common firm sample")
    parser.add_argument("--seed", type=int, default=32800)
    args = parser.parse_args()
    print(run(args.quarter, args.embeddings, args.seed, firm_table=args.firm_table,
              allow_partial_embeddings=args.allow_partial_embeddings).to_string(index=False))

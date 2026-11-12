"""Tests for the shared cross-sectional split and benchmark score."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from valuation.relative_valuation import evaluate, prepare_inputs


def sample_firms(n=60):
    rng = np.random.default_rng(91)
    log_be = rng.normal(5, 1, n)
    signal = rng.normal(size=n)
    return pd.DataFrame(
        {
            "quarter": pd.Timestamp("2022-12-31"),
            "permco": np.arange(n) + 1,
            "be": np.exp(log_be),
            "me": np.exp(0.8 * log_be + 0.5 * signal),
            "beta_60m": signal,
            "at_gr1": rng.normal(size=n),
            "gp_at": rng.normal(size=n),
            "div_at": rng.normal(size=n),
        }
    )


def test_shared_split_and_paper_variance_score():
    firms = sample_firms()
    embeddings = firms[["quarter", "permco"]].copy()
    embeddings["emb_0"] = firms.beta_60m
    metrics, predictions, metadata = evaluate(firms, embeddings=embeddings)
    assert set(metrics.model) == {
        "placebo_4",
        "beta",
        "characteristics_4",
        "embeddings",
        "embeddings_plus_characteristics",
    }
    test = predictions.loc[predictions.split.eq("test")]
    assert len(test) == 12
    assert len(predictions.loc[predictions.split.eq("train")]) == 48
    assert predictions.loc[predictions.split.eq("train"), "beta"].isna().all()
    assert test[list(metrics.model)].notna().all().all()
    expected = 1 - np.var(test.valuation_residual - test.beta) / np.var(
        test.valuation_residual
    )
    assert metrics.set_index("model").loc[
        "beta", "paper_rv_variance_ratio"
    ] == pytest.approx(expected)
    assert metadata["embedding_columns"] == ["emb_0"]


def test_missing_embedding_firm_rejected():
    firms = sample_firms()
    embeddings = firms.loc[1:, ["quarter", "permco"]].copy()
    embeddings["emb_0"] = 1.0
    with pytest.raises(ValueError, match="cover every firm"):
        prepare_inputs(firms, embeddings=embeddings)


def test_partial_embedding_uses_audited_common_universe_for_every_model():
    firms = sample_firms()
    embeddings = firms.loc[1:, ["quarter", "permco"]].copy()
    embeddings["emb_0"] = firms.loc[1:, "beta_60m"]
    metrics, predictions, metadata = evaluate(
        firms, embeddings=embeddings, allow_partial_embeddings=True
    )
    assert metadata["embedding_coverage"]["excluded_permcos"] == [1]
    assert metadata["embedding_coverage"]["n_firms_before_embedding_match"] == 60
    assert len(predictions) == 59
    assert 1 not in set(predictions.permco)
    assert metrics.n_train.nunique() == metrics.n_test.nunique() == 1
    test = predictions.loc[predictions.split.eq("test")]
    assert test[list(metrics.model)].notna().all().all()

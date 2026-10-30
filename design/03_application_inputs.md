# Layer 3: Application Inputs (placeholder)

> **Status: to be designed.** This page lists what layer 3 will contain. Each item gets its own table spec (grain, key, columns) later.

**Goal:** reshape the layer 2 tables into the exact input each model or benchmark needs. This layer is analysis, not cleaning. It reads only layer 2 tables (and model outputs).

## Shared inputs

| Table | Built from | Purpose |
|---|---|---|
| `splits` | `holdings`, `firm_quarterly` | Train/test split for each benchmark, made once with a fixed seed so every model uses the same split |

## Model inputs

| Application | Built from | Input |
|---|---|---|
| Recommender system (RS), all variants | `holdings` | TBD |
| Word2Vec, PS-BERT | `holdings` | TBD |
| OS-BERT | `holdings` | TBD |
| Text embeddings (OpenAI, Cohere) | `firm_names` | TBD |

## Model outputs (common format)

| Table | Content |
|---|---|
| `asset_embeddings` | TBD |
| `investor_embeddings` | TBD |

## Benchmark inputs

| Benchmark | Built from | Input |
|---|---|---|
| Relative valuation (RV) | `firm_quarterly`, `asset_embeddings`, `splits` | TBD |
| Return comovement (RC) | `firm_monthly`, `asset_embeddings`, `splits` | TBD |
| Portfolio prediction (MP) | `holdings`, `asset_embeddings`, `investor_embeddings`, `splits` | TBD |

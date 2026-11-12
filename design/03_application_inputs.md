# Layer 3: Application Inputs (placeholder)

> **Status:** The provisional relative valuation pilot is implemented; the other layer 3 interfaces remain to be designed.

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
| Relative valuation (RV) | `firm_quarterly`, optional `asset_embeddings`, shared firm split | See the Q4 team handoff below |
| Return comovement (RC) | `firm_monthly`, `asset_embeddings`, `splits` | TBD |
| Portfolio prediction (MP) | `holdings`, `asset_embeddings`, `investor_embeddings`, `splits` | TBD |

## Initial relative valuation pilot

The method, run steps, input/output contract, embedding interface, Q4 results,
and unresolved S12/S34 overlap are consolidated in the
[Q4 team handoff](q4_firm_matching_valuation_handoff.md).

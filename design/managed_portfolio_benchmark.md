# Managed portfolio benchmark: pilot method and interface

Status: **preliminary research framework; no trained model or paper replication.**
This document refers to the assigned July 16, 2026 paper, section 4.3,
equations (18)–(19), and Figure 12. Figure 13 is the higher-dimension extension.
[Course paper](https://finm-32800.github.io/_downloads/2bd761eb9fb9d2d6a30e958f837ff449/GabaixKoijenRichmondYogo2026AssetAndInvestorEmbeddings.pdf).

## Objective and decisions

The paper splits investors 90/10, fits models on training investors, and asks
them to identify each test investor's second-largest holding from its other
holdings. It evaluates probability assigned to the masked target against a
random-guess reference. Figure 12 is a 2022Q4 cross-section, not a backtest of
trading returns. The paper's reported stock characteristic set is beta, log
market equity, log market-to-book equity, asset growth, profitability, and
momentum. Model implementations and their baselines remain outstanding.

The pilot implementation fixes seed 32800, uses a 90/10 random investor split,
and breaks equal dollar-holding ties by ascending `permco`; these are explicit
engineering choices, not confirmed paper settings. Run one quarter and one
source/sample at a time. Use source-prefixed IDs; do not treat a provisional
S12/S34 union as deduplicated or final. The paper's final holdings screens,
source-selection decision, data availability timing and 20-by-20 filtering must
be settled upstream. No time-series out-of-sample claim is made here.

## Input and handoff to Juan

`prepare_cases(holdings, seed=32800)` accepts a pandas DataFrame containing:

| Column | Meaning |
|---|---|
| `quarter` | Single quarter-end date |
| `investor_id` | Stable source-qualified investor identifier |
| `permco` | Positive integer firm ID; one row per firm in each portfolio |
| `dollar_holding` | Positive finite value on a consistent basis within portfolio |

It returns `(context, targets, split)`. `split` has quarter, investor_id, split;
`context` has quarter, investor_id, permco, split, context_rank. Training rows
include all holdings; test rows omit the rank-2 holding and are re-ranked to
avoid leaking a rank gap. Dollar weights are omitted from context because they
could reveal information about the removed position. A downstream BERT adapter
may need an explicit mask token/position; that model-specific adapter must not
receive the hidden target identity. `targets` has quarter, investor_id,
target_permco and belongs only to the evaluator: **do not send it to training
or inference**. Split investors before training, rather than after training.

The model should produce a complete candidate distribution with columns
`quarter`, `investor_id`, `permco`, `probability`. Supply a separate, predeclared
eligible firm-universe DataFrame with unique `quarter, permco` keys. The exact
candidate set for each test investor is the universe minus its observed context;
it must include the masked target. Every candidate needs a probability row,
including candidates with zero probability, and probabilities must sum to one.
The target probability must be strictly positive; the scorer does not silently
clip numerical zeros. For BERT/Word2Vec models whose original softmax includes
observed firms, remove those firms and explicitly renormalize over the eligible
candidate complement before submitting; record this adaptation with the run.
Provide probabilities computed without target identities. For very small values,
use a stable log-softmax in the model and avoid numerical underflow.

`score_candidates(targets, predictions, context, universe)` checks normalized
keys, quarter, candidate uniqueness, observed/target membership, non-context
candidates, probability mass, and exact completeness. It avoids materializing a
universe-by-investor cross join: uniqueness + subset checks + expected counts
prove equality to each investor's complement. A universe file can itself omit
valid firms; this routine cannot determine whether the team's selected universe
is economically correct, so preserve its provenance and upstream audit.

`compare_models(targets, {name: predictions, ...}, context, universe)` validates
every model against these same cases and the same universe/context, then returns
the model-level metrics and case-level audit. This enforces identical candidate
identities, not just counts. It does not establish identical upstream data
preprocessing, training separation, weights or model provenance; the team must
record and review these separately. The common case/universe SHA-256 is for
tracing this particular set of inputs, not proof that models were honestly trained.

## Score ambiguity: do not treat as final paper metric

Equation (19) prints a log-probability ratio minus one, with a denominator based
on `|A_i(k)|`. The surrounding text defines `A_i(k)` as observed context but
equation (18) normalizes over assets *outside* that context. Also, taking the
printed ratio-minus-one literally gives zero for uniform predictions and -1 for
perfect predictions, while the paper says higher is better with upper bound one.
The meaning of cardinality in the denominator, summation when it varies by
investor, and sign require confirmation from authors' code or instructor.

**Additional primary-source evidence (November 25, 2026):** The same authors'
[September 13, 2024 conference paper, section 4.3, pages 18–19](https://www.aeaweb.org/conference/2025/program/paper/DftBsYFb)
defines the same masked-asset task and explicitly uses the complement of the
observed portfolio as its candidate set. Its normalized score is
`(sum(log(p_i)) + sum(log(C_i))) / sum(log(C_i))`, where `C_i` is the
number of candidates. This is algebraically identical to the pilot's
`higher_is_better_candidate_normalization`, including unequal candidate counts.
It is strong evidence for that interpretation, **not** proof of the code used
for the July 2026 Figure 12: the drafts differ and no authors' implementation has
been verified. A uniform candidate distribution scores zero; perfect target
probability scores one. Confirm the implementation and any later methodological
changes with the instructor or authors before presenting it as exact replication.

The scorer reports mean target log probability, mean log uniform probability
based on the verified candidate count, and two clearly
labeled pilot normalizations: `printed_equation_candidate_normalization`
(ratio − 1) and `higher_is_better_candidate_normalization` (1 − ratio).
The `asmp_2024` field is the higher-is-better formula explicitly printed in the
2024 paper. It is the recommended pilot score but is not verified as the authors'
implementation for the 2026 Figure 12. For variable candidate counts, it uses
the ratio of summed log likelihoods, as in the 2024 draft. Do not
compare these scores with Figure 12 or present them as replicated paper results
without resolving the ambiguity and checking identical inputs.

## Example (synthetic; no WRDS or model fit)

```python
import pandas as pd
from valuation.managed_portfolio import compare_models, prepare_cases

holdings = pd.DataFrame(
    [("2022-12-31", f"fund_{i}", firm, value)
     for i in range(20) for firm, value in [(1, 30), (2, 20), (3, 10)]],
    columns=["quarter", "investor_id", "permco", "dollar_holding"],
)
context, targets, split = prepare_cases(holdings)
# Only context and split go to a model; in this toy, choose a fixed universe
# {1, 2, 3, 4} without inspecting target identities.
cases = split.loc[split.split.eq("test"), ["quarter", "investor_id"]]
universe = pd.DataFrame({"quarter": pd.Timestamp("2022-12-31"),
                         "permco": [1, 2, 3, 4]})
pred = cases.merge(universe, on="quarter")
observed = context.loc[context.split.eq("test"), ["quarter", "investor_id", "permco"]]
pred = pred.merge(observed.assign(observed=True), how="left")
pred = pred.loc[pred.observed.isna()].drop(columns="observed")
pred["probability"] = 1 / pred.groupby(["quarter", "investor_id"])["permco"].transform("size")
metrics, case_audit = compare_models(targets, {"uniform": pred}, context, universe)
assert abs(metrics.loc[0, "asmp_2024"]) < 1e-12
```

## Team integration checklist

1. Agree on a source, a single quarter, upstream filters, and the independent
   `universe` table before running the benchmark. For an S12-only pilot, use
   S12 investor IDs and do not describe it as the combined final sample.
2. Evaluation owner runs `prepare_cases`; share **training and test context,
   split and universe** with the model owner, never the evaluator-only targets.
   Preserve `[MASK]` at original position 2 for sequence models. Do not train
   on test portfolios, infer rank from targets, or share target IDs via logs.
3. Model owner returns one Parquet table per model with `quarter`, `investor_id`,
   `permco`, `probability`, with one row per allowed candidate. Include zero
   probabilities explicitly for non-target candidates. Document model name,
   training revision, seed, input/universe hashes, mask handling and softmax
   normalization. The test target must have positive probability.
4. Evaluator reads the tables and calls
   `compare_models(targets, {"word2vec": p1, "bert": p2}, context, universe)`;
   share only model-level metrics and appropriate aggregate coverage diagnostics.
   Keep holdings, raw predictions, and investor-level audits local unless the
   course's data license and sharing rules expressly permit distribution.
5. Compare `asmp_2024` with a uniform baseline, and document the provenance and
   limits before any comparison with the 2026 Figure 12. Confirm the 2026 metric
   with Jeremy or author code; no empirical result is claimed yet.

Run `python -m pytest -q src/valuation/test_managed_portfolio.py` from the
project root. Tests use synthetic inputs and do not establish empirical model
performance or the final 20-by-20 sample.

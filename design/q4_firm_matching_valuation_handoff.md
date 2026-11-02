# 2022Q4 Firm Matching and Relative Valuation: Team Handoff

**Status:** Pilot, pending team review and final sample construction.

**Scope:** S12/S34 identifier matching, the firm-quarter characteristic table,
and the initial relative valuation benchmark.

**Reference:** [Asset and Investor Embeddings, July 16, 2026](https://finm-32800.github.io/_downloads/2bd761eb9fb9d2d6a30e958f837ff449/GabaixKoijenRichmondYogo2026AssetAndInvestorEmbeddings.pdf), Section 4.1, Figures 5–6, and Appendix D.1.2.

The Q4 run produces a provisional holdings union, a company characteristic table,
and placebo, beta, and four-characteristic valuation baselines. **S12 fund and
S34 manager economic overlap is unresolved.** The final holdings universe,
concentration and iterative 20-by-20 filters, and the embedding comparison are
still pending. Juan owns the end-to-end pipeline and final-sample integration;
Shuxin owns firm matching, valuation inputs, and the initial valuation benchmark.

## 1. What is available

| Item | Local path | Grain or purpose |
|---|---|---|
| Provisional holdings | `_data/clean/combined/2022Q4/holdings_provisional.parquet` | `quarter × investor_id × permco`; source union with overlap unresolved |
| Matching audit | `_output/combined/2022Q4/matching_audit.json` | Source counts, matching rates, and cross-source link checks |
| Firm characteristics | `_data/clean/firm_characteristics/2022Q4/firm_quarterly_provisional.parquet` | Eligible `quarter × permco` firms used by the Q4 benchmark |
| Firm coverage | `_output/firm_characteristics/2022Q4/coverage_audit.json` and `firm_coverage.parquet` | Coverage and exclusion flags for all matched holdings firms |
| RV results | `_output/relative_valuation/2022Q4/model_metrics.csv` | Placebo, beta, and four-characteristic scores |
| RV predictions | `_output/relative_valuation/2022Q4/firm_predictions.parquet` | Keys, shared split, valuation target, and test predictions |
| RV run metadata | `_output/relative_valuation/2022Q4/run_metadata.json` | Parameters, clipping bounds, first-stage regression, and input hash |

Raw and generated datasets and reports are ignored by Git. Each collaborator
must generate them locally using their own WRDS access. The source scripts,
tests, and this handoff are intended to be shared through the repository.

## 2. Reproduce the Q4 pilot

1. Set up the repository's Python dependencies from `uv.lock`, and activate the
   project environment. Create a local, Git-ignored `.env` from `.env.example`.
   Set your WRDS username, `START_DATE=2022-01-01`, `END_DATE=2022-12-31`, and
   `S34_HISTORICAL_FALLBACK=False`. Configure WRDS authentication locally.
2. From the repository root, run:

   ```bash
   python -m doit relative_valuation
   ```

   The task runs its required upstream steps if needed: S12 and S34 pulls and
   matching, the provisional holdings union, the JKP pull, and firm table
   construction. A first run downloads the S12 report vintages for 2022 and may
   take time and disk space. Existing valid caches are reused.
3. Review the matching and coverage audits listed above, then inspect
   `model_metrics.csv` and `run_metadata.json`. Run the relevant tests with:

   ```bash
   python -m pytest -q src/holdings_pipeline/test_integrate_holdings.py \
       src/holdings_pipeline/test_q4_firm_characteristics.py \
       src/valuation/test_relative_valuation.py
   ```

## 3. Matching and firm-quarter inputs

The pilot uses unique CRSP CUSIP links valid on the holdings report date. Both
sources use the provisional US-exchange-listed ordinary-share policy; foreign
incorporation alone is not an exclusion. S34's historical-CUSIP fallback is off
for this pilot. Ambiguous matches are not silently selected. S12 and S34 links
are compared on the same report date and CUSIP before the union is written.

The company table joins the provisional holdings-firm union to CRSP and JKP on
quarter and `permco`. CRSP quarter-end company market equity (`me`) sums eligible
share classes. JKP's Compustat-derived book equity (`be`) is counted once per
`gvkey`, then summed within `permco`; inconsistent values or missing `gvkey`
are flagged. `me` and `be` are in **USD millions**. JKP ratios are aggregated
across securities using their JKP market-equity weights. The table contains
`log_me`, `log_be`, `log_be_me`, `beta_60m`, `at_gr1`, `gp_at`, `div_at`,
`ret_12_1`, and `size_grp`. The full six-characteristic universe is required by
the current joint-sample design; momentum is not an RV predictor.

In the local Q4 run, 5,169 firms appear in the holdings union and 2,106 pass the
firm-characteristic screen. There are no disagreements among the 5,038
same-date/CUSIP links compared across sources. The exclusions in the firm audit
are **nonexclusive**: 2,425 firms fail the size screen, 830 have missing or
nonpositive book equity, and 1,784 lack at least one core characteristic. These
counts must not be summed. S12's 37.97% date-valid match rate uses all selected
source positions as its denominator, not a prefiltered US-equity universe; it
is not directly comparable with S34's 96.88% rate.

## 4. Relative valuation method and results

1. On the quarter's full provisional company cross-section, regress `log(me)`
   on `log(be)` and an intercept. The fitted residual is the valuation target.
2. Clip asset growth and profitability at their 1st/99th percentiles and
   dividends/assets at its 99th percentile on that cross-section. Split firms
   80/20 using seed 32800; all models use the same split.
3. Fit ridge regressions with ten-fold cross-validation on training firms.
   Feature scaling is fitted within each CV training fold. The alpha grid has
   25 log-spaced values from `1e-4` to `1e4`; the seed, grid, and shuffled CV
   folds are documented pilot choices.
4. Report equation (15), `1 - Var(test target - prediction) / Var(test target)`.
   Also report the conventional test R² based on squared errors; the two can
   differ because the variance score centers errors.

The local run uses 1,684 training and 422 test firms:

| Model | Paper variance score | Conventional test R² |
|---|---:|---:|
| Four-dimensional random placebo | 0.09% | -0.59% |
| Market beta | 1.38% | 0.73% |
| Beta, asset growth, profitability, dividends/assets | 10.59% | 10.13% |

The first regression and cross-sectional clipping use the entire quarter,
including test firms. This follows the paper-style cross-sectional construction
but is not a fully isolated or forward-looking forecast. The 2022Q4 pilot is
not the paper's 2005Q1–2022Q4 historical average. The sample has not yet had
the final joint-sample filters applied. Results must be regenerated after the
team settles the final sample and preprocessing order.

## 5. Interface for asset embeddings

The benchmark accepts a Parquet file with one row per `quarter × permco` and
numeric embedding columns named `emb_0`, `emb_1`, etc. Keys must be unique and
non-null. By default the file must cover every company in the current comparison
sample. Extra firms are ignored; missing or nonfinite values cause an error so
that models cannot silently use different samples.

```bash
python src/valuation/relative_valuation.py --embeddings /path/to/embeddings.parquet
```

An embedding run scores the baselines, embeddings alone, and embeddings plus the
four characteristics using the same firms and split. If the team agrees to use
the common intersection when coverage is incomplete, it can be selected
explicitly:

```bash
python src/valuation/relative_valuation.py --embeddings /path/to/embeddings.parquet \
    --allow-partial-embeddings
```

In that mode, firms with absent or nonfinite embeddings are excluded from every
model before clipping, target construction and the split. `run_metadata.json`
records the original firm count, excluded `permco` values, and extra embedding
firm count. Compare the embedding results with the baselines from that same run;
the previously reported full-sample baselines use a different universe.

When the final company sample is supplied, use `--firm-table /path/to/firm.parquet`
to substitute it for the pilot table. Its schema must contain `quarter`, `permco`,
`me`, `be`, and the four RV characteristics; `me` and `be` must use the same
monetary unit. The script does not independently establish that an external table
has passed final-sample filters. It labels its status unverified.

Alternative inputs and seeds write to a `comparison_<hash>` subdirectory under
`_output/relative_valuation/2022Q4/`, preserving the default baseline. The hash
uses the input file hashes, seed, and intersection setting; metadata records the
input files and output directory. No team embedding or final company sample has
yet been supplied or evaluated.

## 6. Open issue: economic overlap between S12 and S34

S12 observations are funds; S34 observations are 13F reporting institutions or
managers. A manager's S34 report can include positions already represented by
its funds in S12. Prefixing investor IDs with `s12_` or `s34_` prevents key
collisions but does not identify distinct economic investors. A company held in
both sources is not evidence by itself that two positions are duplicates.

The current union retains source and investor IDs and marks its status as
`provisional_overlap_unresolved`. It does not deduplicate, delete, or rescale
positions based on similar names, a shared company, or ownership above 100%.
Overlap can affect investor counts, holdings values, AUM, ownership, embeddings,
and benchmark results. Separately, the current S12 run flags six companies with
aggregate S12 ownership above 100%; these exceptions remain unresolved.

Decisions for the team and Jeremy:

1. Is a verifiable fund-to-13F-manager mapping available, including treatment
   of partially overlapping managers?
2. If mapping is incomplete, what documented source-selection rule should be
   used, and what sensitivity comparison should be reported?
3. Which suspected Q4 overlaps should be checked against original reports before
   extending the rule to other quarters?
4. After the rule and joint filters are implemented, recompute AUM, portfolio
   weights, ownership and holding counts; rebuild the common firm universe and
   rerun the benchmark. Preserve unresolved exceptions and document the effects.

Implementation details are in `src/holdings_pipeline/integrate_holdings.py`,
`src/holdings_pipeline/build_q4_firm_characteristics.py`, and
`src/valuation/relative_valuation.py`. See `src/holdings_pipeline/MERGE_NOTES.md`
for the broader integration checks.

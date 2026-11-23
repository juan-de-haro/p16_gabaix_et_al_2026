# Historical S12 holdings, 2005Q1–2022Q4

The historical run covers 72 consecutive quarters and 73,149,773 fund–company–quarter positions. The [coverage table](quarterly_coverage.csv) has one row per quarter. The [validation matrix](validation_by_quarter.csv) gives the outcome of each of the 15 checks in each quarter. Both are aggregate snapshots of the sample built with the S12 cleaning rules in commit `8be2e87`.

## Reproduce the panel

Run these commands from the repository root. Install the locked environment with `uv sync --locked`. Copy `.env.example` to `.env`, set `WRDS_USERNAME`, and configure your WRDS PostgreSQL login (including Duo if prompted). Your account needs access to `tfn.s12`, `tfn.s12type1`, `crspm.msenames` and `crspm.msf_v2`. The historical tasks specify each full calendar year explicitly.

To generate one quarter for a model run, use the quarter task.

```bash
uv run doit -f dodo_s12_history.py quarter:2022Q4
```

This fetches or verifies the **full year's** raw caches. Annual pulls keep all available vintages of reports dated in that year and obtain an independent WRDS row count by report quarter. The cleaner selects each fund's latest report in the quarter and its earliest available vintage. It uses report-date CRSP identifiers and split-adjusted shares to value eligible ordinary shares at quarter end.

If the three verified annual caches, their sidecars and the quarter's original `source_row_counts.json` are already in the configured directories, you can rerun the cleaner offline. For example, to reproduce the flagged quarter 2005Q2, run the quarter task on its own.

```bash
uv run doit -f dodo_s12_history.py run --single quarter:2005Q2
```

To build all 18 years and both summaries, run the summary task.

```bash
uv run doit -f dodo_s12_history.py summary
```

Rerun the same command after an interruption. Completed tasks with current dependencies and targets can be reused. A fresh WRDS extract can differ from the reviewed snapshot if the provider revises historical inputs. The annual cache sidecars record queries, dates, row counts and SHA-256 hashes.

With all quarterly holdings and validation reports already present, the following command rebuilds the aggregate CSVs locally.

```bash
uv run python src/holdings_pipeline/s12_history.py
```

| Output (default paths) | How to use it |
|---|---|
| `_data/clean/s12/<quarter>/holdings_s12.parquet` | Model input at the `quarter × investor_id × permco` level. Dollar holdings are in USD. Portfolio weights sum to one over the retained equity positions of each fund. |
| `_data/clean/s12/<quarter>/filter_log_s12.parquet` | Trace the change in observations through matching, eligibility, valuation and company aggregation. |
| `_output/s12/<quarter>/validation.json` and `quality_report.html` | Inspect checks and source counts. The adjacent `exceptions/` directory contains row-level evidence for flags. |
| `_output/s12_history/` | Compare the two regenerated CSVs with the snapshots linked above. |

`DATA_DIR` and `OUTPUT_DIR` can point to other local roots. These runs write S12-only inputs. S12/S34 overlap treatment, the joint firm universe, final weights and the iterative 20-by-20 filter belong to the combined-sample stage. The raw caches, holdings files, detailed exception records and credentials stay local.

## Read the coverage table

- **Sample size.** `raw_rows` counts all source rows by report quarter, including multiple vintages. `funds`, `firms` and `fund_firm_positions` count retained investors, distinct companies and fund–company positions after S12 cleaning. The historical total sums quarterly positions.
- **Match rate.** `cusip_match_pct` is the percentage of positive-share positions with a valid CUSIP format that obtain a unique report-date CRSP link, before ordinary-share eligibility filters. Compare sources only after aligning denominators.
- **Timing.** `off_quarter_end_funds` counts retained funds reporting before quarter end. `later_vintage_funds` counts those whose first selected database vintage follows quarter end. These dates describe a retrospective holdings sample.
- **Coverage change.** From 2017Q3 to 2017Q4, retained funds increased from 8,543 to 16,593, while retained companies fell from 3,851 to 3,393 and the match rate fell from 78.41% to 39.82%. Both quarters passed all 15 checks. The cause of the break remains open for the cross-source comparison.

## Validation findings

**59 quarters passed all 15 checks.** In the other 13 quarters, only `firm_ownership_at_most_one` was flagged. The annual and quarterly record counts agreed with WRDS throughout the sample. Other checks cover key uniqueness, valid numeric values, weights, aggregation, metadata coverage, report timing and filter counts.

Summing `ownership_flags` over the panel gives **18 company–quarter flags affecting 17 distinct companies**. The largest reported ownership is **301.99% for Mylan in 2010Q3**. In 2022Q4, six companies are flagged. The denominator is eligible CRSP company equity at quarter end. Flagged positions remain in the S12 output for investigation. Each quarter's local report identifies the companies and their underlying inputs.

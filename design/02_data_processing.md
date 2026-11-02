# Layer 2: Data Processing (clean tables)

**Goal:** turn raw data into tidy tables that share the same keys and the same firm universe. All the filters from Appendix D happen here, and nowhere else.

## Rules

1. **Reads only layer 1** (through the `load_*()` functions).
2. **Tidy tables.** Each table has one clear grain (what one row is) and a unique key.
3. **One firm universe per quarter.** `holdings`, `firm_quarterly`, and `firm_names` contain exactly the same `permco` values in each quarter.
4. **Every filter is logged.** Each step writes its row counts to `filter_log`.

## Overview of tables

**Primary tables**

- `holdings`: which investor holds which firm, and how much, each quarter.
- `firm_monthly`: monthly returns and market cap for every firm.
- `firm_quarterly`: the firm characteristics used in the paper, one value per quarter.
- `firm_quarterly_jkp_full`: all JKP characteristics, used only for Figure 4, Panel B.

**Auxiliary tables**

- `investors`: who each investor is (name, source, type, size).
- `firm_names`: the CRSP name of each firm, used for text embeddings and reading results.
- `filter_log`: how many rows, investors, and firms remain after each processing step.

## Processing steps

Steps run in this order. Order matters, because the 20×20 filter must run on the final firm universe.

| # | Step | Paper reference |
|---|---|---|
| 1 | Keep CRSP common stocks. Aggregate PERMNO → PERMCO. | |
| 2 | Link holdings to CRSP: CUSIP → PERMNO (name valid on the report date) → PERMCO. | |
| 3 | Align dates to quarter ends. Adjust shares for splits. Compute dollar holdings. | |
| 4 | Combine s12 and s34 into one holdings table, avoiding double counting. | |
| 5 | Build the firm universe: drop firms with missing characteristics, non-positive book equity, or micro/nano cap. | App. D.1.2 |
| 6 | Iterative 20×20 filter on holdings (investors with ≥ 20 firms, firms with ≥ 20 investors) until nothing changes. | App. D.1.3 |
| 7 | Winsorize characteristics. Compute centered log holdings. | App. D.1.3 |

## Tables at a glance

| Table | One row per | Key | Main use |
|---|---|---|---|
| `holdings` | quarter × investor × firm | `quarter`, `investor_id`, `permco` | All models |
| `firm_monthly` | month × firm | `month`, `permco` | Return comovement benchmark |
| `firm_quarterly` | quarter × firm | `quarter`, `permco` | Relative valuation benchmark, characteristics |
| `firm_quarterly_jkp_full` | quarter × firm | `quarter`, `permco` | Figure 4, Panel B only |
| `investors` | quarter × investor | `quarter`, `investor_id` | Figure 2, investor embeddings |
| `firm_names` | quarter × firm | `quarter`, `permco` | Text embeddings, reading results |
| `filter_log` | quarter × step | `quarter`, `step` | Validation table / figure |

## Tables

### `holdings`

- **Script:** `clean_holdings.py`
- **One row per:** quarter × investor × firm (positive positions only)
- **Key:** `quarter`, `investor_id`, `permco`

| Column | Description |
|---|---|
| `quarter` | Quarter-end date |
| `investor_id` | Investor (`s12_<fundno>` or `s34_<mgrno>`) |
| `permco` | Firm |
| `shares` | Shares held, split-adjusted to the quarter end |
| `dollar_holding` | H = value of the position at quarter-end prices |
| `log_h` | log(H) |
| `log_h_centered` | Winsorized log(H) minus investor and firm fixed effects |
| `portfolio_weight` | H / total portfolio value of the investor |
| `ownership_share` | H / market cap of the firm |

### `firm_monthly`

- **Script:** `clean_firms.py`
- **One row per:** month × firm
- **Key:** `month`, `permco`

| Column | Description |
|---|---|
| `month` | Month-end date |
| `permco` | Firm |
| `ret` | Monthly total return, market-cap weighted across PERMNOs |
| `me` | Market cap (sum across PERMNOs). **Source of truth for `me`.** |

This table covers **all** CRSP common stocks, not only the quarterly universe. Layer 3 picks the firms it needs. For example, the comovement benchmark uses returns in quarter *t* for firms in the universe of quarter *t − 1*.

### `firm_quarterly`

- **Script:** `clean_firms.py`
- **One row per:** quarter × firm
- **Key:** `quarter`, `permco`

| Column | Description |
|---|---|
| `quarter`, `permco` | Key |
| `me`, `log_me` | Market cap, taken from `firm_monthly` (last month of the quarter) |
| `be`, `log_be` | Book equity |
| `log_be_me` | Log book-to-market, winsorized |
| `at_gr1` | Asset growth, winsorized |
| `gp_at` | Profitability, winsorized |
| `ret_12_1` | Momentum, winsorized |
| `div_at` | Dividends to assets, winsorized |
| `beta_60m` | Market beta |
| `size_grp` | JKP size group (documents the micro/nano cap filter) |

JKP data is monthly. We use the value from the last month of each quarter.

### `firm_quarterly_jkp_full`

- **Script:** `clean_firms.py`
- **One row per:** quarter × firm
- **Key:** `quarter`, `permco`
- **Columns:** `quarter`, `permco`, and all JKP characteristics.

Kept separate so `firm_quarterly` stays small.

### `investors`

- **Script:** `clean_holdings.py`
- **One row per:** quarter × investor
- **Key:** `quarter`, `investor_id`

| Column | Description |
|---|---|
| `quarter`, `investor_id` | Key |
| `source` | `s12` or `s34` |
| `source_id` | Original identifier (`fundno` or `mgrno`) |
| `name` | Fund or manager name |
| `investor_type` | Mutual fund, hedge fund, etc. (where available) |
| `n_holdings` | Number of firms held |
| `aum` | Total portfolio value |

### `firm_names`

- **Script:** `clean_firms.py`
- **One row per:** quarter × firm
- **Key:** `quarter`, `permco`

| Column | Description |
|---|---|
| `quarter`, `permco` | Key |
| `permnos` | List of PERMNOs combined into this firm |
| `name` | CRSP company name |

### `filter_log`

- **Script:** written by every `clean_*.py` step
- **One row per:** quarter × processing step
- **Key:** `quarter`, `step`

| Column | Description |
|---|---|
| `quarter`, `step` | Key (`step` is the step number and name from the table above) |
| `n_obs` | Holdings rows remaining after the step |
| `n_investors` | Investors remaining |
| `n_firms` | Firms remaining |

## Validation checks (tests)

- Each table's key is unique.
- Portfolio weights sum to 1 for each investor and quarter.
- Ownership shares sum to at most 1 for each firm and quarter.
- `holdings`, `firm_quarterly`, and `firm_names` have the same `permco` set in each quarter.
- Every `investor_id` in `holdings` exists in `investors`.
- Row counts in `filter_log` never increase from one step to the next.

## Open decisions

### Implemented S12 contribution

`src/holdings_pipeline/clean_s12.py` produces an S12-only contribution to this design. Run
`uv run doit s12` after setting `START_DATE` and `END_DATE` in the local `.env`;
the last quarter of that period is processed. To process another fully covered
quarter, run `uv run python src/holdings_pipeline/clean_s12.py --quarter YYYYQn`.

For each fund, it selects the latest `rdate` within the processing quarter and
the earliest `fdate` containing that report. Earlier-quarter reports are not
carried forward. Identical positions are deduplicated; conflicting source keys,
ambiguous historical CUSIP mappings, and multiple source CUSIPs mapping to the
same security are quarantined for inspection instead of being summed.

CUSIP mappings use CRSP name intervals valid on `rdate`. The contribution keeps
US common stocks on NYSE/AMEX/NASDAQ at both the report date and quarter end.
The monthly price and split inputs use CRSP CIZ (`crspm.msf_v2`); historical name
intervals currently use `crspm.msenames`, which covers the 2022 development sample.
Recheck historical-name coverage before extending beyond the legacy period.

S12 share quantities are on the **FDATE split basis**, not necessarily the RDATE
basis. Following the [WRDS Return Gap example](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/wrds-macros-return-gap/),
quarter-end shares are `shares * factor(fdate) / factor(quarter_end)`.
Quarter-end market value uses the absolute CRSP price. Firm market capitalization
sums all eligible share classes, including classes not held by a particular fund;
CRSP `shrout` is in thousands. Share counts summed across classes are retained for
traceability but are not economically fungible; use dollar holdings for comparisons.

Outputs in `_data/clean/s12/YYYYQn/` include `holdings_s12`, security-level
holdings, `investors_s12`, `firm_names_s12`, the selected report dates, and a
filter log. AUM and portfolio weights describe the matched US common-equity
subset, not the fund's total NAV. Diagnostics, rejected rows, and an HTML quality
report are saved under `_output/s12/YYYYQn/`.

As agreed for the S12 handoff, S12/S34 overlap handling, the JKP-based common
universe, concentration filters, the final iterative 20x20 filter, winsorization,
and two-way demeaning remain in the combined pipeline. Recompute portfolio
weights after those filters. Ownership above 100% is explicitly flagged and
retained for review, rather than being silently clipped or declared valid.

The initial **2022Q4 pilot** produced 1,735,513 fund-firm positions, 17,799
funds, and 4,156 firms. Structural checks passed, but four firms had aggregate
S12 ownership above one: Thunder Bridge Capital Partners III, Iris Acquisition,
Landcadia Holdings IV, and OmniLit Acquisition. All four have a 90-97% fall in
CRSP shares outstanding from November to December 2022, with unchanged split
factors. The [OmniLit 2022 annual report](https://www.sec.gov/Archives/edgar/data/1866816/000149315223002997/form10-k.htm)
confirms substantial December redemptions; [Thunder Bridge's filing](https://www.sec.gov/Archives/edgar/data/1815753/000121390023012835/ea173935-8k_thunderiii.htm)
also documents December redemption payments. This supports investigating
corporate-action timing and source alignment; it does not establish which
individual holdings are wrong. The flagged positions remain unresolved.

The follow-up review is reproducible with `python src/holdings_pipeline/audit_s12_ownership.py`
after processing 2022Q4 and retaining the supplemental evidence cache at
`_data/raw/s12/ownership_review_2022Q4`. It writes a Chinese evidence report,
57 position-level evidence rows, and machine-readable findings under
`_output/s12/2022Q4/ownership_review`, and attaches them to the quality report.
The review manifest `design/s12_2022q4_ownership_evidence.json` contains source
links and judgments only; it is never a numerical override table.

The four CRSP denominators reconcile to issuer-disclosed Class A shares within
thousand-share rounding. The four firms still exceed 100% using only RDATE
2022-12-31 reports. A fresh bounded WRDS query matches the annual cache, and
none of the 57 selected positions changes share count across available FDATEs.
Original fund filings reproduce the major positions, with several valued at
redemption prices. First Trust's year-end N-PORT XML and portfolio exhibit
disagree. These findings support a redemption/source-alignment conflict, but
do not establish replacement balances or justify setting positions to zero.
Unlisted founder Class B shares must not be inserted into the denominator at
the Class A price without an explicit, consistently applied research rule.

`clean_s12.py` now always regenerates report-date ownership decomposition and
security-level review rows, including report age, contextual report-month
SHROUT and individual ownership flags. These diagnostics never filter or
rescale holdings. The detailed 2022Q4 source investigation is an explicit
second step; rerun it after cleaning to refresh the evidence annotation.

The pilot's 37.97% CUSIP match rate is calculated against all selected S12
positions. A WRDS stock-name spot check of the 20 most frequent unmatched codes
included ASML, Roche, Nestle, Tencent, and Kweichow Moutai. Do not interpret that
rate as coverage of a prefiltered US-equity universe, or assume every unmatched
position is foreign. The quality report separates codes absent from the cached
CRSP name history from codes present outside the report-date validity interval.
The local `_output/s12/2022Q4/unmatched_source_spotcheck.json` preserves that
one-off source query and its results; recurring diagnostics are regenerated by
`clean_s12.py` from the cached raw inputs.

### Decisions for the combined pipeline

- **Double counting between s12 and s34:** the rule for combining the two sources (step 4).
- **Delisting returns:** confirm that CRSP's new format already includes them in `mthret`.
- **One universe vs. several:** the relative valuation benchmark uses 4 characteristics, while comovement and portfolio prediction use 6. This design reads Appendix D.1.2 as one universe for everything. Write this choice in the README.

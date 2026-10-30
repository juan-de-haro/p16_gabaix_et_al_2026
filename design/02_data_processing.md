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

- **Double counting between s12 and s34:** the rule for combining the two sources (step 4).
- **Delisting returns:** confirm that CRSP's new format already includes them in `mthret`.
- **One universe vs. several:** the relative valuation benchmark uses 4 characteristics, while comovement and portfolio prediction use 6. This design reads Appendix D.1.2 as one universe for everything. Write this choice in the README.

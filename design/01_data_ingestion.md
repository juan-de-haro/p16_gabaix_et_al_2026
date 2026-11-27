# Layer 1: Data Ingestion (raw data)

**Goal:** download the source data exactly as delivered and save it locally.

## Rules

1. **No transformations.** No filters, merges, or new columns. The only restrictions are the date range and the columns we request.
2. **Original column names.** Renaming happens in layer 2.
3. **One script per source.** Each `pull_<source>.py` saves its tables to `_data/raw/` and has a matching `load_<table>()` function. Layer 2 reads data only through these functions.
4. **Not committed.** Raw data is copyrighted. It stays out of Git.

## Raw tables at a glance

| Raw table | Source | Frequency | Used for |
|---|---|---|---|
| `raw_s12_holdings` | WRDS, Thomson Reuters `tfn.s12` | Quarterly | Mutual fund holdings |
| `raw_s34_holdings` | WRDS, Thomson Reuters `tr_13f.s34type1`, `tr_13f.s34type3` | Quarterly | 13F institutional holdings |
| `raw_crsp_monthly` | WRDS, CRSP `crspm.msf_v2` | Monthly | Prices, returns, market cap |
| `raw_crsp_names` | WRDS, CRSP stock names | Event-based | CUSIP → PERMNO link, firm names |
| `raw_jkp_characteristics` | WRDS, JKP `contrib_global_factor.global_factor` (US) | Quarterly (quarter-end months) | Firm characteristics |

## Tables

### `raw_s12_holdings`

- **Script:** `pull_s12.py` (implemented for the S12 contribution)
- **One row per:** file date × fund × security
- **Key:** `fdate`, `fundno`, `cusip`

| Column | Description |
|---|---|
| `fdate` | Data vintage date; the reported share quantities incorporate splits through this date |
| `rdate` | Actual date of the reported portfolio holdings; neither date is an SEC publication timestamp |
| `fundno` | Fund identifier |
| `fundname` | Fund name |
| `cusip` | Security identifier |
| `shares` | Shares held |

The implemented pull selects the annual sample on `rdate` and retains all
available `fdate` vintages, including later vintages of an in-sample report.
Files are cached under `_data/raw/s12/START_END/`, with one Parquet file per
source table and JSON sidecars containing the query, timestamp, row count, and
SHA-256 checksum. The supporting raw CRSP tables cover the report period through
the last included vintage so that the share basis can be converted correctly.
The raw layer does not select funds, remove stale versions, join tables, or
transform share quantities.

### `raw_s34_holdings`

- **Script:** `pull_s34.py` (implemented for the S34 contribution); cleaned by `clean_s34.py`
- **Source tables:** `tr_13f.s34type1` (manager reports) and `tr_13f.s34type3` (holdings)
- **One row per:** file date × manager (`s34type1`); file date × manager × security (`s34type3`)
- **Key:** `fdate`, `mgrno` (`s34type1`); `fdate`, `mgrno`, `cusip` (`s34type3`)

| Column | Table | Description |
|---|---|---|
| `fdate` | both | File date (data vintage); share quantities incorporate splits through this date |
| `rdate` | `s34type1` | Report date of the holdings |
| `mgrno` | both | Manager identifier |
| `mgrname` | `s34type1` | Manager name |
| `typecode` | `s34type1` | Manager type (Thomson Reuters typecode) |
| `cusip` | `s34type3` | Security identifier (8-character CUSIP) |
| `shares` | `s34type3` | Shares held |

The implemented pull selects on `rdate` and keeps every `fdate` vintage of each
manager report with that report date: TR carries reports forward for several
quarters, so a manager–rdate pair can appear under several vintages.
`s34type3` is restricted to the (`mgrno`, `fdate`) pairs of those reports, with
no filter on shares. Files are saved per report date under
`_data/raw/s34/YYYYQn/` as `s34type1.parquet` and `s34type3.parquet`, together
with `msf_v2.parquet`: CRSP `permno`, `yyyymm`, `mthprc`, `mthcumfacshr`, and
`primaryexch` for the `rdate` month and every `fdate` month of those reports,
used to value holdings and to convert share quantities from `fdate` to `rdate`.
The CRSP name history is saved once as `_data/raw/crsp/stocknames_v2.parquet`
(`permno`, `permco`, `cusip`, `hdrcusip`, `namedt`, `nameenddt`, and the
security attributes `sharetype`, `securitytype`, `securitysubtype`, `usincflg`,
`issuertype`). The raw layer does not select vintages, filter shares, link
CUSIPs, or join tables. `clean_s34.py` does all of this and reads the raw files
only through `load_raw_s34()` and `load_crsp_names()`.

### `raw_crsp_monthly`

- **Script:** `pull_CRSP_stock.py` (already exists)
- **One row per:** month × security
- **Key:** `mthcaldt`, `permno`

| Column | Description |
|---|---|
| `mthcaldt` | Month-end date |
| `permno` | Security identifier |
| `permco` | Company identifier |
| `mthret` | Monthly total return (with dividends) |
| `mthprc` | Month-end price |
| `shrout` | Shares outstanding |
| `sharetype`, `securitytype`, `primaryexch` | Used in layer 2 to keep only common stocks |

### `raw_crsp_names`

- **Script:** `pull_CRSP_stock.py`
- **One row per:** security × name period
- **Key:** `permno`, `namedt`

| Column | Description |
|---|---|
| `permno`, `permco` | Identifiers |
| `ncusip` | Historical CUSIP (links holdings to CRSP) |
| `comnam` | Company name |
| `namedt`, `nameenddt` | Period in which this row is valid |

### `raw_jkp_characteristics`

- **Script:** `pull_jkp.py` (implemented for the JKP contribution); cleaned by `clean_jkp.py`
- **Source table:** `contrib_global_factor.global_factor` on WRDS, US stocks from CRSP (`excntry = 'USA'`, `source_crsp = 1`), quarter-end months only
- **One row per:** month × security
- **Key:** `eom`, `permno`

| Column | Description |
|---|---|
| `eom` | Month-end date |
| `permno`, `permco`, `gvkey` | Security, company, and Compustat identifiers |
| `size_grp` | JKP size group (used to drop micro and nano caps) |
| `me`, `book_equity` | Market and book equity, millions of USD (log book-to-market is recomputed in layer 2) |
| `at_gr1`, `gp_at`, `ret_12_1`, `div_at`, `beta_60m` | The other characteristics used in the paper |
| All other characteristics | Kept for Figure 4, Panel B only |

The implemented pull saves two Parquet files in `_data/raw/jkp/`:
`jkp_raw.parquet` with the columns above, and `jkp_full_raw.parquet` with
`permco`, `eom`, `me`, and the 153 main JKP characteristics listed in
`data_manual/jkp_153_characteristics.csv`. The WRDS table is already
restricted to common, primary securities on the main exchange. `clean_jkp.py`
reads the raw files only through `load_jkp_raw()` and `load_jkp_full_raw()`.

## Open questions

- **Split adjustment factor (resolved):** `msf_v2.mthcumfacshr`, the cumulative factor to adjust shares. Both S12 and S34 use it to convert share quantities between dates.
- **Exact JKP column name (resolved):** dividends-to-assets is `div_at`.
- **Template pulls** (`pull_CRSP_Compustat.py`, FF factors, MSIX index): not needed by this design. Remove them unless a later step uses them.

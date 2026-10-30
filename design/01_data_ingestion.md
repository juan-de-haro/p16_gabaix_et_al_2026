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
| `raw_s34_holdings` | WRDS, Thomson Reuters `tfn.s34` | Quarterly | 13F institutional holdings |
| `raw_crsp_monthly` | WRDS, CRSP `crspm.msf_v2` | Monthly | Prices, returns, market cap |
| `raw_crsp_names` | WRDS, CRSP stock names | Event-based | CUSIP → PERMNO link, firm names |
| `raw_jkp_characteristics` | jkpfactors.com (US) | Monthly | Firm characteristics |

## Tables

### `raw_s12_holdings`

- **Script:** `pull_tr_holdings.py`
- **One row per:** file date × fund × security
- **Key:** `fdate`, `fundno`, `cusip`

| Column | Description |
|---|---|
| `fdate` | File date (date the holdings are valued at) |
| `rdate` | Report date |
| `fundno` | Fund identifier |
| `fundname` | Fund name |
| `cusip` | Security identifier |
| `shares` | Shares held |

### `raw_s34_holdings`

- **Script:** `pull_tr_holdings.py`
- **One row per:** file date × manager × security
- **Key:** `fdate`, `mgrno`, `cusip`

| Column | Description |
|---|---|
| `fdate` | File date |
| `rdate` | Report date |
| `mgrno` | Manager identifier |
| `mgrname` | Manager name |
| `typecode` | Manager type |
| `cusip` | Security identifier |
| `shares` | Shares held |

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

- **Script:** `pull_jkp.py`
- **One row per:** month × security
- **Key:** `eom`, `id` (`id` is the PERMNO for US stocks)

| Column | Description |
|---|---|
| `eom` | Month-end date |
| `id` | Security identifier (PERMNO) |
| `size_grp` | JKP size group (used to drop micro and nano caps) |
| `me`, `be_me`, `at_gr1`, `gp_at`, `ret_12_1`, `beta_60m`, dividend-to-assets | The characteristics used in the paper |
| All other characteristics | Kept for Figure 4, Panel B only |

## Open questions

- **Split adjustment factor:** which CRSP table provides it in the new CRSP format (`msf_v2`).
- **Exact JKP column name** for dividends-to-assets.
- **Template pulls** (`pull_CRSP_Compustat.py`, FF factors, MSIX index): not needed by this design. Remove them unless a later step uses them.

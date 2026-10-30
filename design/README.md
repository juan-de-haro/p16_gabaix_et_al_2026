# Data Pipeline Design

This folder describes how data flows from the raw sources to the inputs of each model and benchmark.

## Three layers

```
 Layer 1: RAW                Layer 2: CLEAN                 Layer 3: APPLICATION INPUTS
 (as downloaded)             (tidy, filtered, joined)       (one per model / benchmark)

 WRDS, jkpfactors.com  --->  holdings, firm_quarterly, ...  --->  RS matrix, BERT sequences,
                                                                  benchmark tables, ...
```

| Layer | What it does | Scripts | Output folder | Document |
|---|---|---|---|---|
| 1. Ingestion | Downloads source data. No changes. | `pull_*.py` | `_data/raw/` | [01_data_ingestion.md](01_data_ingestion.md) |
| 2. Processing | Applies the filters, joins, and winsorization from Appendix D. Produces tidy tables. | `clean_*.py` | `_data/clean/` | [02_data_processing.md](02_data_processing.md) |
| 3. Application inputs | Reshapes clean tables for each model and benchmark. | `make_*.py` | `_data/app/` | [03_application_inputs.md](03_application_inputs.md) (placeholder) |

Each layer reads **only** from the layer before it. No layer 3 script reads raw data.

## Conventions used in every layer

- **File format:** Parquet, one file per table.
- **Git:** no data files are committed. Everything in `_data/` is rebuilt by `doit`.
- **Date range:** set once in `settings.py` (`START_DATE`, `END_DATE`). No script hard-codes dates.
- **Common keys (from layer 2 on):**

| Key | Meaning |
|---|---|
| `quarter` | Quarter-end date (e.g. `2022-12-31`) |
| `month` | Month-end date |
| `permco` | Firm (CRSP company identifier; share classes combined) |
| `investor_id` | Investor, with a source prefix (`s12_1234`, `s34_5678`) |

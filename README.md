P16. Asset and Investor Embeddings
==================================

## About this project

A data science project

## Quick Start

### S12 data contribution

S12 scripts and tests live in `src/holdings_pipeline/`, alongside the S34/JKP
scripts. Raw data, processed data, local configuration, and generated reports
stay out of Git; each collaborator uses their own WRDS access to reproduce them.

For the S12 development run, set `WRDS_USERNAME`, `START_DATE=2022-01-01`,
and `END_DATE=2022-12-31` in your local, Git-ignored `.env`. Use your existing
WRDS PostgreSQL login configuration (and Duo when prompted). Then run:

```bash
uv sync --locked
uv run doit s12
uv run pytest -q src/holdings_pipeline/test_s12.py
```

This downloads every available vintage of S12 reports **dated in 2022**, caches
the supporting CRSP tables, and processes 2022Q4. Cached raw files are verified
and reused. Clean outputs are in `_data/clean/s12/2022Q4/`; open
`_output/s12/2022Q4/quality_report.html` for checks, counts, and assumptions.

These are S12-only inputs to the joint pipeline. S12/S34 deduplication,
JKP-based universe selection, the final 20x20 filter, and demeaning follow after
combining sources. See [processing details](design/02_data_processing.md#implemented-s12-contribution).

The optional ownership audit uses additional locally cached WRDS/SEC evidence;
its provenance and unresolved findings are documented in the processing notes.
The dependency lock pins PyArrow 19.0.1 for compatibility with NumPy 1.26.4.

### S34 and JKP data contribution

S34 (TR 13F institutional holdings) and JKP (firm characteristics) scripts and
tests live in `src/holdings_pipeline/`. They use the same `.env` settings as S12
and process the quarter of `END_DATE` (2022Q4 for the development run). On
Windows, also set `USER=<your name>` in `.env`, because `dodo.py` reads it.

```bash
uv run doit s34 jkp
uv run pytest -q src/holdings_pipeline/test_s34.py src/holdings_pipeline/test_jkp.py
```

Outputs in `_data/`:

- `s34/holdings_<quarter>.pkl`, `s34/investors_<quarter>.pkl`, `s34/log_<quarter>.pkl`
- `s34/security_<quarter>.pkl` (accepted security-level links for reconciliation)
- `firm_quarterly.pkl`, `firm_quarterly_jkp_full.pkl`, `filter_log_firms.pkl`

Units: S34 `dollar_holding` and `aum` are in USD; JKP `me` and `be` are in
millions of USD. Each script's docstring documents its processing rules.

Two TR data issues found while building S34 also apply to S12:

- Excluding foreign-incorporated firms (`usincflg = 'Y'`) drops US-listed
  ordinary shares that are in the JKP universe. In 2022Q4, 258 of the 259
  universe firms without S34 holdings were foreign-incorporated. S34 keeps them.
- From 2011, and heavily from 2017Q4, TR records some holdings under a CUSIP the
  security only received later (e.g. GE in 2017Q4 uses its post-2021 CUSIP).
  S34 adds a historical-CUSIP fallback; without it, about 8% of 2017Q4 holdings
  are lost.

S34 includes fund families that are also in S12 (e.g. Vanguard), so S34 and S12
must not be combined until the overlap rule is agreed. See
`src/holdings_pipeline/MERGE_NOTES.md`.

### Provisional 2022Q4 holdings integration

The pilot uses a common matching policy: a unique CRSP CUSIP link valid on the
holdings report date, followed by US-exchange-listed ordinary-share eligibility.
Foreign incorporation alone is not an exclusion. Ambiguous S34 direct CUSIPs and
multiple source CUSIPs resolving to one manager/security are quarantined rather
than arbitrarily selected or summed. S34's historical-CUSIP fallback is **off**
for the common pilot; set `S34_HISTORICAL_FALLBACK=True` only for a separately
labelled sensitivity run, then rerun `s34` and the combined task. Do not use
`S34_SKIP_EXISTING=True` while changing this matching policy.

After configuring local WRDS access and the 2022 date range above, run:

```bash
uv run doit holdings_provisional
uv run pytest -q src/holdings_pipeline/test_integrate_holdings.py
```

This writes `_data/clean/combined/2022Q4/holdings_provisional.parquet` and
`_output/combined/2022Q4/matching_audit.json` plus a local
`link_conflicts.parquet` review file. The audit includes source-level date-valid
match rates, same-date CUSIP link disagreements, source counts, and firm overlap.
The provisional table is withheld if the two sources disagree on a same-date
CUSIP link. A shared firm does **not**
prove that an S12 fund and S34 manager are distinct economic investors. This table
does not resolve their overlap, apply the final firm universe or 20-by-20 filter,
or provide final portfolio weights. It is an input to Juan's final-sample stage.

### Provisional 2022Q4 company characteristics

With the same ignored `.env` date range and WRDS access, run:

```bash
conda run -n finm python -m doit firm_characteristics
```

The full run order and manual command are at the top of
`src/holdings_pipeline/build_q4_firm_characteristics.py`. The task writes
`_data/clean/firm_characteristics/2022Q4/firm_quarterly_provisional.parquet`
with one row per eligible matched `quarter × permco`. It uses CRSP quarter-end
market equity as `me` and JKP's Compustat-derived book equity as `be`, both in
USD millions. The core JKP characteristics are market-equity weighted across
share classes. `log_be_me` is recomputed using the CRSP value.

Inspect `_output/firm_characteristics/2022Q4/coverage_audit.json` and
`firm_coverage.parquet` for unmatched CRSP/JKP firms, eligibility exclusions,
and inconsistent book equity within a `gvkey`. These are provisional inputs for
the initial valuation benchmark. Final sample filters and winsorization belong
after Juan's combined sample is agreed; the S12/S34 economic overlap remains
unresolved.

### Initial relative valuation benchmark

After building the provisional company table, run `python -m doit
relative_valuation` in the project environment. This evaluates the paper's
placebo, market-beta and four-characteristic reference models for 2022Q4.
Results, the shared firm split, and run metadata are saved locally under
`_output/relative_valuation/2022Q4/`. The [application input notes](design/03_application_inputs.md#initial-relative-valuation-pilot)
describe the score, pilot assumptions, and optional embedding input.

### Full project template

The quickest way to run code in this repo is to use the following steps.

You must have TexLive (or another LaTeX distribution) installed on your computer and available in your path.
You can do this by downloading and installing it from here ([windows](https://tug.org/texlive/windows.html#install)
and [mac](https://tug.org/mactex/mactex-download.html) installers).


This project uses [uv](https://docs.astral.sh/uv/) for dependency management. First, install uv if you haven't already:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then sync the environment:
```bash
uv sync
```

Finally, run the project tasks:
```bash
doit
```
And that's it!


### Other commands

#### Unit Tests and Doc Tests

You can run the unit test, including doctests, with the following command:
```
pytest --doctest-modules
```

You can build the documentation with:
```
rm ./src/.pytest_cache/README.md
jupyter-book build -W ./
```
Use `del` instead of rm on Windows


#### Setting Environment Variables

You can [export your environment variables](https://stackoverflow.com/questions/43267413/how-to-set-environment-variables-from-env-file)
from your `.env` files like so, if you wish. This can be done easily in a Linux or Mac terminal with the following command:
```bash
set -a  # automatically export all variables
source .env
set +a
```
On Windows (PowerShell):
```powershell
Get-Content .env | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') } }
```

### Formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting Python code.

```bash
# Auto-fix linting issues (e.g., unused imports, undefined names)
ruff check . --fix

# Format code (consistent style, spacing, line length)
ruff format .

# Sort imports, then fix linting issues, then format
ruff format . && ruff check --select I --fix . && ruff check --fix .
```

- `ruff check --fix` applies safe auto-fixes for linting violations
- `ruff format` formats code similar to Black
- `--select I` targets only import sorting rules (isort-compatible)

### General Directory Structure

 - The `assets` folder is used for things like hand-drawn figures or other
   pictures that were not generated from code. These things cannot be easily
   recreated if they are deleted.

 - The `_output` folder, on the other hand, contains dataframes and figures that are
   generated from code. The entire folder should be able to be deleted, because
   the code can be run again, which would again generate all of the contents.

 - The `data_manual` is for data that cannot be easily recreated. This data
   should be version controlled. Anything in the `_data` folder or in
   the `_output` folder should be able to be recreated by running the code
   and can safely be deleted.

 - I'm using the `doit` Python module as a task runner. It works like `make` and
   the associated `Makefile`s. To rerun the code, install `doit`
   (https://pydoit.org/) and execute the command `doit` from the `src`
   directory. Note that doit is very flexible and can be used to run code
   commands from the command prompt, thus making it suitable for projects that
   use scripts written in multiple different programming languages.

 - I'm using the `.env` file as a container for absolute paths that are private
   to each collaborator in the project. You can also use it for private
   credentials, if needed. It should not be tracked in Git.

### Data and Output Storage

I'll often use a separate folder for storing data. Any data in the data folder
can be deleted and recreated by rerunning the PyDoit command (the pulls are in
the dodo.py file). Any data that cannot be automatically recreated should be
stored in the "data_manual" folder. Because of the risk of manually-created data
getting changed or lost, I prefer to keep it under version control if I can.
Thus, data in the "_data" folder is excluded from Git (see the .gitignore file),
while the "data_manual" folder is tracked by Git.

Output is stored in the "_output" directory. This includes dataframes, charts, and
rendered notebooks. When the output is small enough, I'll keep this under
version control. I like this because I can keep track of how dataframes change as my
analysis progresses, for example.

Of course, the _data directory and _output directory can be kept elsewhere on the
machine. To make this easy, I always include the ability to customize these
locations by defining the path to these directories in environment variables,
which I intend to be defined in the `.env` file, though they can also simply be
defined on the command line or elsewhere. The `settings.py` is responsible for
loading these environment variables and doing some preprocessing on them.
The `settings.py` file is the entry point for all other scripts to these
definitions. That is, all code that references these variables and others are
loaded by importing `config`.

### Naming Conventions

 - **`pull_` vs `load_`**: Files or functions that pull data from an external
 data source are prepended with "pull_", as in "pull_fred.py". Functions that
 load data that has been cached in the "_data" folder are prepended with "load_".
 For example, inside of the `pull_CRSP_Compustat.py` file there is both a
 `pull_compustat` function and a `load_compustat` function. The first pulls from
 the web, whereas the other loads cached data from the "_data" directory.


### Dependencies and Virtual Environments

#### Working with `uv`

This project uses [uv](https://docs.astral.sh/uv/) for fast, reliable Python package management. Dependencies are defined in `pyproject.toml` and locked in `uv.lock`.

To sync your environment with the lockfile:
```bash
uv sync
```

To add a new dependency:
```bash
uv add <package-name>
```

To update all dependencies:
```bash
uv lock --upgrade
uv sync
```


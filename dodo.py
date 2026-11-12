"""Run or update the project. This file uses the `doit` Python package. It works
like a Makefile, but is Python-based

"""

#######################################
## Configuration and Helpers for PyDoit
#######################################
## Make sure the src folder is in the path
import sys

sys.path.insert(1, "./src/")

import shutil
from os import environ
from pathlib import Path

import pandas as pd

from settings import config

DOIT_CONFIG = {"backend": "sqlite3", "dep_file": "./.doit-db.sqlite"}


BASE_DIR = config("BASE_DIR")
DATA_DIR = config("DATA_DIR")
MANUAL_DATA_DIR = config("MANUAL_DATA_DIR")
OUTPUT_DIR = config("OUTPUT_DIR")
OS_TYPE = config("OS_TYPE")
USER = config("USER")

## Helpers for handling Jupyter Notebook tasks
environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"


# fmt: off
## Helper functions for automatic execution of Jupyter notebooks
def jupyter_execute_notebook(notebook_path):
    return (
        "jupyter nbconvert --execute --to notebook "
        f"--ClearMetadataPreprocessor.enabled=True --inplace {notebook_path}"
    )
def jupyter_to_html(notebook_path, output_dir=OUTPUT_DIR):
    return f"jupyter nbconvert --to html --output-dir={output_dir} {notebook_path}"
def jupyter_to_md(notebook_path, output_dir=OUTPUT_DIR):
    """Requires jupytext"""
    return f"jupytext --to markdown --output-dir={output_dir} {notebook_path}"
def jupyter_clear_output(notebook_path):
    """Clear the output of a notebook"""
    return (
        "jupyter nbconvert --ClearOutputPreprocessor.enabled=True "
        f"--ClearMetadataPreprocessor.enabled=True --inplace {notebook_path}"
    )
def log_timestamp(label, notebook):
    """Shell command that prints a timestamped progress line to stderr"""
    return (
        'python -c "import sys; from datetime import datetime; '
        f"print(f'{label} {notebook}: {{datetime.now()}}', file=sys.stderr)\" "
    )
# fmt: on


def mv(from_path, to_path):
    """Move a file to a folder"""
    from_path = Path(from_path)
    to_path = Path(to_path)
    to_path.mkdir(parents=True, exist_ok=True)
    if OS_TYPE == "nix":
        command = f"mv {from_path} {to_path}"
    else:
        command = f"move {from_path} {to_path}"
    return command


def copy_file(origin_path, destination_path, mkdir=True):
    """Create a Python action for copying a file."""

    def _copy_file():
        origin = Path(origin_path)
        dest = Path(destination_path)
        if mkdir:
            dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, dest)

    return _copy_file


##################################
## Begin rest of PyDoit tasks here
##################################


def task_config():
    """Create empty directories for data and output if they don't exist"""
    return {
        "actions": ["python ./src/settings.py"],
        "targets": [DATA_DIR, OUTPUT_DIR],
        "file_dep": ["./src/settings.py"],
        "clean": [],
    }


def task_pull():
    """Pull data from external sources"""
    yield {
        "name": "crsp_stock",
        "doc": "Pull CRSP stock data from WRDS",
        "actions": [
            "python ./src/settings.py",
            "python ./src/pull_CRSP_stock.py",
        ],
        "targets": [DATA_DIR / "CRSP_monthly_stock.parquet"],
        "file_dep": ["./src/settings.py", "./src/pull_CRSP_stock.py"],
        "clean": [],
    }
    yield {
        "name": "crsp_compustat",
        "doc": "Pull CRSP Compustat data from WRDS",
        "actions": [
            "python ./src/settings.py",
            "python ./src/pull_CRSP_Compustat.py",
        ],
        "targets": [DATA_DIR / "CRSP_Compustat.parquet"],
        "file_dep": ["./src/settings.py", "./src/pull_CRSP_Compustat.py"],
        "clean": [],
    }


def task_s12():
    """Download the configured S12 sample, then process its final quarter."""
    from doit.tools import config_changed

    from holdings_pipeline.pull_s12 import raw_directory, sample_dates

    start, end = sample_dates()
    quarter = str(end.to_period("Q"))
    raw = raw_directory(start, end)
    inputs = [
        raw / f"raw_{name}.parquet"
        for name in ("s12_holdings", "crsp_monthly", "crsp_names")
    ]
    manifests = [path.with_suffix(".json") for path in inputs]
    settings = {"start": str(start.date()), "end": str(end.date()), "quarter": quarter}
    yield {
        "name": "pull",
        "doc": "Cache S12 reports and CRSP inputs without cleaning",
        "actions": [[sys.executable, "src/holdings_pipeline/pull_s12.py"]],
        "file_dep": ["src/holdings_pipeline/pull_s12.py", "src/settings.py"],
        "targets": inputs + manifests,
        "uptodate": [config_changed(settings)],
        "clean": [],
    }
    clean_dir = DATA_DIR / "clean" / "s12" / quarter
    report_dir = OUTPUT_DIR / "s12" / quarter
    yield {
        "name": "process",
        "doc": "Clean and validate the S12-only contribution before combined filters",
        "actions": [
            [sys.executable, "src/holdings_pipeline/clean_s12.py", "--quarter", quarter]
        ],
        "file_dep": inputs
        + manifests
        + [
            "src/holdings_pipeline/clean_s12.py",
            "src/holdings_pipeline/pull_s12.py",
            "src/settings.py",
        ],
        "task_dep": ["s12:pull"],
        "targets": [
            clean_dir / f"{name}.parquet"
            for name in (
                "holdings_s12",
                "holdings_s12_security",
                "investors_s12",
                "firm_names_s12",
                "filter_log_s12",
                "selected_reports_s12",
            )
        ]
        + [
            report_dir / "validation.json",
            report_dir / "quality_report.html",
            report_dir / "exceptions" / "ownership_security_review.parquet",
            report_dir / "exceptions" / "ownership_report_dates.parquet",
        ],
        "uptodate": [config_changed(settings)],
        "clean": [],
    }


def _pipeline_quarter_end():
    """Quarter-end date of END_DATE, shared by the S12, S34, and JKP tasks."""
    from holdings_pipeline.pull_s12 import sample_dates

    _, end = sample_dates()
    return end.to_period("Q").end_time.strftime("%Y-%m-%d")


def task_s34():
    """Pull and clean S34 for the same quarter as the S12 task."""
    from doit.tools import config_changed

    q = _pipeline_quarter_end()
    s34_dir = DATA_DIR / "s34"
    return {
        "actions": [
            [
                sys.executable,
                "src/holdings_pipeline/pull_s34.py",
                f"--S34_START={q}",
                f"--S34_END={q}",
            ]
        ],
        "file_dep": ["src/holdings_pipeline/pull_s34.py", "src/settings.py"],
        "targets": [
            s34_dir / f"holdings_{q}.pkl",
            s34_dir / f"investors_{q}.pkl",
            s34_dir / f"log_{q}.pkl",
            s34_dir / f"security_{q}.pkl",
            DATA_DIR / "crsp_cusip_map.pkl",
            DATA_DIR / "filter_log_s34.pkl",
        ],
        "uptodate": [
            config_changed(
                {
                    "quarter": q,
                    "historical_fallback": config(
                        "S34_HISTORICAL_FALLBACK", default="False", cast=str
                    ),
                }
            )
        ],
        "clean": [],
    }


def task_jkp():
    """Pull JKP characteristics and build the firm tables for the same quarter."""
    from doit.tools import config_changed

    q = _pipeline_quarter_end()
    raw = [DATA_DIR / "jkp_raw.pkl", DATA_DIR / "jkp_full_raw.pkl"]
    yield {
        "name": "pull",
        "doc": "Pull JKP characteristics from WRDS",
        "actions": [
            [
                sys.executable,
                "src/holdings_pipeline/pull_jkp.py",
                f"--JKP_START={q}",
                f"--JKP_END={q}",
            ]
        ],
        "file_dep": [
            "src/holdings_pipeline/pull_jkp.py",
            "src/settings.py",
            MANUAL_DATA_DIR / "jkp_153_characteristics.csv",
        ],
        "targets": raw,
        "uptodate": [config_changed({"quarter": q})],
        "clean": [],
    }
    yield {
        "name": "clean",
        "doc": "Build firm_quarterly and firm_quarterly_jkp_full",
        "actions": [[sys.executable, "src/holdings_pipeline/clean_jkp.py"]],
        "file_dep": raw
        + [
            "src/holdings_pipeline/clean_jkp.py",
            "src/holdings_pipeline/pull_jkp.py",
            "src/settings.py",
        ],
        "task_dep": ["jkp:pull"],
        "targets": [
            DATA_DIR / "firm_quarterly.pkl",
            DATA_DIR / "firm_quarterly_jkp_full.pkl",
            DATA_DIR / "filter_log_firms.pkl",
        ],
        "clean": [],
    }


def task_holdings_provisional():
    """Combine date-valid S12/S34 firm holdings without economic deduplication."""
    from doit.tools import config_changed

    quarter = str(pd.Period(_pipeline_quarter_end(), freq="Q"))
    qdate = _pipeline_quarter_end()
    return {
        "actions": [
            [
                sys.executable,
                "src/holdings_pipeline/integrate_holdings.py",
                "--quarter",
                quarter,
            ]
        ],
        "file_dep": [
            DATA_DIR / "clean" / "s12" / quarter / "holdings_s12.parquet",
            DATA_DIR / "clean" / "s12" / quarter / "filter_log_s12.parquet",
            DATA_DIR / "clean" / "s12" / quarter / "holdings_s12_security.parquet",
            DATA_DIR / "s34" / f"holdings_{qdate}.pkl",
            DATA_DIR / "s34" / f"log_{qdate}.pkl",
            DATA_DIR / "s34" / f"security_{qdate}.pkl",
            "src/holdings_pipeline/integrate_holdings.py",
        ],
        "task_dep": ["s12:process", "s34"],
        "targets": [
            DATA_DIR / "clean" / "combined" / quarter / "holdings_provisional.parquet",
            OUTPUT_DIR / "combined" / quarter / "matching_audit.json",
            OUTPUT_DIR / "combined" / quarter / "link_conflicts.parquet",
        ],
        "uptodate": [config_changed({"quarter": quarter})],
        "clean": [],
    }


def task_firm_characteristics():
    """Build CRSP-authoritative Q4 characteristics for matched holdings firms."""
    from doit.tools import config_changed

    from holdings_pipeline.pull_s12 import raw_directory, sample_dates

    start, end = sample_dates()
    quarter = str(end.to_period("Q"))
    return {
        "actions": [
            [
                sys.executable,
                "src/holdings_pipeline/build_q4_firm_characteristics.py",
                "--quarter",
                quarter,
            ]
        ],
        "file_dep": [
            DATA_DIR / "clean" / "combined" / quarter / "holdings_provisional.parquet",
            DATA_DIR / "jkp_raw.pkl",
            raw_directory(start, end) / "raw_crsp_monthly.parquet",
            "src/holdings_pipeline/build_q4_firm_characteristics.py",
            "src/holdings_pipeline/clean_s12.py",
        ],
        "task_dep": ["holdings_provisional", "jkp:pull"],
        "targets": [
            DATA_DIR
            / "clean"
            / "firm_characteristics"
            / quarter
            / "firm_quarterly_provisional.parquet",
            OUTPUT_DIR / "firm_characteristics" / quarter / "firm_coverage.parquet",
            OUTPUT_DIR / "firm_characteristics" / quarter / "coverage_audit.json",
        ],
        "uptodate": [
            config_changed(
                {"quarter": quarter, "start": str(start.date()), "end": str(end.date())}
            )
        ],
        "clean": [],
    }


def task_relative_valuation():
    """Run provisional single-quarter relative valuation reference models."""
    from doit.tools import config_changed

    quarter = str(pd.Period(_pipeline_quarter_end(), freq="Q"))
    report = OUTPUT_DIR / "relative_valuation" / quarter
    return {
        "actions": [
            [
                sys.executable,
                "src/valuation/relative_valuation.py",
                "--quarter",
                quarter,
            ]
        ],
        "file_dep": [
            DATA_DIR
            / "clean"
            / "firm_characteristics"
            / quarter
            / "firm_quarterly_provisional.parquet",
            "src/valuation/relative_valuation.py",
        ],
        "task_dep": ["firm_characteristics"],
        "targets": [
            report / "model_metrics.csv",
            report / "firm_predictions.parquet",
            report / "run_metadata.json",
        ],
        "uptodate": [config_changed({"quarter": quarter, "seed": 32800})],
        "clean": [],
    }


def task_summary_stats():
    """Generate summary statistics tables"""
    file_dep = ["./src/example_table.py"]
    file_output = [
        "example_table.tex",
        "pandas_to_latex_simple_table1.tex",
    ]
    targets = [OUTPUT_DIR / file for file in file_output]

    return {
        "actions": [
            "python ./src/example_table.py",
            "python ./src/pandas_to_latex_demo.py",
        ],
        "targets": targets,
        "file_dep": file_dep,
        "clean": True,
    }


def task_example_plot():
    """Example plots"""
    file_dep = [Path("./src") / file for file in ["example_plot.py"]]
    file_output = ["example_plot.png"]
    targets = [OUTPUT_DIR / file for file in file_output]

    return {
        "actions": [
            "python ./src/example_plot.py",
        ],
        "targets": targets,
        "file_dep": file_dep,
        "clean": True,
    }


notebook_tasks = {
    "01_example_notebook_interactive.ipynb.py": {
        "path": "./src/01_example_notebook_interactive.ipynb.py",
        "file_dep": [],
        "targets": [],
    },
}


# fmt: off
def task_run_notebooks():
    """Preps the notebooks for presentation format.
    Execute notebooks if the script version of it has been changed.
    """
    for notebook in notebook_tasks.keys():
        pyfile_path = Path(notebook_tasks[notebook]["path"])
        notebook_path = pyfile_path.with_suffix("")  # strips .py, leaves .ipynb
        notebook_name = notebook_path.stem  # e.g. "01_example_notebook_interactive"
        yield {
            "name": notebook,
            "actions": [
                log_timestamp("Start", notebook),
                f"jupytext --to notebook --output {notebook_path} {pyfile_path}",
                jupyter_execute_notebook(notebook_path),
                jupyter_to_html(notebook_path),
                mv(notebook_path, OUTPUT_DIR),
                log_timestamp("End", notebook),
            ],
            "file_dep": [
                pyfile_path,
                *notebook_tasks[notebook]["file_dep"],
            ],
            "targets": [
                OUTPUT_DIR / f"{notebook_name}.html",
                *notebook_tasks[notebook]["targets"],
            ],
            "clean": True,
        }
# fmt: on

###############################################################
## Task below is for LaTeX compilation
###############################################################


def task_compile_latex_docs():
    """Compile the LaTeX documents to PDFs"""
    file_dep = [
        "./reports/report_example.tex",
        "./reports/my_article_header.sty",
        "./reports/slides_example.tex",
        "./reports/my_beamer_header.sty",
        "./reports/my_common_header.sty",
        "./reports/report_simple_example.tex",
        "./reports/slides_simple_example.tex",
        # Generated inputs. The reports typeset a placeholder for a missing one
        # instead of failing, so list them here to have doit build them first.
        OUTPUT_DIR / "example_plot.png",
        OUTPUT_DIR / "example_table.tex",
        OUTPUT_DIR / "pandas_to_latex_simple_table1.tex",
    ]
    targets = [
        "./reports/report_example.pdf",
        "./reports/slides_example.pdf",
        "./reports/report_simple_example.pdf",
        "./reports/slides_simple_example.pdf",
    ]

    return {
        # For each .tex file: compile, then clean auxiliary files.
        # report_example / slides_example are my custom LaTeX templates;
        # the *_simple_* ones are small adjustments to Overleaf templates.
        "actions": [
            f"latexmk -xelatex -halt-on-error {flag}-cd {tex}"
            for tex in [
                "./reports/report_example.tex",
                "./reports/slides_example.tex",
                "./reports/report_simple_example.tex",
                "./reports/slides_simple_example.tex",
            ]
            for flag in ["", "-c "]
        ],
        "targets": targets,
        "file_dep": file_dep,
        "clean": True,
    }


sphinx_targets = [
    "./docs/index.html",
]


def task_build_chartbook_site():
    """Compile Sphinx Docs"""
    notebook_scripts = [
        Path(notebook_tasks[notebook]["path"]) for notebook in notebook_tasks.keys()
    ]
    file_dep = [
        "./README.md",
        "./chartbook.toml",
        *notebook_scripts,
    ]

    return {
        "actions": [
            "chartbook build -f",
        ],  # Use docs as build destination
        "targets": sphinx_targets,
        "file_dep": file_dep,
        "task_dep": [
            "run_notebooks",
        ],
        "clean": True,
    }


def task_run_pytest():
    """Run pytest and save results to OUTPUT_DIR"""
    src_py_files = list(Path("./src").glob("*.py"))
    test_output = OUTPUT_DIR / "pytest_results.xml"

    def run_pytest():
        import subprocess

        result = subprocess.run(
            ["pytest", f"--junitxml={test_output}"],
        )
        if result.returncode != 0:
            # Remove the XML so doit won't consider the target up-to-date
            Path(test_output).unlink(missing_ok=True)
            raise RuntimeError(f"pytest failed with exit code {result.returncode}")

    return {
        "actions": [run_pytest],
        "targets": [test_output],
        "file_dep": src_py_files,
        "clean": True,
        "verbosity": 2,
    }

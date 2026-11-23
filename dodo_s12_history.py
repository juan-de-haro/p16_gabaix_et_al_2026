"""Rebuild the 2005Q1–2022Q4 S12 panel with the existing quarter cleaner."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from holdings_pipeline.pull_s12 import file_hash, pull, raw_directory
from holdings_pipeline.s12_history import OUTPUT_FILES, QUARTERS, YEARS, summarize
from settings import config

DATA = Path(config("DATA_DIR"))
OUTPUT = Path(config("OUTPUT_DIR"))
SRC = Path("src/holdings_pipeline")
DOIT_CONFIG = {
    "default_tasks": ["summary"],
    "dep_file": ".doit-db.sqlite",
    "backend": "sqlite3",
}


def annual_inputs(year):
    """Return the three annual cache paths used by the quarterly cleaner."""
    directory = raw_directory(f"{year}-01-01", f"{year}-12-31")
    return [
        directory / f"raw_{name}.parquet"
        for name in ("s12_holdings", "crsp_monthly", "crsp_names")
    ]


def receipt_path(quarter):
    """Locate the saved WRDS row counts consumed by clean_s12."""
    return OUTPUT / "s12" / quarter / "source_row_counts.json"


def pull_year(year):
    """Download or verify the annual caches and save the source counts."""
    receipt = pull(f"{year}-01-01", f"{year}-12-31", count_year=True)
    for path in annual_inputs(year):
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if file_hash(path) != meta["sha256"]:
            raise ValueError(f"Cache checksum mismatch: {path.name}")
    for q in range(1, 5):
        path = receipt_path(f"{year}Q{q}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def task_annual():
    """Acquire a full report-date year, all vintages and supporting CRSP tables."""
    for year in YEARS:
        raw = annual_inputs(year)
        yield {
            "name": str(year),
            "actions": [(pull_year, [year])],
            "file_dep": [__file__, SRC / "pull_s12.py", "src/settings.py"],
            "targets": raw
            + [p.with_suffix(".json") for p in raw]
            + [receipt_path(f"{year}Q{i}") for i in range(1, 5)],
        }


def task_quarter():
    """Process one quarter in a fresh Python process using its annual cache."""
    for q in QUARTERS:
        year = int(q[:4])
        raw = annual_inputs(year)
        data = DATA / "clean/s12" / q
        report = OUTPUT / "s12" / q
        yield {
            "name": q,
            "task_dep": [f"annual:{year}"],
            "actions": [
                [
                    sys.executable,
                    str(SRC / "clean_s12.py"),
                    "--start-date",
                    f"{year}-01-01",
                    "--end-date",
                    f"{year}-12-31",
                    "--quarter",
                    q,
                ]
            ],
            "file_dep": [
                __file__,
                SRC / "clean_s12.py",
                SRC / "pull_s12.py",
                "src/settings.py",
                receipt_path(q),
                *raw,
                *(p.with_suffix(".json") for p in raw),
            ],
            "targets": [
                data / f"{name}.parquet"
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
                report / "validation.json",
                report / "quality_report.html",
                report / "exceptions/ownership_above_one.parquet",
            ],
        }


def task_summary():
    """Build both quarterly summary tables from the full historical panel."""
    return {
        "actions": [summarize],
        "task_dep": [f"quarter:{q}" for q in QUARTERS],
        "file_dep": [
            SRC / "s12_history.py",
            *(DATA / "clean/s12" / q / "holdings_s12.parquet" for q in QUARTERS),
            *(OUTPUT / "s12" / q / "validation.json" for q in QUARTERS),
        ],
        "targets": [OUTPUT / "s12_history" / name for name in OUTPUT_FILES],
    }

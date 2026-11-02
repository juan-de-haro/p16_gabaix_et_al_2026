"""Cache unmodified S12 reports and the CRSP inputs needed to value them.

The sample is selected on RDATE, not FDATE. All available vintages of those
reports are retained. Selection of the first vintage belongs to clean_s12.py.
PostgreSQL server-side cursors keep the download's memory use bounded.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq

from settings import config


def sample_dates(start=None, end=None):
    start = pd.Timestamp(start if start is not None else config("START_DATE"))
    end = pd.Timestamp(end if end is not None else config("END_DATE"))
    if start > end:
        raise ValueError("The start date must not be after the end date.")
    return start, end


def raw_directory(start=None, end=None, data_dir=None):
    start, end = sample_dates(start, end)
    tag = f"{start:%Y%m%d}_{end:%Y%m%d}"
    return Path(data_dir or config("DATA_DIR")) / "raw" / "s12" / tag


def load_raw_table(name, start=None, end=None, data_dir=None, filters=None):
    allowed = {"s12_holdings", "crsp_monthly", "crsp_names"}
    if name not in allowed:
        raise ValueError(f"Unknown S12 input: {name}")
    return pd.read_parquet(
        raw_directory(start, end, data_dir) / f"raw_{name}.parquet",
        filters=filters,
    )


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def arrow_type(column):
    """Preserve PostgreSQL values, including exact decimals and date types."""
    types = {
        16: pa.bool_(),
        20: pa.int64(),
        21: pa.int16(),
        23: pa.int32(),
        700: pa.float32(),
        701: pa.float64(),
        1082: pa.date32(),
        1114: pa.timestamp("us"),
        1184: pa.timestamp("us", tz="UTC"),
        18: pa.string(),
        19: pa.string(),
        25: pa.string(),
        1042: pa.string(),
        1043: pa.string(),
    }
    if column.type_code == 1700:
        return pa.decimal128(column.precision or 38, column.scale or 0)
    if column.type_code not in types:
        raise TypeError(
            f"Unsupported PostgreSQL type {column.type_code}: {column.name}"
        )
    return types[column.type_code]


def export_query(connection, query, params, target, force=False):
    """Atomically stream one source query to Parquet with a provenance sidecar."""
    target = Path(target)
    manifest_path = target.with_suffix(".json")
    if target.exists() and manifest_path.exists() and not force:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            metadata["query"] == query
            and metadata["params"] == params
            and metadata["sha256"] == file_hash(target)
        ):
            print(f"Cached {target.name}: {metadata['rows']:,} rows", flush=True)
            return metadata
        raise ValueError(
            f"Cache provenance mismatch for {target}; use --force to replace."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".parquet.part")
    rows_written = 0
    writer = None
    try:
        with connection.cursor(name="s12_export") as cursor:
            cursor.itersize = 100_000
            cursor.execute(query, params)
            while True:
                rows = cursor.fetchmany(100_000)
                if writer is None:
                    schema = pa.schema(
                        [
                            pa.field(column.name, arrow_type(column))
                            for column in cursor.description
                        ]
                    )
                    writer = pq.ParquetWriter(temporary, schema, compression="zstd")
                if not rows:
                    break
                arrays = [
                    pa.array(values, type=field.type)
                    for values, field in zip(zip(*rows), schema)
                ]
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                rows_written += len(rows)
                print(f"{target.name}: {rows_written:,} rows", flush=True)
        writer.close()
        writer = None
        temporary.replace(target)
    finally:
        if writer is not None:
            writer.close()
        if temporary.exists():
            temporary.unlink()
        connection.rollback()
    metadata = {
        "query": query,
        "params": params,
        "rows": rows_written,
        "columns": schema.names,
        "sha256": file_hash(target),
        "bytes": target.stat().st_size,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": "Only source columns and date restrictions; no cleaning or joins.",
    }
    manifest_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def pull(start=None, end=None, force=False):
    start, end = sample_dates(start, end)
    directory = raw_directory(start, end)
    params = {"start": start.date().isoformat(), "end": end.date().isoformat()}
    # pgpass is used by libpq; passwords are never put in code or manifests.
    connection = psycopg2.connect(
        host="wrds-pgdata.wharton.upenn.edu",
        port=9737,
        dbname="wrds",
        user=config("WRDS_USERNAME"),
        sslmode="require",
        connect_timeout=30,
        application_name="p16_s12_pull",
        options="-c default_transaction_read_only=on -c statement_timeout=600000",
    )
    try:
        export_query(
            connection,
            """
            SELECT fdate, rdate, fundno, fundname, cusip, shares, country, ioc
            FROM tfn.s12
            WHERE rdate BETWEEN %(start)s AND %(end)s
        """,
            params,
            directory / "raw_s12_holdings.parquet",
            force,
        )
        # A report can be repeated in later FDATE vintages. Fetch split factors
        # through the last vintage without dropping those raw observations.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT min(fdate), max(fdate) FROM tfn.s12type1
                WHERE rdate BETWEEN %(start)s AND %(end)s
            """,
                params,
            )
            first_vintage, last_vintage = cursor.fetchone()
        connection.rollback()
        if first_vintage is None:
            raise ValueError("No S12 reports exist for the requested date range.")
        crsp_params = {
            "start": min(start.date(), first_vintage).isoformat(),
            "end": max(end.date(), last_vintage).isoformat(),
        }
        export_query(
            connection,
            """
            SELECT permno, permco, mthcaldt, mthprc, mthprcdt, shrout,
                   mthcumfacshr, mthret, cusip, issuernm, securitytype,
                   securitysubtype, sharetype, usincflg, issuertype,
                   primaryexch, tradingstatusflg, conditionaltype
            FROM crspm.msf_v2
            WHERE mthcaldt BETWEEN %(start)s AND %(end)s
        """,
            crsp_params,
            directory / "raw_crsp_monthly.parquet",
            force,
        )
        export_query(
            connection,
            """
            SELECT permno, permco, ncusip, namedt, nameendt, comnam, shrcd, exchcd
            FROM crspm.msenames
            WHERE nameendt >= %(start)s AND namedt <= %(end)s
        """,
            crsp_params,
            directory / "raw_crsp_names.parquet",
            force,
        )
    finally:
        connection.close()
    print(f"Raw inputs saved to {directory}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    pull(arguments.start_date, arguments.end_date, arguments.force)

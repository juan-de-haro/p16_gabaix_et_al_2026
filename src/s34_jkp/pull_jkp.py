"""Pull Jensen-Kelly-Pedersen (JKP) firm characteristics from WRDS.

Source: contrib_global_factor.global_factor, US stocks from CRSP only
(excntry = 'USA', source_crsp = 1), last month of each quarter. The WRDS
table is already restricted to common, primary securities on the main
exchange (common, primary_sec, obs_main, exch_main all equal 1).

Units: JKP market equity (me, market_equity) and book_equity are in
millions of USD.

Outputs (in DATA_DIR):
    jkp_raw.pkl       characteristics used in the paper (Appendix D.1.2)
    jkp_full_raw.pkl  the 153 main JKP characteristics (Figure 4, Panel B),
                      listed in data_manual/jkp_153_characteristics.csv
                      (from bkelly-lab/ReplicationCrisis, Factor Details.xlsx)

Usage (from the project root). The default is the 2022Q4 pilot:

    uv run python src/s34_jkp/pull_jkp.py

Full history:

    uv run python src/s34_jkp/pull_jkp.py --JKP_START=2005-03-31 --JKP_END=2025-12-31

JKP on WRDS currently ends in 2025-12.
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import wrds

from settings import config

DATA_DIR = Path(config("DATA_DIR"))
MANUAL_DATA_DIR = Path(config("MANUAL_DATA_DIR"))
WRDS_USERNAME = config("WRDS_USERNAME")
JKP_START = config("JKP_START", default="2022-12-31", cast=str)
JKP_END = config("JKP_END", default="2022-12-31", cast=str)

BASE_COLS = ["permno", "permco", "gvkey", "eom", "size_grp", "me", "book_equity"]
CHAR_COLS = ["at_gr1", "gp_at", "ret_12_1", "div_at", "beta_60m"]


def load_chars153(manual_dir=MANUAL_DATA_DIR):
    path = Path(manual_dir) / "jkp_153_characteristics.csv"
    return pd.read_csv(path)["characteristic"].tolist()


def pull_jkp(db, start, end, cols):
    """Pull quarter-end JKP rows between start and end, one year at a time."""
    parts = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        lo = max(start, f"{y}-01-01")
        hi = min(end, f"{y}-12-31")
        df = db.raw_sql(
            f"""
            SELECT {", ".join(cols)}
            FROM contrib_global_factor.global_factor
            WHERE excntry = 'USA' AND source_crsp = 1
              AND eom BETWEEN '{lo}' AND '{hi}'
              AND EXTRACT(MONTH FROM eom) IN (3, 6, 9, 12)
            """,
            date_cols=["eom"],
        )
        parts.append(df)
        print(y, len(df))
    return pd.concat(parts, ignore_index=True)


def load_jkp_raw(data_dir=DATA_DIR):
    return pd.read_pickle(Path(data_dir) / "jkp_raw.pkl")


def load_jkp_full_raw(data_dir=DATA_DIR):
    return pd.read_pickle(Path(data_dir) / "jkp_full_raw.pkl")


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = wrds.Connection(wrds_username=WRDS_USERNAME)

    jkp = pull_jkp(db, JKP_START, JKP_END, BASE_COLS + CHAR_COLS)
    jkp.to_pickle(DATA_DIR / "jkp_raw.pkl")

    chars153 = load_chars153()
    jkp_full = pull_jkp(db, JKP_START, JKP_END, ["permco", "eom", "me"] + chars153)
    jkp_full.to_pickle(DATA_DIR / "jkp_full_raw.pkl")

    db.close()

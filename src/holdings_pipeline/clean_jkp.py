"""Build firm_quarterly and firm_quarterly_jkp_full from raw JKP data.

Implements step 5 of design/02_data_processing.md (firm universe) and the
characteristic winsorizing in step 7, following Appendix D.1.2.

Aggregation from PERMNO to PERMCO:
    me              sum over PERMNOs (equals JKP me_company and CRSP mthcap)
    be              book_equity counted once per gvkey, then summed over gvkeys
                    (Berkshire A/B share one gvkey; Liberty Media tracking
                    stocks have separate gvkeys)
    log_be_me       recomputed as log(be) - log(me), not JKP be_me, because
                    JKP be_me divides a tracking stock's book equity by the
                    whole company's market equity
    other ratios    market-equity weighted average over PERMNOs
    size_grp        a firm is kept if any PERMNO is small, large, or mega;
                    the firm's size_grp is its largest group

Universe filters, in order: drop micro/nano, drop be <= 0, drop missing
characteristics. Then winsorize each quarter: log_be_me, at_gr1, gp_at,
ret_12_1 at 1%/99%; div_at at 99% only; beta_60m not winsorized.

Units: me and be are in millions of USD.

Outputs (in DATA_DIR):
    firm_quarterly.pkl           quarter x permco
    firm_quarterly_jkp_full.pkl  quarter x permco, 153 characteristics,
                                 same universe as firm_quarterly, not winsorized
    filter_log_firms.pkl         quarter, step, n_obs, n_investors, n_firms

Usage (after pull_jkp.py):

    uv run python src/holdings_pipeline/clean_jkp.py
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from pull_jkp import load_chars153, load_jkp_full_raw, load_jkp_raw
from settings import config

DATA_DIR = Path(config("DATA_DIR"))

CHARS = ["at_gr1", "gp_at", "div_at", "ret_12_1", "beta_60m"]
SIZE_ORDER = {"nano": 0, "micro": 1, "small": 2, "large": 3, "mega": 4}


def _me_weighted_mean(df, keys, cols):
    """Market-equity weighted mean of cols within keys, ignoring missing values."""
    x, w = {}, {}
    for c in cols:
        ok = df[c].notna() & df["me"].notna()
        x[c] = (df[c] * df["me"]).where(ok)
        w[c] = df["me"].where(ok)
    xs = pd.concat([df[keys], pd.DataFrame(x)], axis=1).groupby(keys).sum(min_count=1)
    ws = pd.concat([df[keys], pd.DataFrame(w)], axis=1).groupby(keys).sum(min_count=1)
    return xs / ws


def build_firm_quarterly(jkp):
    """Aggregate JKP to PERMCO, apply universe filters, and winsorize.

    Returns (firm_quarterly, filter_log).
    """
    keys = ["eom", "permco"]

    me = jkp.groupby(keys)["me"].sum(min_count=1)
    be = (
        jkp.drop_duplicates(subset=keys + ["gvkey"])
        .groupby(keys)["book_equity"]
        .sum(min_count=1)
    )
    wavg = _me_weighted_mean(jkp, keys, CHARS)

    rev = {v: k for k, v in SIZE_ORDER.items()}
    r = jkp["size_grp"].map(SIZE_ORDER)
    by = [jkp[k] for k in keys]
    keep_size = (r >= SIZE_ORDER["small"]).groupby(by).any()
    size_grp = r.groupby(by).max().map(rev)

    firm = (
        pd.concat(
            {"me": me, "be": be, "keep_size": keep_size, "size_grp": size_grp}, axis=1
        )
        .join(wavg)
        .reset_index()
    )
    firm["permco"] = firm["permco"].astype(int)
    firm["log_me"] = np.log(firm["me"])
    firm["log_be"] = np.log(firm["be"].where(firm["be"] > 0))
    firm["log_be_me"] = firm["log_be"] - firm["log_me"]

    req = ["me", "be", "log_be_me"] + CHARS
    steps = [("5a_aggregate_permco", firm)]
    f = firm[firm["keep_size"]]
    steps.append(("5b_drop_micro_nano", f))
    f = f[f["be"] > 0]
    steps.append(("5c_drop_nonpositive_be", f))
    f = f.dropna(subset=req)
    steps.append(("5d_drop_missing_chars", f))

    log = pd.concat(
        [
            s.groupby("eom").size().rename("n_firms").reset_index().assign(step=name)
            for name, s in steps
        ],
        ignore_index=True,
    ).rename(columns={"eom": "quarter"})
    log["n_obs"] = log["n_firms"]
    log["n_investors"] = np.nan
    log = log[["quarter", "step", "n_obs", "n_investors", "n_firms"]]

    f = f.copy()
    for c in ["log_be_me", "at_gr1", "gp_at", "ret_12_1"]:
        g = f.groupby("eom")[c]
        f[c] = f[c].clip(
            g.transform(lambda s: s.quantile(0.01)),
            g.transform(lambda s: s.quantile(0.99)),
        )
    g = f.groupby("eom")["div_at"]
    f["div_at"] = f["div_at"].clip(upper=g.transform(lambda s: s.quantile(0.99)))

    f = f.rename(columns={"eom": "quarter"})
    cols_out = [
        "quarter", "permco", "me", "log_me", "be", "log_be", "log_be_me",
        "at_gr1", "gp_at", "ret_12_1", "div_at", "beta_60m", "size_grp",
    ]
    return f[cols_out].reset_index(drop=True), log


def build_jkp_full(df, universe, chars153):
    """Aggregate the 153 characteristics to PERMCO and keep the firm universe."""
    keys = ["eom", "permco"]
    ratio_chars = [c for c in chars153 if c != "market_equity"]

    out = _me_weighted_mean(df, keys, ratio_chars)
    out["market_equity"] = df.groupby(keys)["market_equity"].sum(min_count=1)
    out = out.reset_index().rename(columns={"eom": "quarter"})
    out["permco"] = out["permco"].astype(int)

    out = out.merge(universe[["quarter", "permco"]], on=["quarter", "permco"])
    return out[["quarter", "permco"] + chars153]


if __name__ == "__main__":
    fq, log = build_firm_quarterly(load_jkp_raw())
    fq.to_pickle(DATA_DIR / "firm_quarterly.pkl")
    log.to_pickle(DATA_DIR / "filter_log_firms.pkl")
    print("firm_quarterly:", fq.shape)

    chars153 = load_chars153()
    raw = load_jkp_full_raw()
    full = pd.concat(
        [build_jkp_full(g, fq, chars153) for _, g in raw.groupby(raw["eom"].dt.year)],
        ignore_index=True,
    )
    full.to_pickle(DATA_DIR / "firm_quarterly_jkp_full.pkl")
    print("firm_quarterly_jkp_full:", full.shape)

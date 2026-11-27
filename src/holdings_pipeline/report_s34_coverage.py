"""Data validation table for S34: what remains after each filter.

Panel A: S34 holdings cleaning (pull_s34.py), from clean/s34/filter_log_s34.parquet.
Panel B: JKP firm universe, from clean/jkp/filter_log_jkp.parquet.
Panel C: the sample filters of the paper's Appendix D, applied to the S34
    holdings of each quarter: restrict to firms in the JKP universe, drop
    investors with one position above 75% of their portfolio, then keep
    investors with at least 20 stocks and stocks with at least 20 investors,
    iterating until nothing changes.

Outputs in OUTPUT_DIR/s34_coverage/:
    step_counts.parquet  quarter, panel, step, label, n_obs, n_investors, n_firms
    filter_steps.tex     LaTeX table with its note
    filter_steps.md      Markdown version with the same note
    coverage_over_time.png / .pdf  firms, investors, and holdings by year,
                         compared with Figure 2 of the paper
    coverage_over_time.md  caption and note for the figure

The paper's values come from data_manual/paper_figure2.csv, read from the
vector graphics of Figure 2 (see data_manual/data_README.md).

Usage (from the project root):

    uv run python src/holdings_pipeline/report_s34_coverage.py
"""

import sys
from pathlib import Path

# settings.py lives in src/, one level up from this folder
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib.pyplot as plt
import pandas as pd

from settings import config

DATA_DIR = Path(config("DATA_DIR"))
OUT_DIR = Path(config("OUTPUT_DIR")) / "s34_coverage"
PAPER_FIG2 = Path(config("MANUAL_DATA_DIR")) / "paper_figure2.csv"
# Thomson coverage gap after its 2011 feed migration (WRDS regenerated-data note)
GAP = (pd.Timestamp("2011-03-31"), pd.Timestamp("2017-09-30"))
TABLE_QUARTER = pd.Timestamp(config("PILOT_QUARTER", default="2022-12-31", cast=str))
COUNT_COLS = ["n_obs", "n_investors", "n_firms"]

# Steps shown in the table, keyed by the prefix of the step name in the logs
LABELS = {
    "A": {
        "1": "Downloaded (first vintage of each report)",
        "2": "Matched to CRSP (CUSIP valid on report date)",
        "2b": "Duplicate manager-security links removed",
        "3": "Ordinary common shares",
        "4": "Priced and listed on NYSE, AMEX, or Nasdaq",
        "5": "Aggregated to firms (PERMCO)",
    },
    "B": {
        "5a": "JKP firms (PERMCO)",
        "5b": "Excluding micro and nano caps",
        "5c": "Positive book equity",
        "5d": "Non-missing characteristics",
    },
    "C": {
        "c1": "Holdings of firms in the JKP universe",
        "c2": "Excluding concentrated investors (one position > 75%)",
        "c3": "Iterated 20×20 filter (final sample)",
    },
}
N_STEPS = sum(len(steps) for steps in LABELS.values())
PANELS = {
    "A": "Panel A. S34 holdings",
    "B": "Panel B. JKP firm universe",
    "C": "Panel C. Sample filters (paper, Appendix D)",
}


# ---- Counts ---------------------------------------------------------------


def _counts(h, step):
    return {
        "step": step,
        "n_obs": len(h),
        "n_investors": h["investor_id"].nunique(),
        "n_firms": h["permco"].nunique(),
    }


def sample_filters(h, universe, n_min=20, w_max=0.75):
    """Appendix D filters for one quarter.

    h has one row per investor and firm (investor_id, permco, dollar_holding).
    Returns the final holdings and the counts after each step.
    """
    if h.duplicated(["investor_id", "permco"]).any():
        raise ValueError("holdings must have one row per investor and firm")
    h = h[h["permco"].isin(universe)]
    steps = [_counts(h, "c1")]

    weight = h["dollar_holding"] / h.groupby("investor_id")["dollar_holding"].transform(
        "sum"
    )
    concentrated = h.loc[weight > w_max, "investor_id"]
    h = h[~h["investor_id"].isin(concentrated)]
    steps.append(_counts(h, "c2"))

    while True:
        keep = (h.groupby("permco")["investor_id"].transform("size") >= n_min) & (
            h.groupby("investor_id")["permco"].transform("size") >= n_min
        )
        if keep.all():
            break
        h = h[keep]
    steps.append(_counts(h, "c3"))
    return h, steps


def _from_log(log, panel):
    """Rows of a filter log for the steps shown in the table."""
    log = log.assign(quarter=pd.to_datetime(log["quarter"]), panel=panel)
    log["step"] = log["step"].str.split("_").str[0]
    log = log[log["step"].isin(LABELS[panel])].copy()
    if panel == "B":
        # One row per firm, so the observation count repeats the firm count
        log["n_obs"] = float("nan")
    return log[["quarter", "panel", "step", *COUNT_COLS]]


def _panel_c(quarters, firms, data_dir):
    rows = []
    for q in quarters:
        h = pd.read_parquet(
            Path(data_dir)
            / "clean"
            / "s34"
            / str(pd.Period(q, "Q"))
            / "holdings_s34.parquet"
        )
        universe = set(firms.loc[firms["quarter"] == q, "permco"])
        _, steps = sample_filters(h, universe)
        rows += [{"quarter": q, "panel": "C", **s} for s in steps]
    return pd.DataFrame(rows)


def build_counts(data_dir=DATA_DIR):
    """Counts for every quarter and step of the three panels."""
    data_dir = Path(data_dir)
    s34 = pd.read_parquet(data_dir / "clean" / "s34" / "filter_log_s34.parquet")
    jkp = pd.read_parquet(data_dir / "clean" / "jkp" / "filter_log_jkp.parquet")
    firms = pd.read_parquet(data_dir / "clean" / "jkp" / "firm_quarterly_jkp.parquet")
    firms["quarter"] = pd.to_datetime(firms["quarter"])
    quarters = sorted(set(pd.to_datetime(s34["quarter"])) & set(firms["quarter"]))

    counts = pd.concat(
        [_from_log(s34, "A"), _from_log(jkp, "B"), _panel_c(quarters, firms, data_dir)],
        ignore_index=True,
    )
    counts = counts[counts["quarter"].isin(quarters)].copy()
    order = {key: i for i, key in enumerate((p, s) for p in LABELS for s in LABELS[p])}
    keys = list(zip(counts["panel"], counts["step"], strict=True))
    counts["label"] = [LABELS[p][s] for p, s in keys]
    counts["order"] = [order[k] for k in keys]
    return counts.sort_values(["order", "quarter"]).reset_index(drop=True)


def complete_quarters(counts):
    """Quarters whose logs contain every step shown in the table."""
    per_quarter = counts.groupby("quarter")["order"].nunique()
    return per_quarter.index[per_quarter == N_STEPS]


def filter_table(counts, quarter=TABLE_QUARTER):
    """One row per step: counts in `quarter` and averages over complete quarters.

    Averaging only over quarters that log every step keeps each column on the
    same set of quarters, so averages cannot rise from one step to the next.
    """
    complete = complete_quarters(counts)
    if quarter not in complete:
        raise ValueError(f"{quarter.date()} does not log every step")
    at_q = counts[counts["quarter"] == quarter].set_index("order")[COUNT_COLS]
    full = counts[counts["quarter"].isin(complete)]
    avg = full.groupby("order")[COUNT_COLS].mean()
    meta = counts.drop_duplicates("order").set_index("order")[["panel", "label"]]
    return meta.join(at_q.add_prefix("q_")).join(avg.add_prefix("avg_")).sort_index()


# ---- Output ---------------------------------------------------------------


def _period(counts):
    """Range and number of complete quarters, and how many were left out."""
    qs = sorted(complete_quarters(counts))
    first, last = pd.Period(qs[0], "Q"), pd.Period(qs[-1], "Q")
    excluded = counts["quarter"].nunique() - len(qs)
    return f"{first}–{last}", len(qs), excluded


def _quarters(n):
    return f"{n} quarter" + ("" if n == 1 else "s")


def table_note(table, counts, quarter=TABLE_QUARTER):
    period, n_q, excluded = _period(counts)
    final = table.iloc[-1]
    q = pd.Period(quarter, "Q")
    return (
        "Observations are manager–firm positions, investors are 13F institutions "
        "(Thomson Reuters manager numbers), and firms are CRSP PERMCOs. "
        "Panel A follows the cleaning in pull_s34.py. Panel B lists the JKP firm "
        "universe, so it has no holdings or investors. Panel C applies the sample "
        "filters of the paper's Appendix D to the Panel A holdings: firms in the "
        "Panel B universe, no investor with one position above 75% of its "
        "portfolio, and an iterated filter keeping investors with at least 20 "
        "stocks and stocks with at least 20 investors. "
        f"Columns {q} report one quarter; the average columns are means over "
        f"{_quarters(n_q)} ({period})"
        + (
            f", excluding {_quarters(excluded)} whose logs lack a step. "
            if excluded
            else ". "
        )
        + f"The final sample in {q} has {final.q_n_obs:,.0f} positions held by "
        f"{final.q_n_investors:,.0f} institutions in {final.q_n_firms:,.0f} firms. "
        "S34 is reported at the 13F institution level, whereas the paper uses "
        "FactSet fund-level holdings, so it has fewer investors than the paper."
    )


def _fmt(x, dash):
    return dash if pd.isna(x) else f"{x:,.0f}"


def _tex_escape(s):
    for a, b in [("%", r"\%"), ("&", r"\&"), (">", "$>$"), ("×", r"$\times$")]:
        s = s.replace(a, b)
    return s.replace("–", "--")


def to_latex(table, counts, quarter=TABLE_QUARTER):
    period, n_q, _ = _period(counts)
    q = pd.Period(quarter, "Q")
    cols = [f"q_{c}" for c in COUNT_COLS] + [f"avg_{c}" for c in COUNT_COLS]
    lines = [
        r"\begin{table}[htbp]",
        r"\centering\small",
        r"\caption{S34 sample construction: observations after each filter}",
        r"\label{tab:s34_filter_steps}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        rf" & \multicolumn{{3}}{{c}}{{{q}}} & \multicolumn{{3}}{{c}}"
        rf"{{Average, {_tex_escape(period)} ({_quarters(n_q)})}} \\",
        r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}",
        r"Step & Holdings & Investors & Firms & Holdings & Investors & Firms \\",
        r"\midrule",
    ]
    for panel, rows in table.groupby("panel", sort=False):
        lines.append(rf"\multicolumn{{7}}{{l}}{{\textit{{{PANELS[panel]}}}}} \\")
        for _, r in rows.iterrows():
            cells = " & ".join(_fmt(r[c], "---") for c in cols)
            lines.append(rf"\quad {_tex_escape(r['label'])} & {cells} \\")
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\par\medskip",
        rf"\parbox{{\linewidth}}{{\footnotesize \textit{{Note.}} "
        rf"{_tex_escape(table_note(table, counts, quarter))}}}",
        r"\end{table}",
    ]
    return "\n".join(lines) + "\n"


def to_markdown(table, counts, quarter=TABLE_QUARTER):
    q = pd.Period(quarter, "Q")
    cols = [f"q_{c}" for c in COUNT_COLS] + [f"avg_{c}" for c in COUNT_COLS]
    lines = [
        "**S34 sample construction: observations after each filter**",
        "",
        f"| Step | Holdings ({q}) | Investors ({q}) | Firms ({q}) | Holdings (avg) "
        "| Investors (avg) | Firms (avg) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for panel, rows in table.groupby("panel", sort=False):
        lines.append(f"| *{PANELS[panel]}* | | | | | | |")
        for _, r in rows.iterrows():
            cells = " | ".join(_fmt(r[c], "—") for c in cols)
            lines.append(f"| {r['label']} | {cells} |")
    lines += ["", f"*Note.* {table_note(table, counts, quarter)}"]
    return "\n".join(lines) + "\n"


# ---- Figure -----------------------------------------------------------------


def _series(counts, panel, step, col):
    rows = counts[(counts["panel"] == panel) & (counts["step"] == step)]
    return rows.set_index("quarter")[col]


def annual_coverage(counts):
    """Annual means of quarterly counts, as in the paper's Figure 2."""
    q = pd.DataFrame(
        {
            "n_firms": _series(counts, "C", "c3", "n_firms"),
            "n_investors": _series(counts, "C", "c3", "n_investors"),
            "n_obs": _series(counts, "C", "c3", "n_obs"),
            "jkp_firms": _series(counts, "B", "5d", "n_firms"),
        }
    )
    return q.groupby(q.index.year).mean()


def gap_summary(counts):
    """JKP firms dropped by the sample filters, inside and outside the gap."""
    dropped = _series(counts, "B", "5d", "n_firms") - _series(
        counts, "C", "c3", "n_firms"
    )
    inside = (dropped.index >= GAP[0]) & (dropped.index <= GAP[1])
    return dropped[inside].min(), dropped[inside].max(), dropped[~inside].median()


def coverage_figure(annual, paper):
    """Three panels like Figure 2: firms, investors, holdings (thousands)."""
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    panels = [
        ("n_firms", "firms", "Firms", 1),
        ("n_investors", "investors", "Investors", 1),
        ("n_obs", "holdings_thousands", "Holdings (thousands)", 1e-3),
    ]
    for ax, (ours, theirs, title, scale) in zip(axes, panels, strict=True):
        ax.axvspan(2010.5, 2017.5, color="0.9", zorder=0, label="Thomson coverage gap")
        ax.axvline(2022.5, color="0.5", lw=0.8, ls="-.", label="Update period")
        ax.plot(
            annual.index, annual[ours] * scale, marker="o", ms=3, label="S34 sample"
        )
        ax.plot(
            paper["year"],
            paper[theirs],
            marker="s",
            ms=3,
            ls="--",
            label="Paper (FactSet)",
        )
        if ours == "n_firms":
            ax.plot(
                annual.index,
                annual["jkp_firms"],
                color="0.4",
                lw=1,
                ls=":",
                label="JKP universe",
            )
        ax.set_title(title)
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
    handles, labels = {}, []
    for ax in axes:
        for h, label in zip(*ax.get_legend_handles_labels(), strict=True):
            if label not in handles:
                handles[label] = h
                labels.append(label)
    fig.legend([handles[x] for x in labels], labels, loc="lower center", ncol=5)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    return fig


def figure_note(counts):
    lo, hi, outside = gap_summary(counts)
    period, _, _ = _period(counts)
    gap_counts = (
        f"In these quarters the filters drop {lo:,.0f}-{hi:,.0f} JKP firms per "
        f"quarter, against a median of {outside:,.0f} in other quarters. "
        if pd.notna(lo)
        else ""
    )
    return (
        "Annual means of quarterly counts. S34 sample: Thomson Reuters 13F "
        "holdings after the paper's Appendix D filters (Panel C of the filter "
        "table). Paper: values read from the vector graphics of Figure 2 in "
        "Gabaix, Koijen, Richmond, and Yogo (2026), FactSet data, 2005-2022. "
        "JKP universe: firms before the holdings filters. Shaded: 2011Q1-2017Q3, "
        "when Thomson's raw data lack the holdings of some widely held firms "
        "after its 2011 feed migration (WRDS, S12/S34 Regenerated Data "
        f"2010-2016). {gap_counts}S34 investors are 13F institutions, while the "
        "paper counts funds, so S34 has fewer investors. Years after the "
        f"dash-dotted line are the update period. Sample: {period}."
    )


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    counts = build_counts()
    counts.to_parquet(OUT_DIR / "step_counts.parquet", index=False)
    _, n_q, excluded = _period(counts)
    if excluded:
        print(
            f"Warning: {_quarters(excluded)} without every step "
            "left out of the averages"
        )
    table = filter_table(counts)
    (OUT_DIR / "filter_steps.tex").write_text(to_latex(table, counts), encoding="utf-8")
    (OUT_DIR / "filter_steps.md").write_text(
        to_markdown(table, counts), encoding="utf-8"
    )
    fig = coverage_figure(annual_coverage(counts), pd.read_csv(PAPER_FIG2))
    for ext in ("png", "pdf"):
        fig.savefig(OUT_DIR / f"coverage_over_time.{ext}", dpi=200)
    (OUT_DIR / "coverage_over_time.md").write_text(
        "**S34 coverage over time compared with the paper (Figure 2)**\n\n"
        f"*Note.* {figure_note(counts)}\n",
        encoding="utf-8",
    )
    print(table.to_string())
    print(f"Saved to {OUT_DIR}")

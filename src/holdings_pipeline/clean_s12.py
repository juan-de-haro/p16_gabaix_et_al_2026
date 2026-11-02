"""Build an auditable S12-only firm-holdings contribution for one quarter.

This is the input to the team's combined-sample construction. S12/S34 overlap,
JKP universe filters, concentration filters, 20x20 pruning, winsorization, and
two-way log-holdings demeaning must be applied after the datasets are combined.
"""

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import polars as pl
import pyarrow.parquet as pq

from holdings_pipeline.pull_s12 import load_raw_table, raw_directory, sample_dates
from settings import config


def select_reports(reports, quarter):
    """Latest actual report in the quarter, earliest vintage of that report."""
    quarter = pd.Period(quarter, freq="Q")
    frame = reports.dropna(subset=["fundno", "rdate", "fdate"]).copy()
    for column in ("rdate", "fdate"):
        frame[column] = pd.to_datetime(frame[column])
    frame = frame.loc[
        frame.rdate.between(quarter.start_time, quarter.end_time)
        & (frame.fdate >= frame.rdate)
    ]
    return frame.sort_values(
        ["fundno", "rdate", "fdate"], ascending=[True, False, True]
    ).drop_duplicates("fundno")[["fundno", "rdate", "fdate"]]


def snapshot_from_cache(start, end, quarter):
    """Filter a potentially large raw cache before materializing in pandas."""
    quarter = pd.Period(quarter, freq="Q")
    raw = (
        pl.scan_parquet(raw_directory(start, end) / "raw_s12_holdings.parquet")
        .filter(
            pl.col("rdate").is_between(
                quarter.start_time.date(), quarter.end_time.date()
            )
        )
        .with_columns(
            pl.col("fundno").cast(pl.Int64), pl.col("shares").cast(pl.Float64)
        )
    )
    counts = (
        raw.select(pl.len().alias("rows"), pl.col("fundno").n_unique().alias("funds"))
        .collect()
        .row(0)
    )
    reports = raw.select("fundno", "rdate", "fdate").unique().collect().to_pandas()
    selected = select_reports(reports, quarter)
    chosen = pl.from_pandas(selected).with_columns(
        pl.col("rdate").cast(pl.Date), pl.col("fdate").cast(pl.Date)
    )
    snapshot = (
        raw.join(chosen.lazy(), on=["fundno", "rdate", "fdate"], how="inner")
        .collect()
        .to_pandas()
    )
    return snapshot, reports, selected, counts


def remove_source_conflicts(frame):
    """Remove identical positions; quarantine conflicting values, never sum them."""
    key = ["fundno", "rdate", "fdate", "cusip"]
    frame = frame.drop_duplicates(key + ["shares"]).copy()
    conflict = frame.duplicated(key, keep=False)
    return frame.loc[~conflict].copy(), frame.loc[conflict].copy()


def link_by_report_date(positions, names):
    """Match historical 8-character CUSIP at RDATE and reject ambiguous links."""
    frame = positions.reset_index(drop=True).copy()
    frame["position_id"] = np.arange(len(frame))
    names = names.copy()
    names["ncusip"] = names.ncusip.astype("string").str.strip().str.upper()
    for column in ("namedt", "nameendt"):
        names[column] = pd.to_datetime(names[column])
    candidates = frame.merge(names, left_on="cusip", right_on="ncusip", how="inner")
    candidates = candidates.loc[
        candidates.rdate.between(candidates.namedt, candidates.nameendt)
    ]
    # Repeated intervals for the same permanent security are not distinct links.
    candidates = candidates.sort_values(["position_id", "namedt"]).drop_duplicates(
        ["position_id", "permno"], keep="last"
    )
    ambiguity = candidates.groupby("position_id").permno.nunique()
    ambiguous_ids = ambiguity.index[ambiguity > 1]
    linked_ids = candidates.position_id.unique()
    unmatched = frame.loc[~frame.position_id.isin(linked_ids)].copy()
    ambiguous = candidates.loc[candidates.position_id.isin(ambiguous_ids)].copy()
    linked = candidates.loc[~candidates.position_id.isin(ambiguous_ids)].copy()
    linked = linked.rename(columns={"permco": "report_permco"})
    return linked, unmatched, ambiguous


def common_stock_mask(frame):
    return (
        frame.securitytype.eq("EQTY")
        & frame.securitysubtype.eq("COM")
        & frame.sharetype.eq("NS")
        & frame.usincflg.eq("Y")
        & frame.issuertype.isin(["CORP", "ACOR"])
        & frame.primaryexch.isin(["N", "A", "Q"])
    )


def prepare_market(monthly, quarter):
    frame = monthly.copy()
    for column in ("mthprc", "shrout", "mthcumfacshr", "mthret"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["month"] = pd.to_datetime(frame.mthcaldt) + pd.offsets.MonthEnd(0)
    if frame.duplicated(["permno", "month"]).any():
        raise ValueError("CRSP contains multiple rows for a security-month.")
    date = pd.Period(quarter, freq="Q").end_time.normalize()
    market = frame.loc[frame.month.eq(date) & common_stock_mask(frame)].copy()
    market["security_me"] = market.mthprc.abs() * market.shrout * 1000
    market = market.loc[market.security_me.gt(0) & np.isfinite(market.security_me)]
    market = market.rename(
        columns={"mthprc": "quarter_price", "mthcumfacshr": "quarter_factor"}
    )
    firm_caps = market.groupby("permco", as_index=False).agg(me=("security_me", "sum"))
    return frame, market, firm_caps


def value_positions(linked, monthly, market):
    """S12 shares use the FDATE basis, so rescale FDATE -> quarter end."""
    frame = linked.copy()
    frame["vintage_month"] = pd.to_datetime(frame.fdate) + pd.offsets.MonthEnd(0)
    factors = monthly[["permno", "month", "mthcumfacshr"]].rename(
        columns={"month": "vintage_month", "mthcumfacshr": "vintage_factor"}
    )
    frame = frame.merge(
        factors, on=["permno", "vintage_month"], how="left", validate="many_to_one"
    )
    frame = frame.merge(
        market[
            [
                "permno",
                "permco",
                "quarter_price",
                "quarter_factor",
                "security_me",
                "issuernm",
                "mthprcdt",
            ]
        ],
        on="permno",
        how="left",
        validate="many_to_one",
    )
    valid = (
        frame.vintage_factor.gt(0)
        & frame.quarter_factor.gt(0)
        & frame.quarter_price.abs().gt(0)
        & frame.permco.notna()
    )
    rejected = frame.loc[~valid].copy()
    frame = frame.loc[valid].copy()
    frame["split_ratio"] = frame.vintage_factor / frame.quarter_factor
    frame["shares_adjusted"] = frame.shares * frame.split_ratio
    frame["dollar_holding"] = frame.shares_adjusted * frame.quarter_price.abs()
    frame["security_ownership_share"] = frame.dollar_holding / frame.security_me
    return frame, rejected


def aggregate_firms(securities, firm_caps, quarter):
    frame = securities.copy()
    frame["investor_id"] = "s12_" + frame.fundno.astype("int64").astype(str)
    frame["quarter"] = pd.Period(quarter, freq="Q").end_time.normalize()
    holdings = frame.groupby(["quarter", "investor_id", "permco"], as_index=False).agg(
        shares=("shares_adjusted", "sum"),
        dollar_holding=("dollar_holding", "sum"),
        n_share_classes=("permno", "nunique"),
        rdate=("rdate", "first"),
        fdate=("fdate", "first"),
    )
    holdings = holdings.merge(
        firm_caps, on="permco", how="left", validate="many_to_one"
    )
    holdings["log_h"] = np.log(holdings.dollar_holding)
    holdings["portfolio_weight"] = holdings.dollar_holding / holdings.groupby(
        "investor_id"
    ).dollar_holding.transform("sum")
    holdings["ownership_share"] = holdings.dollar_holding / holdings.me
    # Flag anomalies instead of silently capping ownership or deleting firms.
    ownership = holdings.groupby("permco").ownership_share.sum()
    holdings["ownership_total_s12"] = holdings.permco.map(ownership)
    holdings["ownership_flag"] = holdings.ownership_total_s12.gt(1 + 1e-6)
    return holdings, frame


def validate_outputs(holdings, securities, investors, firms, log):
    checks = {}

    def record(name, passed, detail):
        checks[name] = {"passed": bool(passed), "detail": detail}

    record("nonempty", len(holdings) > 0, int(len(holdings)))
    record(
        "unique_fund_firm",
        not holdings.duplicated(["quarter", "investor_id", "permco"]).any(),
        "quarter/investor_id/permco",
    )
    record(
        "unique_fund_security",
        not securities.duplicated(["quarter", "investor_id", "permno"]).any(),
        "quarter/investor_id/permno",
    )
    columns = ["shares", "dollar_holding", "me", "portfolio_weight", "ownership_share"]
    record(
        "positive_finite_values",
        np.isfinite(holdings[columns]).all().all()
        and holdings[columns].gt(0).all().all(),
        columns,
    )
    deviations = holdings.groupby("investor_id").portfolio_weight.sum().sub(1).abs()
    record(
        "portfolio_weights_sum_to_one",
        deviations.max() < 1e-10,
        float(deviations.max()),
    )
    total_security = securities.dollar_holding.sum()
    record(
        "aggregation_preserves_value",
        np.isclose(total_security, holdings.dollar_holding.sum(), rtol=1e-12),
        float(total_security),
    )
    record(
        "investor_coverage",
        set(holdings.investor_id) == set(investors.investor_id),
        int(len(investors)),
    )
    record("firm_coverage", set(holdings.permco) == set(firms.permco), int(len(firms)))
    record(
        "filter_rows_nonincreasing",
        pd.Series([row["n_obs"] for row in log]).diff().dropna().le(0).all(),
        [row["n_obs"] for row in log],
    )
    quarter_columns = [
        frame["quarter"]
        for frame in (holdings, securities, investors, firms, pd.DataFrame(log))
    ]
    record(
        "quarter_keys_are_quarter_end_dates",
        all(
            pd.api.types.is_datetime64_any_dtype(column)
            and column.notna().all()
            and column.dt.is_quarter_end.all()
            for column in quarter_columns
        ),
        "Quarter keys are dates in all clean tables, including the filter log",
    )
    record(
        "reports_in_quarter",
        holdings.rdate.dt.to_period("Q").eq(holdings.quarter.dt.to_period("Q")).all(),
        "No reports carried forward from previous quarters",
    )
    record(
        "vintage_not_before_report",
        holdings.fdate.ge(holdings.rdate).all(),
        "FDATE >= RDATE",
    )
    record(
        "firm_ownership_at_most_one",
        not holdings.ownership_flag.any(),
        int(holdings.loc[holdings.ownership_flag, "permco"].nunique()),
    )
    return checks


def ownership_exception_details(holdings, securities, monthly):
    """Decompose warnings by security and report date without changing positions.

    Quarter-end-only totals are a sensitivity diagnostic, not a replacement
    sample. Report-month SHROUT is contextual; it is never used as a new
    quarter-end denominator. Private founder classes absent from CRSP are not
    implicitly valued at the public class price.
    """
    flagged = holdings.loc[holdings.ownership_flag]
    rows = securities.loc[securities.permco.isin(flagged.permco)].copy()
    rows = rows.merge(
        flagged[["quarter", "investor_id", "permco", "me", "ownership_total_s12"]],
        on=["quarter", "investor_id", "permco"],
        validate="many_to_one",
    )
    rows["report_is_quarter_end"] = rows.rdate.eq(rows.quarter)
    rows["report_age_days"] = (rows.quarter - rows.rdate).dt.days
    rows["report_month"] = rows.rdate + pd.offsets.MonthEnd(0)
    context = monthly[["permno", "month", "shrout"]].rename(
        columns={"month": "report_month", "shrout": "report_month_shrout_thousands"}
    )
    rows = rows.merge(
        context, on=["permno", "report_month"], how="left", validate="many_to_one"
    )
    rows["quarter_shrout_shares"] = rows.security_me / rows.quarter_price.abs()
    rows["individual_security_above_one"] = rows.security_ownership_share.gt(1 + 1e-6)
    rows["review_status"] = "unresolved_do_not_treat_as_validated"
    rows["firm_ownership_contribution"] = rows.dollar_holding / rows.me
    rows["quarter_end_report_contribution"] = rows.firm_ownership_contribution.where(
        rows.report_is_quarter_end, 0.0
    )
    totals = rows.groupby(["quarter", "permco"], as_index=False).agg(
        ownership_total_s12=("firm_ownership_contribution", "sum"),
        ownership_from_quarter_end_reports=("quarter_end_report_contribution", "sum"),
        n_security_positions=("permno", "size"),
        n_earlier_report_positions=("report_is_quarter_end", lambda x: int((~x).sum())),
        n_individual_security_above_one=("individual_security_above_one", "sum"),
    )
    totals["ownership_from_earlier_reports"] = (
        totals.ownership_total_s12 - totals.ownership_from_quarter_end_reports
    )
    totals["quarter_end_reports_still_above_one"] = (
        totals.ownership_from_quarter_end_reports.gt(1 + 1e-6)
    )
    by_date = rows.groupby(
        ["quarter", "permco", "permno", "rdate"], as_index=False
    ).agg(
        n_positions=("fundno", "size"),
        shares_quarter_basis=("shares_adjusted", "sum"),
        firm_ownership_contribution=("firm_ownership_contribution", "sum"),
    )
    return rows, totals, by_date


def quality_diagnostics(
    holdings, securities, firms, unmatched, names, rejected, monthly
):
    """Explain exclusions and expose the inputs behind ownership warnings."""
    names_elsewhere = unmatched.cusip.isin(names.ncusip)
    flagged = holdings.loc[holdings.ownership_flag]
    ownership = flagged.groupby("permco", as_index=False).agg(
        ownership_total_s12=("ownership_share", "sum"),
        largest_investor_ownership=("ownership_share", "max"),
        n_investors=("investor_id", "nunique"),
        dollar_holding=("dollar_holding", "sum"),
        me=("me", "first"),
    )
    ownership = ownership.merge(firms[["permco", "name"]], on="permco")
    flagged_securities = securities.loc[securities.permco.isin(flagged.permco)]
    comparison = flagged_securities.groupby(
        ["permco", "permno", "cusip", "issuernm"], as_index=False
    ).agg(
        s12_shares=("shares_adjusted", "sum"),
        min_split_ratio=("split_ratio", "min"),
        max_split_ratio=("split_ratio", "max"),
        quarter=("quarter", "first"),
    )
    comparison["previous_month"] = comparison.quarter - pd.offsets.MonthEnd(1)
    comparison = comparison.merge(
        monthly[["permno", "month", "shrout"]].rename(
            columns={"month": "quarter", "shrout": "quarter_shrout_thousands"}
        ),
        on=["permno", "quarter"],
        how="left",
        validate="one_to_one",
    ).merge(
        monthly[["permno", "month", "shrout"]].rename(
            columns={"month": "previous_month", "shrout": "previous_shrout_thousands"}
        ),
        on=["permno", "previous_month"],
        how="left",
        validate="one_to_one",
    )
    comparison["shrout_monthly_change"] = (
        comparison.quarter_shrout_thousands / comparison.previous_shrout_thousands - 1
    )
    comparison = comparison.drop(columns=["quarter", "previous_month"])
    return {
        "unmatched": {
            "no_cusip_in_cached_name_intervals": int((~names_elsewhere).sum()),
            "cusip_exists_but_not_valid_at_report_date": int(names_elsewhere.sum()),
            "alphabetic_first_character": int(unmatched.cusip.str.match("[A-Z]").sum()),
            "note": "An alphabetic identifier is a diagnostic only, not a verified "
            "asset-country classification. The overall match rate uses all S12 "
            "holdings, not a preselected US common-stock universe.",
        },
        "market_exclusions": {
            "total_positions": int(len(rejected)),
            "no_eligible_quarter_end_market_row": int(rejected.permco.isna().sum()),
            "eligible_quarter_but_missing_vintage_factor": int(
                (rejected.permco.notna() & ~rejected.vintage_factor.gt(0)).sum()
            ),
            "note": "Missing late-vintage factors can arise after delisting; "
            "no factor is forward-filled or assumed to be one.",
        },
        "ownership_companies": json.loads(ownership.to_json(orient="records")),
        "ownership_security_inputs": json.loads(comparison.to_json(orient="records")),
        "ownership_note": "Ownership uses eligible CRSP-listed common equity, "
        "not every legal share class or voting right. Compare source holdings "
        "with the quarter-end share "
        "count and its previous-month change. A large fall is a possible timing "
        "or corporate-action discrepancy, not proof of which input is wrong. "
        "Flagged positions remain pending review; do not treat them as validated.",
    }


def write_html(report, log, target):
    checks = pd.DataFrame(
        [{"check": key, **value} for key, value in report["checks"].items()]
    )
    summary = pd.DataFrame(list(report["summary"].items()), columns=["metric", "value"])
    assumptions = "".join(
        f"<li>{html.escape(value)}</li>" for value in report["method"]
    )
    diagnostics = report["diagnostics"]
    ownership = pd.DataFrame(diagnostics["ownership_companies"])
    ownership_inputs = pd.DataFrame(diagnostics["ownership_security_inputs"])
    timing = pd.DataFrame(diagnostics["ownership_report_timing"])
    investigation_html = (
        '<p class="note"><a href="ownership_review/report_zh.html">'
        "2022Q4 detailed ownership investigation / 中文证据报告</a>: "
        "unresolved source alignment; no holdings or denominators overridden.</p>"
        if diagnostics.get("ownership_investigation")
        else ""
    )
    exclusions = html.escape(
        json.dumps(
            {key: diagnostics[key] for key in ("unmatched", "market_exclusions")},
            indent=2,
        )
    )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
    <title>S12 {report["quarter"]} quality report</title>
    <style>body{{font:16px system-ui;max-width:1150px;margin:40px auto;
    padding:0 24px;color:#172337}}h1,h2{{color:#123f63}}
    table{{border-collapse:collapse;width:100%;margin:20px 0;font-size:14px}}
    th,td{{border:1px solid #dbe2ea;padding:8px;text-align:left}}
    th{{background:#edf4f8}}.note{{background:#fff5dc;padding:16px;
    border-left:4px solid #d08b00}}li{{margin:10px 0}}</style>
    <h1>S12 {report["quarter"]} — processing and validation</h1>
    <p class="note">S12-only contribution before the combined S12/S34
    and JKP universe filters. Status: {report["status"]}.
    Any failed ownership check remains an unresolved data-quality exception.</p>
    {investigation_html}
    <h2>Summary</h2>{summary.to_html(index=False, escape=True)}
    <h2>Validation</h2>{checks.to_html(index=False, escape=True)}
    <h2>Processing counts</h2>{pd.DataFrame(log).to_html(index=False, escape=True)}
    <h2>Exclusion diagnostics</h2><pre style="white-space:pre-wrap">{exclusions}</pre>
    <h2>Ownership exceptions</h2>{ownership.to_html(index=False, escape=True)}
    {ownership_inputs.to_html(index=False, escape=True)}
    <h2>Report-date decomposition (diagnostic, not a filtered sample)</h2>
    {timing.to_html(index=False, escape=True)}
    <p>Security-level review rows include original RDATE/FDATE, individual
    security ownership, report age, and report-month share counts. See
    exceptions/ownership_security_review.parquet and
    exceptions/ownership_report_dates.parquet. Removing earlier reports or
    adding unlisted founder shares is not an automatic correction.</p>
    <p>{html.escape(diagnostics["ownership_note"])}</p>
    <h2>Method and boundaries</h2><ul>{assumptions}</ul>
    <p>Input queries, retrieval timestamps and SHA-256 checksums are stored
    beside the raw Parquet files.
    Row-level exceptions are in the adjacent exceptions directory.</p></html>"""
    target.write_text(document, encoding="utf-8")


def clean(start=None, end=None, quarter=None):
    start, end = sample_dates(start, end)
    quarter = pd.Period(quarter or end, freq="Q")
    if quarter.start_time < start or quarter.end_time.normalize() > end:
        raise ValueError(
            "The complete processing quarter must be within the raw report-date range."
        )
    output = Path(config("DATA_DIR")) / "clean" / "s12" / str(quarter)
    report_dir = Path(config("OUTPUT_DIR")) / "s12" / str(quarter)
    exceptions = report_dir / "exceptions"
    output.mkdir(parents=True, exist_ok=True)
    exceptions.mkdir(parents=True, exist_ok=True)
    log = []

    def step(label, frame, count=None, funds=None):
        row = {
            "quarter": quarter.end_time.normalize(),
            "step": label,
            "n_obs": len(frame) if count is None else int(count),
            "n_investors": int(frame.fundno.nunique()) if funds is None else int(funds),
            "n_firms": int(frame.permco.nunique()) if "permco" in frame else None,
        }
        log.append(row)
        print(
            f"{label}: {row['n_obs']:,} rows, {row['n_investors']:,} investors",
            flush=True,
        )

    def save_exception(name, frame):
        frame.to_parquet(exceptions / f"{name}.parquet", index=False)

    snapshot, reports, selected, counts = snapshot_from_cache(start, end, quarter)
    step("raw_reports_in_quarter_all_vintages", snapshot, *counts)
    step("latest_report_first_vintage", snapshot)
    snapshot["fundno"] = snapshot.fundno.astype("int64")
    snapshot["cusip"] = snapshot.cusip.astype("string").str.strip().str.upper()
    for column in ("rdate", "fdate"):
        snapshot[column] = pd.to_datetime(snapshot[column])
    snapshot = snapshot.drop_duplicates(["fundno", "rdate", "fdate", "cusip", "shares"])
    step("identical_positions_deduplicated", snapshot)
    snapshot, conflicts = remove_source_conflicts(snapshot)
    save_exception("conflicting_source_keys", conflicts)
    step("conflicting_keys_quarantined", snapshot)
    valid = (
        snapshot.shares.gt(0)
        & np.isfinite(snapshot.shares)
        & snapshot.cusip.str.fullmatch(r"[A-Z0-9]{8}", na=False)
    )
    save_exception("invalid_positions", snapshot.loc[~valid])
    snapshot = snapshot.loc[valid].copy()
    step("positive_shares_valid_cusip", snapshot)

    names = load_raw_table("crsp_names", start, end)
    linked, unmatched, ambiguous = link_by_report_date(snapshot, names)
    save_exception("unmatched_cusip", unmatched)
    save_exception("ambiguous_cusip", ambiguous)
    step("unique_cusip_at_report_date", linked)
    common = linked.shrcd.isin([10, 11]) & linked.exchcd.isin([1, 2, 3])
    save_exception("not_common_stock_at_report", linked.loc[~common])
    linked = linked.loc[common].copy()
    step("us_common_stock_at_report", linked)
    # Multiple source CUSIPs resolving to one security can double-count a position.
    alias_collision = linked.duplicated(["fundno", "permno"], keep=False)
    save_exception("multiple_cusips_one_security", linked.loc[alias_collision])
    linked = linked.loc[~alias_collision].copy()
    step("security_alias_collisions_quarantined", linked)

    monthly, market, firm_caps = prepare_market(
        load_raw_table("crsp_monthly", start, end), quarter
    )
    securities, not_valued = value_positions(linked, monthly, market)
    save_exception("missing_or_ineligible_quarter_market_data", not_valued)
    step("quarter_common_stocks_and_split_factors", securities)
    if securities.empty:
        raise ValueError("No valid S12 positions remain after matching and valuation.")
    securities["permco"] = securities.permco.astype("int64")
    securities["permno"] = securities.permno.astype("int64")
    holdings, securities = aggregate_firms(securities, firm_caps, quarter)
    step("aggregate_share_classes_to_firm", securities, count=len(holdings))
    metadata = securities.sort_values(["fundno", "rdate", "fdate"]).drop_duplicates(
        "investor_id"
    )
    investors = holdings.groupby(["quarter", "investor_id"], as_index=False).agg(
        n_holdings=("permco", "nunique"), aum=("dollar_holding", "sum")
    )
    investors = investors.merge(
        metadata[
            ["investor_id", "fundno", "fundname", "country", "ioc", "rdate", "fdate"]
        ],
        on="investor_id",
        validate="one_to_one",
    )
    investors = investors.rename(columns={"fundno": "source_id", "fundname": "name"})
    investors["source"] = "s12"
    investors["investor_type"] = "mutual_fund"
    used = market.loc[market.permco.isin(holdings.permco)].copy()
    representative = used.sort_values(
        ["permco", "security_me", "permno"], ascending=[True, False, True]
    ).drop_duplicates("permco")
    firms = representative[["permco", "issuernm"]].rename(columns={"issuernm": "name"})
    firms = firms.merge(firm_caps, on="permco", validate="one_to_one")
    firms["quarter"] = quarter.end_time.normalize()
    firms["permnos"] = firms.permco.map(used.groupby("permco").permno.agg(list))
    checks = validate_outputs(holdings, securities, investors, firms, log)
    diagnostics = quality_diagnostics(
        holdings, securities, firms, unmatched, names, not_valued, monthly
    )
    review_rows, review_totals, review_dates = ownership_exception_details(
        holdings, securities, monthly
    )
    save_exception("ownership_security_review", review_rows)
    save_exception("ownership_report_dates", review_dates)
    diagnostics["ownership_report_timing"] = json.loads(
        review_totals.to_json(orient="records", date_format="iso")
    )
    diagnostics["individual_security_positions_above_one"] = int(
        securities.security_ownership_share.gt(1 + 1e-6).sum()
    )

    raw_meta = pq.ParquetFile(
        raw_directory(start, end) / "raw_s12_holdings.parquet"
    ).metadata
    source_count_path = report_dir / "source_row_counts.json"
    if source_count_path.exists():
        reference = json.loads(source_count_path.read_text(encoding="utf-8"))
        if (start.month, start.day, end.month, end.day) == (1, 1, 12, 31):
            if start.year == end.year == reference["year"]:
                expected = reference["total_rows"]
                checks["annual_rows_match_wrds_count"] = {
                    "passed": raw_meta.num_rows == expected,
                    "detail": {"expected": expected, "actual": raw_meta.num_rows},
                }
                expected_q = reference["rows_by_report_quarter"][str(quarter.quarter)]
                checks["quarter_rows_match_wrds_count"] = {
                    "passed": counts[0] == expected_q,
                    "detail": {"expected": expected_q, "actual": counts[0]},
                }
    source_reports = reports.loc[reports.rdate.notna()].copy()
    summary = {
        "raw_annual_rows_all_vintages": int(raw_meta.num_rows),
        "raw_quarter_rows_all_vintages": int(counts[0]),
        "funds_with_reports_in_quarter": int(counts[1]),
        "selected_report_positions": int(log[1]["n_obs"]),
        "selected_fund_reports": int(len(selected)),
        "conflicting_source_keys": int(
            conflicts[["fundno", "cusip"]].drop_duplicates().shape[0]
        ),
        "unmatched_cusip_positions": int(len(unmatched)),
        "ambiguous_cusip_positions": int(ambiguous.position_id.nunique()),
        "cusip_match_rate": float(log[5]["n_obs"] / log[4]["n_obs"]),
        "final_security_positions": int(len(securities)),
        "final_fund_firm_positions": int(len(holdings)),
        "final_funds": int(len(investors)),
        "final_firms": int(len(firms)),
        "total_matched_equity_usd": float(holdings.dollar_holding.sum()),
        "split_rescaled_positions": int((~np.isclose(securities.split_ratio, 1)).sum()),
        "first_vintage_after_quarter_funds": int(
            selected.fdate.gt(quarter.end_time).sum()
        ),
        "non_quarter_end_report_funds": int(
            selected.rdate.ne(quarter.end_time.normalize()).sum()
        ),
        "non_quarter_end_report_funds_retained": int(
            investors.rdate.ne(quarter.end_time.normalize()).sum()
        ),
        "first_vintage_after_quarter_funds_retained": int(
            investors.fdate.gt(quarter.end_time).sum()
        ),
        "funds_below_20_firms_before_combined_filter": int(
            investors.n_holdings.lt(20).sum()
        ),
        "firms_below_20_s12_investors": int(
            holdings.groupby("permco").investor_id.nunique().lt(20).sum()
        ),
        "firms_with_s12_ownership_above_100pct": int(
            holdings.loc[holdings.ownership_flag, "permco"].nunique()
        ),
        "max_firm_s12_ownership": float(holdings.ownership_total_s12.max()),
        "minimum_selected_report_date": str(selected.rdate.min().date()),
        "maximum_raw_quarter_vintage": str(source_reports.fdate.max().date()),
    }
    method = [
        "Raw annual sample uses RDATE and retains all available FDATE vintages; "
        "this is a retrospective dataset, not a point-in-time filing feed.",
        "For each fund, select its latest report within the quarter, then the "
        "first FDATE containing that report. Do not carry previous-quarter "
        "reports forward.",
        "Resolve 8-character CUSIP using CRSP names valid on RDATE; ambiguous "
        "links and conflicting source positions are quarantined.",
        "Keep US common shares on NYSE/AMEX/NASDAQ at the report date and the "
        "target quarter. Missing prices/factors are logged, never imputed.",
        "S12 SHARES already reflect splits through FDATE. Quarter-end shares "
        "= SHARES * CRSP_factor(FDATE) / CRSP_factor(quarter end). "
        "Source: WRDS Return Gap macro.",
        "Dollar holdings = quarter-end shares * abs(quarter-end CRSP price). "
        "Company market cap sums all eligible CRSP share classes, including "
        "classes not held by S12 funds; SHROUT is multiplied by 1000.",
        "Company-level shares is a sum of share-class units; use dollar_holding "
        "for economic comparisons. Security-level details remain available.",
        "Investor AUM and portfolio weights refer only to matched US common "
        "equities, not total fund NAV. Recompute weights after final "
        "combined-sample filters.",
        "No S12/S34 deduplication, JKP micro/nano or book-equity filters, "
        "concentration filters, 20x20 pruning, winsorization, or two-way "
        "demeaning has been applied; these belong to the combined pipeline.",
        "Ownership above 100% is flagged and retained for investigation "
        "rather than silently clipped.",
        "Sources: https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/"
        "wrds-macros-return-gap/ ; CRSP Flat File Format 2.0 database guide.",
    ]
    failures = [key for key, value in checks.items() if not value["passed"]]
    structural_failures = [
        name for name in failures if name != "firm_ownership_at_most_one"
    ]
    report = {
        "quarter": str(quarter),
        "scope": "s12_only_pre_combined_universe",
        "status": (
            "failed"
            if structural_failures
            else ("passed_with_warnings" if failures else "passed")
        ),
        "summary": summary,
        "checks": checks,
        "diagnostics": diagnostics,
        "method": method,
    }
    holdings.to_parquet(output / "holdings_s12.parquet", index=False)
    securities.to_parquet(output / "holdings_s12_security.parquet", index=False)
    investors.to_parquet(output / "investors_s12.parquet", index=False)
    firms.to_parquet(output / "firm_names_s12.parquet", index=False)
    pd.DataFrame(log).to_parquet(output / "filter_log_s12.parquet", index=False)
    selected.to_parquet(output / "selected_reports_s12.parquet", index=False)
    holdings.loc[holdings.ownership_flag].to_parquet(
        exceptions / "ownership_above_one.parquet", index=False
    )
    (report_dir / "validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    write_html(report, log, report_dir / "quality_report.html")
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"Validation failures: {failures}", flush=True)
    if structural_failures:
        raise ValueError(f"Structural validation failed: {failures}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--quarter")
    arguments = parser.parse_args()
    clean(arguments.start_date, arguments.end_date, arguments.quarter)

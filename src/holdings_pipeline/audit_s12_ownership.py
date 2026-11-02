"""Reproduce the bounded 2022Q4 ownership investigation from cached evidence.

Run after clean_s12.py. This writes audit artifacts only; it does not change
clean positions. Additional WRDS queries and public SEC documents live in
_data/raw/s12/ownership_review_2022Q4 with URL/query/time/hash sidecars.
"""

import hashlib
import html
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import polars as pl

from settings import config


def verify_evidence_file(path, expected_url=None):
    """Refuse altered supplemental files instead of silently reusing evidence."""
    metadata_path = path.with_suffix(path.suffix + ".json")
    if not metadata_path.exists():
        metadata_path = path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
        raise ValueError(f"Evidence checksum mismatch: {path}")
    if expected_url is not None and metadata.get("url") != expected_url:
        raise ValueError(f"Evidence URL mismatch: {path}")
    return metadata


def audit():
    root = Path(config("BASE_DIR"))
    evidence_path = root / "design" / "s12_2022q4_ownership_evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    data = Path(config("DATA_DIR"))
    output = Path(config("OUTPUT_DIR")) / "s12" / "2022Q4"
    target = output / "ownership_review"
    target.mkdir(parents=True, exist_ok=True)
    raw = data / "raw" / "s12" / "ownership_review_2022Q4"
    rows = pd.read_parquet(output / "exceptions" / "ownership_security_review.parquet")
    reviewed = [case["permco"] for case in evidence["companies"]]
    if set(rows.permco) != set(reviewed):
        raise ValueError(
            "The exception population changed; reassess the evidence scope."
        )

    source_metadata = verify_evidence_file(raw / "s12_four_cusips.parquet")
    source = pd.read_parquet(raw / "s12_four_cusips.parquet")
    cached = (
        pl.scan_parquet(data / "raw/s12/20220101_20221231/raw_s12_holdings.parquet")
        .filter(
            pl.col("cusip").is_in(rows.cusip.unique().tolist())
            & (pl.col("rdate") >= pd.Timestamp("2022-10-01").date())
        )
        .collect()
        .to_pandas()
    )
    columns = source.columns.tolist()
    # Same WRDS query projection and native Arrow decimal/date types.
    same_source = (
        source.astype(str)
        .sort_values(columns)
        .reset_index(drop=True)
        .equals(cached.astype(str).sort_values(columns).reset_index(drop=True))
    )
    if not same_source:
        raise ValueError(
            "Fresh WRDS query differs from the annual cache; review first."
        )
    for column in ("rdate", "fdate"):
        source[column] = pd.to_datetime(source[column])
    key = ["fundno", "rdate", "cusip"]
    versions = source.groupby(key, as_index=False).agg(
        source_rows=("shares", "size"),
        source_distinct_shares=("shares", "nunique"),
        source_first_fdate=("fdate", "min"),
        source_last_fdate=("fdate", "max"),
        source_distinct_vintages=("fdate", "nunique"),
    )
    rows = rows.merge(versions, on=key, how="left", validate="one_to_one")
    events = pd.DataFrame(evidence["companies"])[["permco", "event_date"]]
    events["event_date"] = pd.to_datetime(events.event_date)
    rows = rows.merge(events, on="permco", validate="many_to_one")
    rows["report_precedes_redemption_meeting"] = rows.rdate.lt(rows.event_date)

    observations = []
    filing_metadata = {}
    for filing in evidence["fund_filings"]:
        path = raw / "sec" / filing["filename"]
        filing_metadata[filing["filename"]] = verify_evidence_file(path, filing["url"])
        doc = ET.parse(path).getroot()
        for element in doc.iter():
            element.tag = element.tag.split("}")[-1]
        for position in doc.findall(".//invstOrSec"):
            cusip = position.findtext("cusip", "")[:8]
            if cusip not in set(rows.cusip):
                continue
            balance = float(position.findtext("balance"))
            observations.append(
                {
                    "fundno": filing["fundno"],
                    "cusip": cusip,
                    "sec_series_name": doc.findtext(".//genInfo/seriesName"),
                    "sec_report_date": doc.findtext(".//genInfo/repPdDate"),
                    "sec_balance": balance,
                    "sec_unit_value": float(position.findtext("valUSD")) / balance,
                    "sec_units": position.findtext("units"),
                    "sec_asset_category": position.findtext("assetCat"),
                    "sec_is_loan_by_fund": position.findtext(
                        "securityLending/isLoanByFund"
                    ),
                    "sec_url": filing["url"],
                }
            )
    obs = pd.DataFrame(observations)
    rows = rows.merge(obs, on=["fundno", "cusip"], how="left", validate="one_to_one")
    rows["sec_balance_matches_s12"] = pd.array(
        [
            pd.NA if pd.isna(a) else bool(np.isclose(a, b, rtol=0, atol=1e-8))
            for a, b in zip(rows.sec_balance, rows.shares)
        ],
        dtype="boolean",
    )
    rows.to_parquet(target / "position_evidence.parquet", index=False)
    rows.to_csv(target / "position_evidence.csv", index=False, encoding="utf-8-sig")

    companies = []
    for case in evidence["companies"]:
        subset = rows.loc[rows.permco.eq(case["permco"])]
        denominator = subset.quarter_shrout_shares.iloc[0]
        total = subset.shares_adjusted.sum()
        qend = subset.loc[subset.report_is_quarter_end, "shares_adjusted"].sum()
        companies.append(
            {
                **case,
                "status": "unresolved_source_alignment",
                "positions": len(subset),
                "s12_shares": total,
                "crsp_class_a_shares": denominator,
                "issuer_minus_crsp_shares": case["class_a_shares_after_redemption"]
                - denominator,
                "ownership_all_reports": total / denominator,
                "shares_from_earlier_reports": total - qend,
                "ownership_quarter_end_reports_only": qend / denominator,
                "ownership_using_exact_issuer_class_a": total
                / case["class_a_shares_after_redemption"],
                "sec_verified_positions": int(
                    subset.sec_balance_matches_s12.fillna(False).sum()
                ),
            }
        )
    result = {
        "quarter": "2022Q4",
        "status": "unresolved_source_alignment",
        "clean_values_changed": False,
        "source_requery_equals_annual_cache": same_source,
        "source_requery_rows": len(source),
        "reviewed_positions": len(rows),
        "positions_with_changed_shares_across_vintages": int(
            rows.source_distinct_shares.gt(1).sum()
        ),
        "duplicate_selected_source_keys": int(source.duplicated(key + ["fdate"]).sum()),
        "sec_verified_positions": int(rows.sec_balance_matches_s12.fillna(False).sum()),
        "wrds_query_provenance": source_metadata,
        "fund_filing_provenance": filing_metadata,
        "companies": companies,
        "evidence_manifest_sha256": hashlib.sha256(
            evidence_path.read_bytes()
        ).hexdigest(),
        "provenance": "Query/URL, retrieval timestamp and SHA256 sidecars in "
        + str(raw),
    }
    (target / "review.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary = pd.DataFrame(companies)[
        [
            "name",
            "positions",
            "s12_shares",
            "crsp_class_a_shares",
            "class_a_shares_after_redemption",
            "ownership_all_reports",
            "shares_from_earlier_reports",
            "ownership_quarter_end_reports_only",
            "sec_verified_positions",
        ]
    ].rename(
        columns={
            "name": "公司",
            "positions": "持仓数",
            "s12_shares": "S12合计股数",
            "crsp_class_a_shares": "CRSP A股股数",
            "class_a_shares_after_redemption": "发行人A股股数",
            "ownership_all_reports": "全部报告比例",
            "shares_from_earlier_reports": "季末前报告股数",
            "ownership_quarter_end_reports_only": "仅季末报告比例",
            "sec_verified_positions": "SEC股数复核笔数",
        }
    )
    for column in ("全部报告比例", "仅季末报告比例"):
        summary[column] = summary[column].map(lambda value: f"{value:.4%}")
    sections = "".join(
        f"<h2>{html.escape(c['name'])}</h2><p>{html.escape(c['finding'])}</p>"
        f"<p>{html.escape(c['judgment'])}</p>"
        f'<p><a href="{html.escape(c["issuer_url"])}">发行人原始披露</a></p>'
        for c in companies
    )
    links = "".join(
        f'<li><a href="{html.escape(s["url"])}">'
        f"{html.escape(s.get('title', s.get('filename', '')))}</a></li>"
        for s in evidence["fund_filings"] + evidence["additional_sources"]
    )
    document = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>S12 2022Q4 四家公司持股异常核查</title>
    <style>body{{font:16px system-ui;max-width:1250px;margin:40px auto;
    padding:0 24px;line-height:1.65;color:#172337}}
    table{{border-collapse:collapse;font-size:13px;width:100%}}
    th,td{{border:1px solid #ccd6df;padding:8px}}
    th{{background:#edf4f8}}.note{{background:#fff1d2;padding:18px}}h1,h2{{color:#123f63}}a{{color:#165f93}}</style>
    <h1>S12 2022Q4 四家公司持股异常核查</h1>
    <p class="note">结论：四家仍待决，57笔持仓保留原值；未裁剪、置零或改分母。
    已排除下载差异、可用FDATE修订、拆股比率与直接重复键问题。
    证据支持“赎回后发行人股本与基金披露口径/结算时点冲突”，不支持逐笔强制修正。</p>
    {summary.to_html(index=False, escape=True)}
    <h2>已经核实的事实</h2><ul>
    <li>WRDS定向重新查询89条记录，与全年缓存逐列一致；57笔选中持仓的所有可用FDATE股数均未变化。
    全部拆股比率为1，选中基金—证券无重复；不存在将多个FDATE相加的问题。</li>
    <li>四家CRSP股数与发行人披露的A股数差异均小于500股，符合千股口径舍入。
    使用发行人精确A股股数也不能消除任何一家的异常。</li>
    <li>剔除所有非12月31日报告后，四家仍超过100%。这只是敏感性分析，未据此删持仓。
    旧报告在大额赎回前形成，确实加剧异常，但不是完整解释。</li>
    <li>四份基金原始N-PORT共复核{result["sec_verified_positions"]}笔，股数全部与S12一致。
    涉及的证券是A类普通股，并非把单位或认股权证误配为普通股；这些N-PORT记录均标示未出借。
    其他持仓未逐一验证证券借贷状态。</li>
    <li>Merger Fund的N-PORT日期为12月30日，S12及其年报为12月31日，
    已在逐笔证据中保留这一区别；没有据此改动RDATE。</li>
    <li>First Trust（原Vivaldi）年末XML保留三笔异常持仓，而同份持仓附件不含这三家；
    XML净资产为2,533,977,767.97美元，附件为2,537,183,609美元，二者并非完全一致。
    不从“附件缺项”推断零持仓，也不将下一期清仓日期倒推到年末。</li>
    <li>S12自身证券主表在2022-12-31仍保留四家公司赎回前的股数；Iris和Landcadia到2023-03-31仍未更新。
    这进一步显示来源更新不同步，不能用该旧股数替换已获发行人支持的CRSP分母。</li></ul>
    {sections}
    <h2>代码与数据处理结论</h2>
    <p>未发现有证据支持修改经济数值的程序错误。本次增加通用诊断：按RDATE拆解持股比例、
    单基金—证券超过100%标记、报告年龄、报告月份股数及明确未决状态。
    证券层面与公司层面保持独立；多个类别按美元市值合计，不能混加不同类别股数来计算比例。</p>
    <p>分母是符合条件的CRSP上市普通股市值，不是公司全部法律股本或投票权。
    四家公司均另有B类创始人股，未在当前CRSP市场表中独立计价。
    直接按A股价格补入B股会改变研究定义，不能作为让检查通过的修复。</p>
    <h2>仍需团队决定</h2><ul>
    <li>联合研究是否排除这四个公司—季度，或将其设为不可用并做敏感性比较；
    不能只删到比例低于100%的若干基金，避免按结果挑选样本。</li>
    <li>赎回权益/应收款是否属于研究的普通股持仓；若需重建剩余股权，应取得基金或供应商的逐笔更正/结算证据。</li>
    <li>公司市值是否需要非上市创始人类别，及相应估值和全样本一致规则。
    当前保留上市CRSP口径。未开展S12/S34合并或最终样本筛选。</li></ul>
    <h2>复现与证据</h2><p>先运行clean_s12.py处理2022Q4，再运行src/holdings_pipeline/audit_s12_ownership.py。
    本脚本只读取缓存，若源端重查与全年缓存不一致或异常公司范围改变则停止，要求重新核查。
    全部判定来自design/s12_2022q4_ownership_evidence.json；该文件不覆盖数据。</p>
    <p><a href="position_evidence.csv">57笔逐项证据（CSV）</a> ·
    <a href="review.json">机器可读核查结论</a> ·
    <a href="../quality_report.html">流水线质量报告</a></p>
    <p>源端查询与SEC文件及时间戳、SHA256保存在_data/raw/s12/ownership_review_2022Q4。
    position_evidence中的空SEC字段表示尚未逐笔核对原始基金文件，不能解释为零股。</p>
    <ul>{links}</ul></html>"""
    (target / "report_zh.html").write_text(document, encoding="utf-8")
    # Attach evidence without changing validation outcomes or clean tables.
    from holdings_pipeline.clean_s12 import write_html

    validation_path = output / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["diagnostics"]["ownership_investigation"] = result
    validation_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    log = pd.read_parquet(data / "clean/s12/2022Q4/filter_log_s12.parquet")
    write_html(validation, log.to_dict("records"), output / "quality_report.html")
    print(json.dumps({k: v for k, v in result.items() if k != "companies"}, indent=2))


if __name__ == "__main__":
    audit()

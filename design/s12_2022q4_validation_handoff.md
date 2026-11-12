# S12 2022Q4 Data Validation: Consultation Handoff

Prepared November 12, 2026. Scope: S12-only inputs before the combined S12/S34 research sample.

## Meeting takeaway

The S12 pilot has been replayed from verified local raw caches. Under the eligibility policy merged in PR #1, it contains **1,935,608 fund-company positions, 21,101 funds, and 5,091 companies**. Fourteen of fifteen recorded checks pass. The remaining check flags **six companies with aggregate S12 ownership above 100%**; these exceptions remain unresolved.

The earlier count of four exceptions and the newer count of six describe different eligibility policies. Replaying both versions on identical inputs reproduces both counts. This reconciles the reporting difference; it does not validate or correct the exceptional holdings.

## 1. Data sources and construction

| Source | Use |
|---|---|
| WRDS `tfn.s12` | Fund holdings, report dates (`rdate`) and data vintage dates (`fdate`) |
| CRSP `crspm.msenames` | Report-date-valid historical CUSIP links and security eligibility |
| CRSP `crspm.msf_v2` | Quarter-end prices, shares outstanding, split factors and company identifiers |

The raw S12 cache covers reports dated in 2022 and retains all available vintages. For each fund, the pilot selects its latest report within 2022Q4 and the earliest available vintage containing that report. It does not carry forward reports from earlier quarters.

Holdings are matched to securities using historical CUSIPs valid on the report date, valued at quarter end with split-adjusted shares, and aggregated to `quarter × investor_id × permco`. Company market capitalization includes eligible share classes even when a particular fund does not hold them. Holdings and market capitalization are in USD; CRSP shares outstanding are converted from thousands of shares.

Portfolio weights and reported fund AUM describe the retained equity subset, not total fund NAV. This is a retrospective dataset: `fdate` is not an SEC publication timestamp, and 436 retained funds use a first vintage after quarter end. Also, 1,917 retained funds have a report date before December 31. These are timing limitations to disclose, especially before defining a post-2022 forecasting exercise.

## 2. Validation results

These are data-level results from the merged-policy replay, not a claim that the complete project or every unit test has passed.

| Check | Result / evidence |
|---|---|
| Raw download completeness | Annual 26,538,429 rows and Q4 7,756,302 rows agree with the saved WRDS count receipt; no fresh server count was queried |
| Input integrity | SHA-256 hashes of all three raw Parquet inputs match their acquisition sidecars |
| Nonempty output and unique holding keys | Pass at both fund-security and fund-company levels |
| Positive, finite numeric values | Pass for shares, dollar holdings, market capitalization, weights and ownership shares |
| Portfolio weights | Sum to one within each fund's retained equity subset |
| Company aggregation | Preserves total dollar holdings |
| Metadata coverage | Investor and company sets agree across the S12 output tables |
| Dates and processing log | Quarter-end keys; reports inside Q4; `fdate >= rdate`; recorded row counts never increase |
| Source conflicts and ambiguous links | Zero conflicting source keys and zero ambiguous CUSIP positions in this run; rejected-row outputs remain available |
| Aggregate company ownership | **Warning: six companies exceed 100%; not clipped, removed or declared valid** |

The input hash check is an additional replay control, separate from the fifteen checks recorded in `validation.json`.

## 3. Sample coverage

| Stage | Positions / records | Funds | Companies |
|---|---:|---:|---:|
| Q4 reports, all available vintages | 7,756,302 | 65,729 | — |
| Latest Q4 report, first vintage | 5,621,462 | 65,729 | — |
| Positive shares and valid CUSIP format | 5,620,713 | 64,980 | — |
| Unique report-date CRSP link | 2,134,045 | 24,945 | — |
| Eligible ordinary shares at report date | 1,946,169 | 21,101 | — |
| Eligible quarter-end market data and factors | 1,945,714 | 21,101 | 5,091 |
| Aggregated fund-company holdings | 1,935,608 | 21,101 | 5,091 |

Identical-row deduplication, source-conflict quarantine and security-alias quarantine remove no additional rows at their respective stages. The date-valid CUSIP match rate is **37.97% = 2,134,045 / 5,620,713**. Its denominator precedes US-listed-equity eligibility filtering; it is not the match rate of a prefiltered US-equity universe and is not directly comparable with a differently defined S34 rate. There are 3,486,668 unmatched positions; they must not all be assumed to be foreign securities.

## 4. Reconciliation: four versus six ownership exceptions

| Measure | Legacy policy, `e843c11` | Merged policy, `8be2e87` |
|---|---:|---:|
| Report-date share codes | 10, 11 | 10, 11, 12 |
| Quarter-end incorporation restriction | Requires `usincflg = Y` | No incorporation-only restriction; other ordinary-share criteria retained |
| Fund-company positions | 1,735,513 | 1,935,608 |
| Funds | 17,799 | 21,101 |
| Companies | 4,156 | 5,091 |
| Companies with ownership above 100% | 4 | 6 |

The same cached inputs reproduce the saved legacy summary and all its check results exactly. The merged policy adds 200,095 fund-company keys and removes none. Dollar holdings and market capitalization are unchanged for all 1,735,513 shared keys, within the numerical comparison tolerance.

| Company | `permco` | Aggregate S12 ownership | Comparison |
|---|---:|---:|---|
| Brookfield Corp | 32056 | 114.69% | Newly included; share code 12, `usincflg = N` |
| Corner Growth Acquisition Corp | 57618 | 105.55% | Newly included; share code 12, `usincflg = N` |
| Thunder Bridge Capital Partners III | 57982 | 113.39% | Existing exception retained |
| Iris Acquisition | 58081 | 109.05% | Existing exception retained |
| Landcadia Holdings IV | 58130 | 124.17% | Existing exception retained |
| OmniLit Acquisition | 58971 | 126.69% | Existing exception retained |

Use **six** when describing the merged-policy pilot. Older references to four apply to the earlier eligibility universe.

The [existing evidence review](s12_2022q4_ownership_evidence.json) covers the original four companies. It documents redemption and source-timing discrepancies without establishing corrected position balances. Brookfield and Corner Growth require separate source investigation; inclusion under the revised policy explains why they now appear in the exception list, not why their ownership exceeds 100%. All six remain flagged. S12/S34 overlap is a separate unresolved question and cannot explain these S12-only exceptions by itself.

## 5. Open decisions for the team and Jeremy

- Confirm how unresolved ownership exceptions should be treated in the research sample, with an explicit rule and sensitivity comparison. Do not cap ownership or alter balances just to pass a check.
- Agree on S12/S34 economic-overlap treatment and the final common firm universe. Concentration, iterative 20-by-20 pruning, final weights and cross-table consistency must be checked after integration. The current S12 weights are provisional.
- Specify report availability and vintage rules for post-2022 evaluation. This retrospective pilot does not establish a point-in-time forecasting dataset.

Juan's integration stage owns the final joint sample, as described in the [Q4 team handoff](https://github.com/juan-de-haro/p16_gabaix_et_al_2026/blob/8be2e87c82029de39bdafaa7712df098e37dff4c/design/q4_firm_matching_valuation_handoff.md). This document supplies S12 validation evidence for that discussion.

## 6. Reproduction and evidence

The offline comparison used legacy revision `e843c11` and merged revision [`8be2e87c82029de39bdafaa7712df098e37dff4c`](https://github.com/juan-de-haro/p16_gabaix_et_al_2026/commit/8be2e87c82029de39bdafaa7712df098e37dff4c), downloaded raw caches from November 1, 2026, and separate output directories. No fresh WRDS/SEC query or data correction was performed. Existing source outputs and the original ownership investigation were preserved.

To reproduce either policy, use its revision in an isolated checkout, supply the same raw cache and saved source-count receipt, and run from that checkout's root:

```text
uv run python src/holdings_pipeline/clean_s12.py --start-date 2022-01-01 --end-date 2022-12-31 --quarter 2022Q4
```

Each checkout needs the raw files under `_data/raw/s12/20220101_20221231/` and the count receipt at `_output/s12/2022Q4/source_row_counts.json`. Keep the two output locations separate. Confirm input hashes before comparing counts. Each run writes clean tables, `filter_log_s12.parquet`, `validation.json`, `quality_report.html`, and exception tables.

The local review package is under `_output/s12_validation_review/20261112/`: `reconciliation.json`, source and replay manifests, audit helpers, and separate `legacy/` and `merged/` data/report directories. The meeting's detailed HTML evidence is `merged/reports/s12/2022Q4/quality_report.html`; the original four-company investigation remains under `_output/s12/2022Q4/ownership_review/`. These generated files are not in Git. Commit documentation only and share report artifacts separately, following the [repository delivery rules](../src/holdings_pipeline/MERGE_NOTES.md).

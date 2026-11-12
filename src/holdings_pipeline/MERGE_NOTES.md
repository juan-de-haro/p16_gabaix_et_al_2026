# S12, S34, and JKP Integration Notes

**Pilot quarter:** 2022Q4

**Updated:** November 2, 2026

**Status:** A provisional source union is implemented; the final combined sample
has not yet been validated.

These notes describe how to connect the source-specific pipelines. They do not
replace the project's [data processing specification](../../design/02_data_processing.md).
The initial combined run is provisional and explicitly flags unresolved
cross-source overlap. No S12/S34 deduplication rule or fund-to-manager mapping
has been agreed yet.

## 1. Current contributions

| Component | Current contribution | Remaining integration work |
|---|---|---|
| S12 | Raw download with query/hash provenance; fund holdings matched to CRSP; split adjustment; company aggregation; investor metadata; filter logs and diagnostics | Align security eligibility and CUSIP policies with S34; combine sources and apply joint-sample processing |
| S34 | Manager-report selection; CRSP matching with a historical-CUSIP fallback; split adjustment; company holdings and investor metadata | Align output format and matching rules; retain auditable raw inputs and rejected rows; address overlap with S12 |
| JKP | Core and 153-characteristic downloads; company aggregation; characteristic filters and winsorization | Normalize monetary units; reconcile market capitalization with CRSP; align the final company universe and winsorization sample |

This comparison refers to the S12 contribution in commit `9836340` and the
S34/JKP contribution in commit `7f9f7f9`. Unfinished local integration changes
are not part of those contributions.

S12 raw holdings cover reports dated throughout 2022, while the completed S12
processing run covers 2022Q4 only. Source-specific results are inputs to the
joint pipeline, not the final research sample.

## 2. Resolve economic overlap before treating the merge as final

S12 identifies individual funds. S34 identifies reporting institutions or
managers, whose portfolios may already include assets held by S12 funds.
Keeping different `investor_id` prefixes prevents key collisions but does not
prevent economic double counting.

- Do not simply concatenate the sources and describe the result as deduplicated.
- Do not remove observations just because two investors hold the same company:
  that is a normal feature of holdings data.
- Do not infer a reliable fund-family mapping from similar names alone.
- S34 `typecode` is not, by itself, a reliable hedge-fund identifier.
- A final rule needs an agreed mapping or another documented source-selection
  methodology, with explicit treatment of partially overlapping managers.

For the provisional pilot, preserve source identifiers and label the combined
output **provisional: S12/S34 overlap unresolved**. Do not cap ownership at 100%
or delete large managers merely to make a validation check pass. Combined AUM,
ownership totals, investor counts, and benchmark results remain sensitive to
the overlap rule.

## 3. Align the security universe and identifier matching

### Security eligibility

The S12 pilot now includes legacy share code 12 and no longer requires US
incorporation; S34 also retains foreign-incorporated ordinary shares. Both
source pipelines require a US exchange listing. US listing and US
incorporation are different conditions.

Before calling this the final research universe, confirm the exact treatment
of ordinary shares, ADRs, issuer type, exchange, and eligibility at both the
report date and valuation date. Apply the same eligible share-class definition
when computing company market capitalization. Record the effect of this
harmonization on each source.

### CUSIP matching

S12 uses historical names valid on `rdate`. The provisional S34 run uses the
same unique date-valid rule; its historical/header-CUSIP fallback is disabled
by default and available only as a labelled sensitivity run. The fallback can
match a security active on the report date to a CUSIP received later.

If the fallback is retained for the final sample, apply and document it
consistently for both sources. Require a unique candidate, retain a
`link_method` or equivalent audit field, and quarantine ambiguous links. The
provisional S34 direct match now rejects CUSIPs mapping to multiple securities.

Also check whether multiple source CUSIPs map to the same investor-security
position. Do not sum these aliases without establishing that they are distinct
holdings. Historical identifier repair is retrospective and does not establish
when a filing was publicly observable.

## 4. Align dates, split adjustments, monetary units, and valuation

- Store `quarter` as a quarter-end date, such as `2022-12-31`, rather than the
  string `2022Q4`.
- Preserve both `rdate` (portfolio report date) and `fdate` (data vintage).
  Neither should be interpreted as an SEC publication timestamp.
- S12 selects the latest available report within the quarter and its earliest
  vintage. Some funds therefore use October or November reports. S34 uses the
  quarter-end report. Retain and report this timing difference.
- Use the same share-basis convention. The S12/S34 implementation uses
  `shares * factor(fdate) / factor(quarter_end)` before quarter-end valuation.
  Missing factors must be reported rather than silently set to one.
- Use absolute CRSP prices consistently. Both source pipelines do this now.

| Field or source | Current unit | Combined-table convention |
|---|---|---|
| S12/S34 `dollar_holding` | USD | USD |
| CRSP `shrout` | Thousands of shares | Multiply by 1,000 for share counts |
| JKP `me`, `book_equity`, `market_equity` | Millions of USD | Multiply by 1,000,000 when exporting monetary levels in USD |
| Ratios, returns, and other JKP characteristics | Source-defined | Preserve the documented definitions; do not apply a blanket currency multiplier |

Recompute logs from the normalized levels. The specification makes CRSP
`firm_monthly.me` the authoritative market-cap source; the current JKP cleaner
instead derives `me` from JKP. Reconcile this difference and recompute
book-to-market consistently. Company market cap must include all eligible share
classes, including classes absent from a particular fund's portfolio.

When aggregating JKP book equity, check consistency within company/date/GVKEY
before counting it once per accounting entity. Share counts summed across
different share classes are retained for traceability; dollar holdings are the
economically comparable quantity.

## 5. Use one final sample and one processing order

The project specification calls for a common company universe across final
`holdings`, `firm_quarterly`, and `firm_names` tables. Align the full JKP table
with that universe as well.

The joint pipeline needs to:

1. Harmonize the two holdings sources and document the overlap policy or its
   unresolved status.
2. Apply the JKP-based company eligibility rules, including size, required
   characteristics, and positive book equity.
3. Apply the concentration rule with a clearly defined portfolio denominator.
4. Iterate the 20-by-20 rule until stable: each retained investor holds at least
   20 companies, and each retained company has at least 20 investors.
5. Reconcile the winsorization sample and order. The published JKP cleaner
   already winsorizes its candidate universe; the project design places final
   characteristic processing after the joint filters. Retain unwinsorized
   values so the final transformation can be reproduced without double clipping.
6. Recompute portfolio weights, investor AUM and holding counts, ownership
   shares, and centered log holdings using the final sample and consistent units.

The interim AUM and weights refer to matched eligible equity holdings, not
total fund NAV. Preserve source-level logs, add joint-stage logs, and distinguish
filtering from matching diagnostics or aggregation when checking row counts.

## 6. Preserve unresolved exceptions and validate the result

The S12 pilot flags four companies: Thunder Bridge Capital Partners III
(`permco=57982`), Iris Acquisition (`58081`), Landcadia Holdings IV (`58130`),
and OmniLit Acquisition (`58971`). The investigation found conflicting source
timing around redemptions; it did not establish corrected year-end quantities
for every affected fund. The existing records remain flagged.

See the [ownership evidence annotations](../../design/s12_2022q4_ownership_evidence.json)
and the [processing notes](../../design/02_data_processing.md). Exclusion by a
subsequent size filter would remove a company from the research sample, but
would not resolve the underlying source discrepancy.

Validate the combined pilot for:

- Unique keys, including `quarter/investor_id/permco` for holdings.
- Matching company and investor sets across related tables.
- Positive, finite holdings and valid market-cap denominators.
- Preservation of dollar values during aggregation.
- Portfolio weights summing to one after final filtering.
- Convergence of the 20-by-20 filter and consistent quarter-date types.
- Missing characteristics, prices, factors, and unmatched or ambiguous CUSIPs.
- Ownership totals above one, reported separately from structural failures.

A successful pipeline run does not establish that cross-source overlap or
source-data exceptions have been resolved. Report those statuses explicitly.

## 7. Deliverables and repository hygiene

The final clean-table names remain those in the original specification:
`holdings`, `investors`, `firm_monthly`, `firm_quarterly`,
`firm_quarterly_jkp_full`, `firm_names`, and `filter_log`.

Use Parquet for the shared contract. The S34/JKP contribution currently writes
Pickle files, so its persistence layer still needs alignment. Keep the raw,
clean, and application-input layers distinct and retain query/version provenance.

`firm_monthly` covers the broad eligible CRSP universe, not just companies in
the final quarterly holdings sample. Company monthly returns should use a
documented share-class weighting convention; do not average class returns
without weights.

Keep raw and processed data, credentials, generated diagnostics, and local
configuration out of Git. Commit source code, tests, documentation, and
non-sensitive metadata only. Auxiliary source-level tables are implementation
artifacts, not additional final deliverables mandated by the original design.

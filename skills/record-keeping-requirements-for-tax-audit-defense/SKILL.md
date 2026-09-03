---
name: record-keeping-requirements-for-tax-audit-defense
description: >-
  US federal trade record-keeping compliance engine validating tax audit documentation completeness — mandatory fields, IRC § 1222 holding period classification, § 1091 wash sale determinations, § 1.1012-1(c) lot identification, § 475(f) segregation evidence, and disposal-anchored retention.
domain: Tax & Regulatory Compliance
subdomain: Trade Record Retention & Audit Defense
tags: ["record-keeping", "tax-audit", "cost-basis", "holding-period", "wash-sale", "retention-policy", "irs-compliance", "section-475f", "specific-identification"]
brokers_frameworks: ["IRC § 6001 / Treas. Reg. § 1.6001-1", "IRC § 1222", "IRC § 1091", "Treas. Reg. § 1.1012-1(c)", "IRC § 475(f)", "IRS Rev. Proc. 98-25", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when an algorithmic trading operation must be able to substantiate
its **US federal** tax return positions under examination. Active trading
generates thousands of taxable events, and IRC § 6001 places the burden of
proof on the taxpayer to produce records adequate to establish each position.
This engine audits a trade record set for the evidence an examiner will ask for:
per-lot cost basis, acquisition and disposal dates, holding-period
classification, wash sale determinations, lot-identification substantiation,
§ 475(f) segregation evidence, and whether any record is about to be purged
before its retention obligation has run.

## When NOT to Use

- **Non-US regimes.** Nothing here encodes HMRC, CRA, ATO or any other national
  rules. See `record-retention-periods-by-jurisdiction` and
  `multi-jurisdiction-tax-residency-implications`.
- **Computing tax.** This engine checks record *defensibility*, not liability.
  For wash sale matching and basis adjustment use `wash-sale-rule-tracking-us`;
  for § 1256 contracts use `section-1256-contract-tax-treatment-us-futures`.
- **Broker-dealer books and records.** SEC Rule 17a-4 preservation duties are a
  separate regime binding registered broker-dealers, not taxpayers generally.
- **As legal or tax advice.** Applicability, elections and limitation periods
  are fact-specific; a qualified adviser signs off, not this engine.

## Prerequisites

- Trade records (`trade_id`, `symbol`, `side`, `quantity`, `price`, `trade_date`,
  `cost_basis_usd`, `proceeds_usd`, `acquisition_date`, `disposal_date`,
  `lot_method`, `lot_identification_date`, `wash_sale_flag`).
- Engine config: `retention_years` (default 7, a firm *policy* default — see
  Workflow step 5), `mandatory_fields`, `accounting_method`
  (`CAPITAL` or `MTM_475F`), `specific_id_deadline_business_days` (default 1, T+1).
- An explicit `as_of` evaluation date. Omitting it defaults to today and makes
  audit output non-reproducible run to run.

## Workflow

1. **Mandatory Field Completeness Audit**
   - Flag any record missing a configured mandatory field. Cost basis is the
     field examinations turn on: without it the IRS may assert a **zero basis**,
     taxing gross proceeds rather than gain.
2. **Structural Validation**
   - Reject unparseable dates, non-`BUY`/`SELL` sides, non-positive or
     non-finite quantities, unknown lot methods, and disposals dated before
     acquisition. A defective record is reported as data, never raised as an
     exception — one bad row must not abort an audit run.
   - Flag duplicate `trade_id`s: a non-unique key means the audit trail cannot
     tie an entry to a single execution.
3. **Holding Period Classification (IRC § 1222)**
   - Classify from `acquisition_date` and `disposal_date` where both exist.
     Long-term requires disposal **strictly after** the one-year anniversary.
   - Where only `holding_period_days` exists: $\le 365$ is short-term,
     $\ge 367$ is long-term, and exactly $366$ is **ambiguous** — that is one
     year across a leap-day span but more than one year otherwise. Do not guess;
     resolve it by supplying the dates.
4. **Wash Sale Determination (IRC § 1091)**
   - Require a recorded `wash_sale_flag` on every capital-account sell, including
     a negative determination — silence is not evidence.
   - If the audit runs within 30 days of the sale the replacement window is still
     open, so any flag is provisional. That is reported as an **advisory**, not a
     defect.
   - Skip entirely for securities under a valid § 475(f) election.
5. **Lot Identification Substantiation (Treas. Reg. § 1.1012-1(c))**
   - Where `lot_method` is `SPECIFIC_ID`, require a `lot_identification_date` no
     later than the settlement deadline (T+1 for most US securities since
     2024-05-28). Absent that, basis reverts to FIFO and the claimed method
     collapses under examination.
6. **§ 475(f) Segregation Evidence**
   - Where the entity has a mark-to-market election, any security marked
     `held_for_investment` must carry an `investment_identification_date` equal
     to its acquisition date — IRS Topic 429 requires identification **on the day
     acquired**. Such securities stay in the capital account and remain subject to
     steps 3 and 4.
7. **Retention Policy Enforcement**
   - The retention clock starts at **disposal**, not acquisition: IRS guidance is
     to keep property records until the limitations period expires for the year
     of disposal. A record with no known disposal date has no purge date at all.
   - Compute `earliest_purge_date = disposal_date + retention_years`. Flag any
     record with a pending purge that is not yet eligible, and treat a record
     under `legal_hold` as never purgeable.
8. **Audit Report Generation**
   - Output a `TaxAuditComplianceReport` separating `DEFECT` from `ADVISORY`
     issues, with per-record `RetentionAssessment` rationales.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming a universal 7-year IRS retention rule.** There isn't one. The
  assessment period is generally 3 years (§ 6501(a)), 6 years where more than 25%
  of gross income is omitted (§ 6501(e)), and unlimited where no return or a
  fraudulent return is filed (§ 6501(c)). Seven years is the specific figure for
  a worthless-securities or bad-debt loss claim.
- **Anchoring retention to the trade date.** A basis record for a position held
  ten years is not purgeable after seven — the clock has not started. Purging on
  record age destroys basis evidence for still-open lots.
- **Classifying holding period from a 365-day threshold.** Per IRS Publication
  550, stock bought 2012-02-06 and sold 2013-02-06 is **short**-term even though
  366 days elapsed. A naive `> 365 days` test converts short-term gains into
  long-term ones and understates tax.
- **Treating a fresh wash sale flag as final.** The § 1091 window runs 30 days
  *after* the sale; a determination recorded at trade time can be invalidated by
  a replacement purchase weeks later. Re-run the determination after the window
  closes.
- **Claiming SPECIFIC_ID without contemporaneous identification.** An
  identification made when the return is prepared is not adequate under
  § 1.1012-1(c)(1); basis falls back to FIFO, often at a materially worse result.
- **Applying wash sale or short/long analysis to a § 475(f) trader.** Per IRS
  Topic 429 neither applies to securities marked to market, and forcing them
  generates false findings that train reviewers to ignore the report.
- **Carrying SEC Rule 17a-4 into a tax retention policy.** It binds registered
  broker-dealers on a 3- or 6-year schedule; it is not an IRS requirement.

## Verification

- Instantiate `RecordKeepingRequirementsForTaxAuditDefenseEngine`. Add 2 complete
  trade records and 1 record missing cost basis $\implies$ verify
  `AUDIT_ISSUES_FOUND` with 1 incomplete record flagged. Add only complete
  records $\implies$ verify `AUDIT_COMPLIANT`.
- Holding period regression: `classify_holding_period(date(2012,2,6),
  date(2013,2,6))` $\implies$ `SHORT_TERM` (the IRS Pub. 550 example), and
  `classify_holding_period(None, None, 366)` $\implies$ `AMBIGUOUS`.
- Retention regression: a BUY dated 2005 with no `disposal_date` $\implies$
  `earliest_purge_date is None` and `purge_eligible is False`.
- Run `python -m unittest discover -s skills/record-keeping-requirements-for-tax-audit-defense/scripts`.

## Limitations

- No exchange or banking holiday calendar is bundled, so the T+1 settlement
  deadline used by the `SPECIFIC_ID` check skips weekends only. Around a holiday
  the computed deadline can be early, biasing the check toward flagging for
  review rather than toward silence.
- The engine validates a *single* record's internal consistency. It does not
  match sales to replacement purchases across records, reconcile against Form
  1099-B, or verify that a § 475(f) election was actually filed on time.
- `retention_years` is a single policy number. It does not model the § 6501(e)
  or § 6501(c) extensions, which depend on return-level facts the engine cannot
  see.

## Related Skills

- `mark-to-market-election-for-active-traders-us`
- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `record-retention-periods-by-jurisdiction`
- `1099-b-and-broker-tax-reporting-reconciliation`

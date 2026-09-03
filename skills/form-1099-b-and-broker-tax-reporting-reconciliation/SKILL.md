---
name: form-1099-b-and-broker-tax-reporting-reconciliation
description: >-
  Use when reconciling an internal realised-lot ledger against broker 1099-B filings
  before Form 8949, to detect wrong basis, missing lots and wash-sale flag disagreements
  while there is still time to fix them.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: tax, reconciliation, compliance, 1099-B, form-8949, wash-sale, decimal-arithmetic
  brokers_frameworks: ""
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when:

- Running **end-of-year tax reconciliation** between an internal realized-lot ledger and broker 1099-B files (CSV/JSON).
- **Detecting broker reporting errors** (wrong basis, missing lots, wash-sale flag disagreements) before filing Form 8949.
- Producing a **CPA-handoff-ready discrepancy report** from the matched/missing/flagged classification.
- Building an automated pipeline that needs to verify broker data integrity in a regulatory regime where the IRS uses 1099-B as the canonical broker-side record.

## When NOT to Use

Do **not** use this skill when:

- The broker data is for a **non-US jurisdiction** (Canada T5007, UK CG contracts, EU local variants, AU/Taxation Determination, etc.). Use a jurisdiction-specific skill or extend this engine with a normalized lot schema.
- The asset class is **already aggregated** (Section 1256 contracts, Regulated futures) — those need a different schema (boxes 8–11 aggregate) and are out of scope here.
- The data is **digital-asset 1099-DA only** — 1099-DA has different boxes and adjustment codes; use a digital-asset–specific skill.
- You have only **trader-vs-investor determination** or **mark-to-market election** work — see related skills for those.
- The lot ledger has **not yet been finalized** (e.g. corporate-action adjustments pending) — reconciliation against an unstable source produces false discrepancies.

## Prerequisites

- Python 3.9+.
- Frozen internal realized-lot ledger (all corporate actions, splits, mergers applied; all dividends reinvested).
- Parsed 1099-B data from the clearing broker (CSV/JSON, ideally the **final** corrected version, not the preliminary February draft).
- Understanding of **trade-date accounting**. Form 1099-B box 1c carries the *trade date* of the sale, and a security's holding period likewise runs from the day after the trade date to the trade date of the sale — not the settlement date (IRS Pub. 550, *Holding Period → Securities traded on an established market*; Instructions for Form 1099-B, box 1c).
- US dollar as the reporting currency (multi-currency normalization is out of scope; pre-normalize before ingestion).

## Workflow

1. **Ingestion**: Load internal tax lots and broker 1099-B lines through `load_internal_lot*` / `load_broker_lot*`. The engine validates each lot — non-positive quantities and backwards dates are rejected at ingestion time.
2. **Hash-bucketed matching**: Lots are grouped by `(symbol, quantity, acquired_date, sold_date)`; matching inside each bucket runs in two passes:
   - **Pass 1 (clean)**: Pair lots that produce *no* discrepancies on proceeds, basis, wash-sale flag, or wash-sale-amount.
   - **Pass 2 (best-effort)**: Pair any remaining lots in the bucket positionally; these typically populate `matched_with_discrepancies`.
3. **Tolerance evaluation**: Each pair is checked against `ToleranceConfig` (absolute cents + relative basis-percent). Either bound passing ⇒ acceptable.
4. **Classification**: The result is split into `matched_clean`, `matched_with_discrepancies`, `missing_in_1099b`, `missing_in_internal`.
5. **Discrepancy reporting**: Every flagged item carries a `DiscrepancyReason` enum, a human-readable `details` string, and (where applicable) a signed `difference_amount`.
6. **Operational metrics**: `ReconciliationResult.metrics()` returns a count for *every* `DiscrepancyReason` (zero when unseen, so alert rules never have to guard on a missing key) plus the signed internal-minus-broker totals `total_proceeds_delta` and `total_basis_delta`.

For the full lifecycle (data freeze, broker retrieval, CPA handoff), see `references/workflows.md`.

## Decision Points

| Situation | Action |
|-----------|--------|
| All lots matched_clean | Pass-through to Form 8949 generation; no manual review needed. |
| Any `BASIS_OUTSIDE_TOLERANCE` | First establish *which side is wrong* — code B asserts the broker's basis is incorrect, so do not apply it until the internal basis has been substantiated. Then: **covered** lot (8949 box A/D) ⇒ put the broker's box 1e figure in column (e), `B` in column (f), and the adjustment in column (g); **noncovered** lot (box B/E) ⇒ put the correct basis straight into column (e) and `-0-` in column (g), no code. Column (g) is *broker minus internal* — the **negation** of `basis_delta`. Use `MatchPair.form_8949_column_g_basis_adjustment`, which applies both rules. |
| Any `WASH_SALE_FLAG_MISMATCH` or `WASH_SALE_AMOUNT_MISMATCH` | Compare against actual trade tickets; broker typically authoritative for same-account, same-CUSIP. |
| Any `MISSING_IN_BROKER` | Treat as a **real break**, not an expected artifact: box 1c is the trade date, so a Dec 30/31 sale belongs on *this* year's 1099-B. Work the causes in order — internal ledger booked on settlement date, an open short sale (not reported until the year of delivery), a lot held at a different broker/account than the 1099-B under reconciliation, or an aggregated broker row. Do not file until each one is explained. |
| Any `MISSING_IN_INTERNAL` | Internal ledger missed the trade. Investigate broken-import, missed write, or transfer-in failure; **do not** file Form 8949 until reconciled. |
| Net `total_basis_delta` above the firm's review threshold | Manual CPA review before filing. The threshold is an internal materiality convention, not an IRS rule; `assets/checklist.md` §5 carries the default ladder. |
| Aggregate discrepancy count > 0.5% of total lot count | Likely systemic issue; halt pipeline and root-cause before continuing. |
| Trades settled in adjacent tax year (Dec 30/31) | Confirm the internal ledger dated the lot on the **trade date**. Settlement moving into January (SEC Rule 15c6-1 has set T+1 since 2024-05-28) does not move the tax year or the 1099-B year. |

## Common Pitfalls

- **End-of-Year Trade-Date Boundary**: It is tempting to write off a year-boundary `MISSING_IN_BROKER` as "it settled in January". That is wrong and it hides real breaks. The Instructions for Form 1099-B direct the broker to report the **trade date** in box 1c, so a Dec 30/31 sale is on the current year's 1099-B even though it settles in January. Only two things genuinely move a lot across the 1099-B year boundary: an *internal* ledger that books on settlement date (a bug to fix upstream, not a tolerance to accept), and **short sales**, which a broker does not report until the year the customer delivers a security to close the position.
- **Wash Sale Adjustments (§1091)**: Brokers track wash sales across identical CUSIPs *within the same account*. IRC §1091 defines the disallowance window as **±30 calendar days around a loss sale** (61 days total). Internal algorithms trading across multiple accounts or using `substantially identical` heuristics differently will produce discrepancies. Code `W` adjustment applies if the broker's disallowed amount differs from yours.
- **Corporate Actions**: Stock splits, mergers, spin-offs alter cost basis. Multi-leg reorganization events cause basis splitting (e.g. spin-off parent + new-issue child). Always run corporate-action reconciliation **before** running this engine or expect false-positive basis drift of a few pennies.
- **Covered vs Noncovered Securities**: A lot acquired for cash **after 2010** is "covered" — the broker reports basis to the IRS in box 1e and checks box 12. Noncovered lots have box 5 checked and the taxpayer supplies the basis on Form 8949 column (e) directly. The phase-in differs by instrument (stock after 2010; average-basis stock after 2011; less complex debt, options and securities futures after 2013; complex debt after 2015) — see `references/standards.md`. Box routing must follow the **broker's** box 12, since that is the form the IRS matched the return against; `MatchPair.broker_covered` carries it. The engine does **not** raise a discrepancy when the two sides disagree about covered status — compare `covered` against `broker_covered` yourself if that matters.
- **Form 8949 Column (g) Sign**: `basis_delta` is *internal minus broker*, but Form 8949 column (g) runs the other way. The worksheet in the Instructions for Form 8949 takes line 1 as the basis shown on Form 1099-B and line 2 as the correct basis, entering a **negative** number when line 2 exceeds line 1. Filing `internal − broker` instead of `broker − internal` doubles the basis error rather than cancelling it. Read the adjustment off `MatchPair.form_8949_column_g_basis_adjustment` rather than re-deriving it.
- **Float vs Decimal**: Cost basis rounding errors compound across thousands of lots when stored as `float`. This skill uses `Decimal` end-to-end; never coerce monetary fields to `float`.
- **Preliminary vs Final 1099-B**: Brokers issue preliminary 1099-B forms in mid-February and then **corrected** versions anytime — the IRS requires corrected forms within ~30 days of receiving a missing transfer statement. Always reconcile against the latest, finalized version.
- **Multi-Currency Normalization**: If proceeds/basis originate in non-USD, the IRS spot-rate convention must be applied **before** ingestion; the engine treats every monetary field as USD.
- **Truncated Filenames/PDFs**: Many brokers deliver 1099-B as a zipped PDF bundle. Parse to records before calling `load_*_lot*` — the engine does not parse PDFs.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/form-1099-b-and-broker-tax-reporting-reconciliation/scripts -v
```

What they assert:

- Exact and tolerance-bracketed matches.
- Proceeds/basis outside tolerance produce discrepancies but still mark the pair as matched (audit hygiene).
- Wash-sale flag and wash-sale-amount disagree ⇒ discrepancy, not silent match.
- Decimal-precision safety (10-lot, identical-data case).
- Relative-tolerance holds on large lots (1bp on $50M).
- Multi-lot FIFO collision: bucket contains 2 internal + 2 broker lots, paired 1-1, no contamination.
- Negative quantity, backwards date, duplicate-lot-id rejection.
- Non-finite (`NaN`/`Infinity`) quantity, proceeds, basis and wash-sale amount are
  rejected at ingestion, while a genuine `$0.00` basis is still accepted.
- Form 8949 column (g) sign, in both directions plus the noncovered `-0-` case,
  against values derived from the IRS column (g) worksheet.
- Tolerance boundaries: a difference exactly at the absolute bound and exactly at
  the relative bound is accepted, one cent beyond either is flagged, and a
  near-zero broker basis never divides through the relative bound.
- `PROCEEDS_OUTSIDE_TOLERANCE`, and a wash-sale amount present on one side only.
- Deterministic output ordering: discrepancies and matched pairs follow
  internal-ledger insertion order, not randomized hash order.
- Empty ledger and idempotent re-run via `clear()`.
- Operational metrics expose every `DiscrepancyReason` and report correct deltas.

Confirm the implementation against `assets/checklist.md` before production run.

## Success Criteria

A reconciliation run is considered **successful** when:

1. **≥99.5%** of internal lots are matched OR have a classified discrepancy reason.
2. **Zero** lots in the `matched_clean` category actually carry `BASIS_OUTSIDE_TOLERANCE` or `PROCEEDS_OUTSIDE_TOLERANCE` (sanity-check that the engine is honoring tolerances).
3. **Total basis delta** is either exactly `$0.00`, or every contributing pair is
   itemized in `matched_with_discrepancies` and dispositioned against the
   materiality ladder in `assets/checklist.md` §5. Those dollar thresholds are the
   firm's own operating convention — the IRS publishes no de minimis amount for
   basis accuracy on Form 8949.
4. **No internal Python exceptions** during processing. Non-finite (`NaN`/`Inf`)
   monetary or quantity fields are rejected at ingestion with a `ValueError`
   naming the offending lot, so a blank cell in a broker CSV fails loudly at the
   row rather than aborting the run mid-reconciliation.
5. The output `ReconciliationResult.metrics()` is archived alongside the run for audit-defense.

## Related Skills

- `automated-tax-lot-reporting-pipeline` — pipeline that produces the internal ledger before reconciliation.
- `wash-sale-rule-tracking-us` — manages wash-sale classification and disallowed-amount calculation upstream.
- `corporate-action-adjusted-backtesting` — produces the basis-adjusted ledger for the tax year.
- `record-keeping-requirements-for-tax-audit-defense` — retention policies for the discrepancy report and reconciliation log.
- `cross-border-data-transfer-restrictions-for-trade-data` — PII handling for 1099-B data in flight.
- `multi-currency-pnl-and-fx-conversion` — for non-USD brokers that must be normalized before this engine's `load_*` calls.
- `fifo-vs-specific-lot-tax-accounting-methods` — the lot-selection method the internal ledger must share with the filed return for lot mapping to hold.
- `section-1256-contract-tax-treatment-us-futures` — for the aggregated boxes 8–11 contracts this engine excludes.
- `crypto-transaction-tax-lot-tracking` — for digital-asset lots reported on Form 1099-DA rather than 1099-B.

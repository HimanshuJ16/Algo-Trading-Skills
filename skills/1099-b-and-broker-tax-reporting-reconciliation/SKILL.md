---
name: 1099-b-and-broker-tax-reporting-reconciliation
description: Use when reconciling internal algorithmic trading trade ledgers
  (realized tax lots) against official broker 1099-B filings for end-of-year
  tax preparation, broker-error detection, or Form 8949 generation. Compares
  lots on (symbol, quantity, dates, proceeds, cost basis) within configurable
  tolerances, surfaces wash-sale mismatches, missing records, and basis
  out-of-tolerance events. Single-jurisdiction (US/IRS) scope.
domain: tax-accounting-reporting-global
subdomain: tax-reporting
tags:
- tax
- reconciliation
- compliance
- 1099-B
- form-8949
- wash-sale
- decimal-arithmetic
brokers_frameworks: []
jurisdictions:
- US
version: "1.2.0"
author: System
license: MIT
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
- Understanding of **trade-date accounting** (IRS requires tax recognition on the execution date — see IRC §1031 / Pub 550).
- US dollar as the reporting currency (multi-currency normalization is out of scope; pre-normalize before ingestion).

## Workflow

1. **Ingestion**: Load internal tax lots and broker 1099-B lines through `load_internal_lot*` / `load_broker_lot*`. The engine validates each lot — non-positive quantities and backwards dates are rejected at ingestion time.
2. **Hash-bucketed matching**: Lots are grouped by `(symbol, quantity, acquired_date, sold_date)`; matching inside each bucket runs in two passes:
   - **Pass 1 (clean)**: Pair lots that produce *no* discrepancies on proceeds, basis, wash-sale flag, or wash-sale-amount.
   - **Pass 2 (best-effort)**: Pair any remaining lots in the bucket positionally; these typically populate `matched_with_discrepancies`.
3. **Tolerance evaluation**: Each pair is checked against `ToleranceConfig` (absolute cents + relative basis-percent). Either bound passing ⇒ acceptable.
4. **Classification**: The result is split into `matched_clean`, `matched_with_discrepancies`, `missing_in_1099b`, `missing_in_internal`.
5. **Discrepancy reporting**: Every flagged item carries a `DiscrepancyReason` enum, a human-readable `details` string, and (where applicable) a signed `difference_amount`.
6. **Operational metrics**: `ReconciliationResult.metrics()` returns counts by reason and net dollar deltas on proceeds and basis.

For the full lifecycle (data freeze, broker retrieval, CPA handoff), see `references/workflows.md`.

## Decision Points

| Situation | Action |
|-----------|--------|
| All lots matched_clean | Pass-through to Form 8949 generation; no manual review needed. |
| Any `BASIS_OUTSIDE_TOLERANCE` | Apply Form 8949 column (g) adjustment with **code B** in column (f). |
| Any `WASH_SALE_FLAG_MISMATCH` or `WASH_SALE_AMOUNT_MISMATCH` | Compare against actual trade tickets; broker typically authoritative for same-account, same-CUSIP. |
| Any `MISSING_IN_BROKER` | Almost always a December 30/31 trade settling in January of the next tax year. Verify if the trade is in the broker's `trades` view; document for audit defense. |
| Any `MISSING_IN_INTERNAL` | Internal ledger missed the trade. Investigate broken-import, missed write, or transfer-in failure; **do not** file Form 8949 until reconciled. |
| Net `total_basis_delta` > $100 absolute | Manual CPA review before filing. |
| Aggregate discrepancy count > 0.5% of total lot count | Likely systemic issue; halt pipeline and root-cause before continuing. |
| Trades settled in adjacent tax year (Dec 30/31) | Verify tax-year classification; SEC Rule 15c6-1 sets T+1 settlement. |

## Common Pitfalls

- **End-of-Year Settlement Disconnects**: Trades executed on Dec 30/31 appear on internal ledgers for the current tax year but settle in the next, causing broker mismatches. The engine flags them as `MISSING_IN_BROKER`; teams should expect this and reconcile against the broker's confirmation report, not the 1099-B draft.
- **Wash Sale Adjustments (§1091)**: Brokers track wash sales across identical CUSIPs *within the same account*. IRC §1091 defines the disallowance window as **±30 calendar days around a loss sale** (61 days total). Internal algorithms trading across multiple accounts or using `substantially identical` heuristics differently will produce discrepancies. Code `W` adjustment applies if the broker's disallowed amount differs from yours.
- **Corporate Actions**: Stock splits, mergers, spin-offs alter cost basis. Multi-leg reorganization events cause basis splitting (e.g. spin-off parent + new-issue child). Always run corporate-action reconciliation **before** running this engine or expect false-positive basis drift of a few pennies.
- **Covered vs Noncovered Securities**: Post-2010 acquisitions are "covered" — broker reports basis to IRS and fills box 1e. Pre-2011 lots are noncovered — taxpayer must report correct basis on Form 8949 columns (d)/(e) directly. Mixing the two without the `covered` flag gives wrong 8949 box routing.
- **Float vs Decimal**: Cost basis rounding errors compound across thousands of lots when stored as `float`. This skill uses `Decimal` end-to-end; never coerce monetary fields to `float`.
- **Preliminary vs Final 1099-B**: Brokers issue preliminary 1099-B forms in mid-February and then **corrected** versions anytime — the IRS requires corrected forms within ~30 days of receiving a missing transfer statement. Always reconcile against the latest, finalized version.
- **Multi-Currency Normalization**: If proceeds/basis originate in non-USD, the IRS spot-rate convention must be applied **before** ingestion; the engine treats every monetary field as USD.
- **Truncated Filenames/PDFs**: Many brokers deliver 1099-B as a zipped PDF bundle. Parse to records before calling `load_*_lot*` — the engine does not parse PDFs.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/1099-b-and-broker-tax-reporting-reconciliation/scripts -v
```

What they assert:

- Exact and tolerance-bracketed matches.
- Proceeds/basis outside tolerance produce discrepancies but still mark the pair as matched (audit hygiene).
- Wash-sale flag and wash-sale-amount disagree ⇒ discrepancy, not silent match.
- Decimal-precision safety (10-lot, identical-data case).
- Relative-tolerance holds on large lots (1bp on $50M).
- Multi-lot FIFO collision: bucket contains 2 internal + 2 broker lots, paired 1-1, no contamination.
- Negative quantity, backwards date, duplicate-lot-id rejection.
- Empty ledger and idempotent re-run via `clear()`.
- Operational metrics report correct deltas.

Confirm the implementation against `assets/checklist.md` before production run.

## Success Criteria

A reconciliation run is considered **successful** when:

1. **≥99.5%** of internal lots are matched OR have a classified discrepancy reason.
2. **Zero** lots in the `matched_clean` category actually carry `BASIS_OUTSIDE_TOLERANCE` or `PROCEEDS_OUTSIDE_TOLERANCE` (sanity-check that the engine is honoring tolerances).
3. **Total basis delta** within `[$0.00, $0.00]` (clean) or fully itemized as discrepancies ≥ configurable threshold (e.g. $50 absolute).
4. **No internal Python exceptions** during processing.
5. The output `ReconciliationResult.metrics()` is archived alongside the run for audit-defense.

## Related Skills

- `automated-tax-lot-reporting-pipeline` — pipeline that produces the internal ledger before reconciliation.
- `wash-sale-rule-tracking-us` — manages wash-sale classification and disallowed-amount calculation upstream.
- `corporate-action-adjusted-backtesting` — produces the basis-adjusted ledger for the tax year.
- `record-keeping-requirements-for-tax-audit-defense` — retention policies for the discrepancy report and reconciliation log.
- `cross-border-data-transfer-restrictions-for-trade-data` — PII handling for 1099-B data in flight.
- `multi-currency-pnl-and-fx-conversion` — for non-USD brokers that must be normalized before this engine's `load_*` calls.
- `1099-b-and-broker-tax-reporting-reconciliation` (self) — see also the Roundtrip CPA-handoff pattern documented in `references/workflows.md`.

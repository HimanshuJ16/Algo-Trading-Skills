---
name: fifo-vs-specific-lot-tax-accounting-methods
description: >-
  Tax lot matching engine for securities under FIFO, LIFO, HIFO and Specific Identification, computing per-lot realized short-term (STCG) vs long-term (LTCG) capital gains for US federal reporting, with the specific-identification record enforced rather than assumed.
domain: Tax Accounting & Reporting
subdomain: Tax Lot Matching & Capital Gains Accounting
tags: ["tax-accounting", "fifo", "hifo", "lifo", "specific-identification", "capital-gains", "stcg", "ltcg", "tax-lot-matching", "form-8949"]
brokers_frameworks: ["IRS Form 8949", "Treas. Reg. 1.1012-1", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in post-trade processing, tax-loss harvesting engines, and fund
accounting pipelines when a sell order must be matched against an inventory of
open tax lots for **US federal** reporting. When a position was built across
several purchase dates and prices, the matching method (**FIFO**, **LIFO**,
**HIFO**, or explicit **Specific Identification**) determines which basis is
consumed and therefore how much of the sale lands in short-term (STCG) vs
long-term (LTCG) capital gain. This module ranks lots, depletes them, and emits a
per-lot breakdown that maps to Form 8949 rows.

## When NOT to Use

- **Any jurisdiction other than the US.** Every rule encoded here is US federal.
  Other regimes are not "the same thing with a different threshold" — they are
  structurally different. The UK matches disposals same-day, then against
  acquisitions in the next 30 days, then against the Section 104 pool, with no
  taxpayer election at all. India mandates FIFO for dematerialised securities and
  uses a 12-month long-term threshold for listed securities. Neither has a
  concept this engine models. See `references/standards.md`.
- **You want HIFO or LIFO applied automatically to minimise tax.** For securities
  there is exactly one default — FIFO. Everything else is an election of
  *specific identification* and must have been identified no later than the
  earlier of the settlement date or the Rule 15c6-1 settlement time. The engine
  refuses HIFO/LIFO/SPECIFIC_LOT without an `identification_reference` rather
  than producing a basis figure the taxpayer cannot support. Re-ranking lots
  favourably at filing time is not a feature; it is a restatement risk.
- **You need wash-sale adjustments.** This module applies none. Feed it lots
  whose basis is already adjusted — see `wash-sale-rule-tracking-us`.
- **You need average cost.** Average basis is available only for shares in
  regulated investment companies and for DRP shares acquired after 2010-12-31,
  held with a custodian. It is not a general method and is not implemented here.
- **The taxpayer made a §475(f) mark-to-market election.** A mark-to-market
  trader has no capital lot matching to do. See
  `mark-to-market-election-for-active-traders-us`.
- **You need corporate-action basis adjustments.** Splits, spin-offs and return
  of capital change `cost_basis_per_share` and sometimes `quantity`. Adjust the
  inventory upstream; this engine takes basis as given.
- **You need exact decimal accounting.** Amounts are floats, consistent with the
  rest of this repository, and are rounded to cents at the boundary. For
  filing-grade ledgers, reconcile against the broker's own Form 1099-B.

## Prerequisites

- Open tax lot inventory for one symbol (`lot_id`, `symbol`,
  `acquisition_date_iso`, `quantity`, `cost_basis_per_share`), with unique lot
  ids and basis already inclusive of acquisition commissions and any prior
  wash-sale or corporate-action adjustment.
- Sell order details: `sale_qty`, `sale_price`, and a **`sale_date`**. The sale
  date is required — STCG vs LTCG is a function of the acquisition date *and*
  the sale date, and cannot be determined from the lot alone.
- For any non-FIFO election: an `identification_reference` — a broker
  confirmation id or standing-instruction id evidencing the identification.

## Workflow

1. **Choose the method before the sale, not after**:
   - `FIFO` (default): oldest `acquisition_date_iso` first. This is the treatment
     that applies absent an adequate identification; it needs no election and no
     identification record.
   - `LIFO` / `HIFO`: newest acquisition / highest `cost_basis_per_share` first.
     These are not separate regulatory methods — they are standing instructions
     for which particular shares to deliver, i.e. specific identification.
   - `SPECIFIC_LOT`: consume exactly `target_lot_ids`, **in the order given**.
   - An unrecognised strategy raises rather than falling back to FIFO. A typo
     silently treated as FIFO changes which lots are consumed and the tax owed.
2. **Validate the inventory before consuming anything**:
   - Reject lots spanning more than one symbol, duplicate `lot_id`s, non-positive
     quantities, negative basis, and unparseable dates.
   - Reject any lot acquired **after** `sale_date` — it cannot supply basis for a
     sale that predates it.
   - Parse dates; never compare them as strings. `"2024-10-05" < "2024-9-01"`
     lexicographically, which reverses FIFO ordering.
3. **Plan, then commit**: build the full match plan against copies first. A sale
   that exceeds inventory, or a `SPECIFIC_LOT` designation that does not cover
   `sale_qty`, raises with **no lots consumed** and the caller's inventory
   untouched. The engine will not spill a specific-identification sale into
   undesignated lots.
4. **Realize per lot**: $\text{Gain/Loss} = (\text{Sale Price} - \text{Cost Basis
   per Share}) \times \text{Shares Matched}$, computed for each matched lot
   separately.
5. **Classify by calendar anniversary, not by a day count**:
   - The holding period begins the **day after** acquisition and includes the day
     of disposition. Long-term means held **more than one year**, so a sale on
     the one-year anniversary is exactly one year and is `STCG`.
   - Compare `sale_date` against the acquisition anniversary. Do **not** test
     `days_held > 365`: across a leap year 366 elapsed days can still be exactly
     one year.
6. **Emit per-lot rows**: each match becomes one `RealizedLotMatch` with its own
   acquisition date, sale date, proceeds, basis and term, because a single sale
   can straddle Form 8949 Part I (short-term) and Part II (long-term). The report
   exposes `is_mixed_term` and logs a warning when it does.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Classifying the Holding Period With a 365-Day Count**: bought 2024-01-01,
  sold 2025-01-01 — 366 elapsed days across a leap year, but one year to the day,
  so short-term. A `days_held > 365` test reports long-term and understates the
  tax. Use calendar anniversaries.
- **Storing `holding_period_days` on the Lot**: a holding period depends on the
  sale date, so a number frozen on the lot is stale for every subsequent sale and
  can contradict the lot's own acquisition date. The dataclass deliberately has
  no such field.
- **Treating HIFO as a Method You Can Pick at Filing Time**: for securities the
  only default is FIFO. HIFO and LIFO are specific identification and must have
  been identified no later than the earlier of the settlement date or the Rule
  15c6-1 settlement time — which has been T+1 since 2024-05-28, so the window is
  roughly a day, not a filing season.
- **Letting a Specific-Lot Sale Spill Into Undesignated Lots**: designating 100
  shares and selling 250 does not make the other 150 identified. Consuming them
  silently delivers shares the taxpayer never named. Designate more lots, or run
  the remainder as an explicit FIFO sale.
- **Sorting Date Strings Instead of Dates**: any non-zero-padded or mixed-format
  date breaks lexicographic ordering and silently reverses FIFO/LIFO.
- **Collapsing a Mixed-Term Sale Into One Row**: a sale matching both an old and
  a recent lot is not "long-term" or "short-term" — it is both, and splits across
  Part I and Part II. Read `matched_lots`, not the aggregates.
- **Pooling Lots Across Symbols or Accounts**: a sale of one security must never
  consume another's basis. The engine raises on mixed symbols.
- **Matching Against a Lot Acquired After the Sale**: usually a backfill or
  timestamp bug; without a sale date it is undetectable.
- **Assuming FIFO Is Always the Worse Answer**: HIFO minimises the *gain*, not
  necessarily the *tax*. Realising a long-term gain at the preferential rate can
  beat realising a smaller short-term gain, and harvesting a loss into a
  replacement purchase can trigger a wash sale that disallows it entirely.

## Verification

- Instantiate `TaxLotAccountingEngine` (defaults to FIFO). Ingest 3 AAPL lots of
  100 shares each: LOT_A (\$100.00, acquired 2025-06-01), LOT_B (\$150.00,
  2026-04-15), LOT_C (\$120.00, 2026-06-05). Sell 100 shares at \$140.00 on
  2026-07-24.
  - FIFO $\implies$ matches LOT_A, realizes $+\$4{,}000.00$ LTCG.
  - HIFO (with `identification_reference`) $\implies$ matches LOT_B, realizes
    $-\$1{,}000.00$ STCG.
  - `SPECIFIC_LOT` with `target_lot_ids=["LOT_C"]` $\implies$ realizes
    $+\$2{,}000.00$ STCG.
- Call HIFO **without** `identification_reference` and verify it raises rather
  than silently optimising.
- Sell 250 shares with `SPECIFIC_LOT` designating only LOT_C; verify it raises
  and that the caller's lots still hold 100/100/100 — nothing consumed.
- Verify the leap-year boundary: lot acquired 2024-01-01, sold 2025-01-01
  $\implies$ `STCG` with `holding_period_days == 366`; sold 2025-01-02
  $\implies$ `LTCG`.
- Sell 150 shares FIFO and verify `is_mixed_term` is True, with $+\$4{,}000.00$
  LTCG and $-\$500.00$ STCG in separate `matched_lots` rows.
- Provide lots for two different symbols and verify it raises.
- Run `python scripts/test_fifo_vs_specific_lot_tax_accounting_methods.py`.

## Related Skills

- `crypto-transaction-tax-lot-tracking`
- `wash-sale-rule-tracking-us`
- `cross-strategy-tax-lot-optimization`
- `automated-tax-lot-reporting-pipeline`
- `mark-to-market-election-for-active-traders-us`
- `record-keeping-requirements-for-tax-audit-defense`

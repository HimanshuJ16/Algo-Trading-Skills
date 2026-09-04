---
name: mark-to-market-election-for-active-traders-us
description: >-
  Use when an active trader with trader tax status is deciding on or operating under an
  IRC 475(f) mark-to-market election, comparing year-end marks and ordinary treatment
  against default capital accounting with wash sales.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: tax-accounting, section-475f, mark-to-market, wash-sale-exemption, form-4797, trader-tax-status, ordinary-loss, excess-business-loss
  brokers_frameworks: "IRS Code Section 475(f); Form 4797 Part II; Form 3115; Form 461; Rev. Proc. 99-17; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when computing US federal tax figures for an active trader who
qualifies for Trader Tax Status (TTS) and is deciding on, or already operating
under, an **IRC Section 475(f) mark-to-market election**.

Under default capital accounting, wash sale rules (IRC § 1091) defer losses and
net capital losses are deductible against ordinary income only up to $3,000
($1,500 married filing separately, § 1211(b)). A § 475(f) election converts
trading P&L to **ordinary** gain/loss on **Form 4797 Part II**, disapplies § 1091
to losses recognized under § 475(a), and marks open positions to fair market
value at year end.

## When NOT to Use

- **You are an investor, not a trader.** § 475(f) is available only to a person
  engaged in a trade or business as a trader. TTS qualification is a separate
  facts-and-circumstances question this skill does not decide.
- **Futures and broad-based index options.** § 475(f)(1) reaches *securities*
  only. § 1256 contracts need the separate § 475(f)(2) commodities election, and
  making it **forfeits 60/40 treatment** — use
  `section-1256-contract-tax-treatment-us-futures` instead.
- **You need the wash sale computation itself.** This engine consumes a
  disallowance amount; it does not scan the 61-day window. Use
  `wash-sale-rule-tracking-us`.
- **State returns, § 481(a) catch-up adjustments, or § 1092 straddles.** Out of
  scope; see Prerequisites.
- **As a substitute for a tax professional.** Output is audit support for a
  return position, not the return.

## Prerequisites

- Python 3.10+, standard library only.
- Confirmation that the election is **perfected**, not merely intended: a
  Rev. Proc. 99-17 statement filed by the **unextended** due date of the prior
  year's return (new taxpayers: in books and records within 2 months and 15 days
  of the election year's first day), plus **Form 3115** for the method change.
- `realized_trades`: `List[RealizedTrade]` — per-unit `sell_price` and
  `cost_basis`. For a previously marked lot, `cost_basis` must be the prior
  year-end mark (§ 475(a)).
- `open_tax_lots`: `List[TaxLot]` — per-unit `buy_price`, `year_end_fmv_price`,
  and `prior_year_end_mark_price` for any lot carried across a year end.
- `filing_status` and `tax_year` — both drive statutory thresholds.
- `other_net_business_income_usd` if the taxpayer has any other trade or
  business; § 461(l) is tested on the aggregate.

Explicitly out of scope: § 481(a) adjustments and their four-year spread,
§ 1092 straddle deferral, short-term/long-term character splitting, state
conformity, and decimal-exact accounting (amounts are IEEE-754 doubles summed
with `math.fsum` and rounded to cents).

## Workflow

1. **Verify the election before trusting the flag.** `is_mtm_elected=True`
   asserts filed paperwork. If the Rev. Proc. 99-17 statement or Form 3115 is
   unconfirmed, compute the capital branch — an unperfected election reported on
   Form 4797 loses the entire benefit on examination. Pass
   `election_effective_first_tax_year`; the engine rejects a `tax_year` that
   precedes it (§ 475(f)(3): the election is never retroactive).
2. **Partition the blotter.** Elected securities are marked. Securities
   identified for investment under § 475(f)(1)(B) — which requires identification
   in the records *on or before the day acquired*, not a year-end
   reclassification — are excluded, stay capital, and stay subject to § 1091.
   § 1256 contracts are routed out unless `elects_commodities_475f2=True`.
3. **Compute realized and marked P&L.** Realized P&L uses the § 475-adjusted
   basis. Each open lot marks from `prior_year_end_mark_price` when it was
   carried across a year end, otherwise from `buy_price`:
   $$\text{MTM P\&L} = \sum_i (\text{FMV}_i - \text{Basis}^{475}_i) \times \text{Qty}_i$$
   Marking a carried lot from its original purchase price double-counts every
   prior year's appreciation. Flag open shorts with `is_short=True`; a negative
   `quantity` is rejected rather than silently inverting the mark.
4. **Apply the limitation that actually governs.**
   - *Elected:* the ordinary loss is limited by **§ 461(l)** — aggregate net
     business loss above the threshold ($256,000 / $512,000 joint for 2026,
     Rev. Proc. 2025-32) is disallowed and carried forward as an NOL, itself
     capped at 80% of taxable income on use. When no citable threshold exists for
     the year, the engine reports `NOT_EVALUATED_SEE_FORM_461` rather than
     implying an unlimited deduction.
   - *Not elected:* § 1211(b) allows the lower of $3,000 ($1,500 MFS) or the
     excess over capital gains; § 1212(b) carries the remainder forward
     indefinitely.
5. **Read the `warnings` list.** Anything routed out, capped, or left
   unevaluated is recorded there. An empty list is the only clean result.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating "no $3,000 cap" as "no cap".** § 475(f) removes the *capital* loss
  limitation, not § 461(l). A single filer with a $600,000 ordinary trading loss
  in 2026 deducts $256,000 and carries $344,000 forward as an NOL — not $600,000.
- **Re-marking a carried lot from its purchase price.** § 475(a) requires
  "proper adjustment ... in the amount of any gain or loss subsequently
  realized". A lot bought at 100, marked to 140 last year and worth 150 this
  year contributes $10/share, not $50/share. Running the engine two years in a
  row without `prior_year_end_mark_price` overstates income by the whole first
  mark.
- **Universalizing the wash sale waiver.** § 475(d)(1) disapplies § 1091 only to
  losses recognized under § 475(a) — and in the same sentence expressly
  **preserves § 1092**. Securities identified for investment, and any account
  outside the elected trade or business, remain fully subject to § 1091.
- **Sweeping futures into a securities election.** A § 475(f)(1) election reaches
  no § 1256 contract. Conversely, adding the § 475(f)(2) commodities election to
  capture ordinary loss treatment silently forfeits 60/40 on every future — the
  trade is rarely worth it for a profitable futures book.
- **Back-dating the investment identification.** § 475(f)(1)(B) borrows the
  § 475(b)(2) rule: identification must exist in the records before the close of
  the day the security was acquired. Picking losers at year end and calling them
  investments does not work.
- **Electing late.** The statement is due by the **unextended** due date of the
  *prior* year's return. Missing it by a day pushes the election a full year out;
  there is no relief in the engine and generally none from the IRS.
- **Applying self-employment tax to Form 4797 Part II trading income.** IRS Topic
  No. 429: trading gains are not subject to SE tax.
- **Discarding the capital loss carryforward.** § 1212(b) carries the unallowed
  excess forward indefinitely; dropping it silently overstates next year's tax.

## Verification

- Instantiate `MarkToMarketTaxEngine(filing_status="SINGLE")`. For a trader with
  a $600,000 net ordinary trading loss in `tax_year=2026` and
  `is_mtm_elected=True`: verify `wash_sale_disallowed_usd == 0.0`,
  `total_reportable_taxable_pl_usd == -600000.0`,
  `reportable_loss_deduction_usd == -256000.0`,
  `excess_business_loss_disallowed_usd == 344000.0`, and
  `tax_form_mapping == "Form 4797 Part II (Ordinary Income)"`.
- With `is_mtm_elected=False` and a $10,000 realized capital loss: verify
  `reportable_loss_deduction_usd == -3000.0`,
  `capital_loss_carryforward_usd == -7000.0`, and Schedule D mapping.
- With a lot at `buy_price=100`, `prior_year_end_mark_price=140`,
  `year_end_fmv_price=150`, `quantity=100`: verify
  `unrealized_mtm_pl_usd == 1000.0`, not `5000.0`.
- Run the suite:

```bash
python -m unittest discover -s skills/mark-to-market-election-for-active-traders-us/scripts
```

## Related Skills

- `wash-sale-rule-tracking-us`
- `section-1256-contract-tax-treatment-us-futures`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `record-keeping-requirements-for-tax-audit-defense`

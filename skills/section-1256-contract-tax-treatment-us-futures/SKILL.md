---
name: section-1256-contract-tax-treatment-us-futures
description: >-
  Use when computing Form 6781 Part I for a book of IRC 1256 contracts: the mandatory
  last-business-day mark, the prior-year basis adjustment, the 60/40 character split and
  the three-year loss carryback election.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: section-1256, 60-40-rule, mark-to-market, form-6781, futures-taxation, section-1212c-carryback, index-options
  brokers_frameworks: "26 U.S.C. Sec. 1256; 26 U.S.C. Sec. 1212(c); IRS Form 6781; IRS Schedule D (Form 1040); IRS Pub. 550; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

> **NOT TAX ADVICE.** This skill models **US federal** income tax only, as audit
> support for a return position. Statutory citations are to the Internal Revenue
> Code (26 U.S.C.). Have any figure reviewed by a qualified US tax professional
> before it reaches a return.

## When to Use

Use this skill to compute Form 6781 Part I for one tax year on a book of IRC
§1256 contracts, and to model what a §1256 loss is actually worth.

§1256 does three things at once, and all three matter:

1. **A mandatory mark.** Each §1256 contract held at the close of the taxable
   year "shall be treated as sold for its fair market value on the **last
   business day** of such taxable year" (§1256(a)(1)) — whether or not you closed
   it, and whether or not you want the income.
2. **A character split.** 40% short-term, 60% long-term (§1256(a)(3)),
   "regardless of how long the contracts were held" (Instructions for Form 6781).
   With the top 2026 ordinary rate at 37% and the top §1(h) capital rate at 20%,
   the blended rate is 26.8% — a saving of **10.2 percentage points** of net gain
   against all-short-term treatment.
3. **A loss regime of its own.** A net §1256 contracts loss can be carried **back
   3 years** against prior §1256 gains under §1212(c), keeping its 60/40
   character. A §1256 loss is deferred, not forfeited.

## When NOT to Use

- **To decide whether an instrument qualifies.** Eligibility under §1256(b)(1) is
  a legal determination the engine takes as an **input** via `contract_type`,
  never an inference from a symbol. Whether a retail forex position is a
  §1256(g)(2) foreign currency contract is genuinely unsettled — see
  `currency-gain-loss-tax-treatment-for-forex-trading`.
- **For straddles.** §1256(a)(4) disapplies §1092 only where *every* leg is a
  §1256 contract. Mixed straddles, the §1256(d) mixed straddle election and
  Form 6781 Part II are out of scope; flag such legs and they are routed out.
- **For identified hedges.** §1256(e) takes them out of the mark entirely and
  their gain or loss is **ordinary**. Flag them and they are excluded.
- **Under a §475(f)(2) commodities election.** That election disapplies §1256(a)
  and forfeits 60/40. See `mark-to-market-election-for-active-traders-us`.
- **For state tax, or as a return preparer.** Federal only; the engine emits
  figures for Form 6781, not a filed form.

## Prerequisites

- Python 3.7+, standard library only.
- A blotter for **one tax year** of `Section1256Trade` records:
  `trade_id`, `symbol`, `contract_type`, `realized_pnl_usd`,
  `year_end_mark_pnl_usd`, `is_open_at_year_end`,
  `prior_year_end_cumulative_mark_usd`, `is_identified_hedging_transaction`,
  `is_part_of_mixed_straddle`.
- A per-position §1256(b)(1) determination expressed as one of
  `REGULATED_FUTURES`, `FOREIGN_CURRENCY_CONTRACT`, `NONEQUITY_OPTION`,
  `DEALER_EQUITY_OPTION`, `DEALER_SECURITIES_FUTURES_CONTRACT`. Everything else
  goes in as `EQUITY_OPTION`, `SECURITIES_FUTURES_CONTRACT`,
  `SWAP_OR_NOTIONAL_PRINCIPAL_CONTRACT` or `OTHER_NON_SECTION_1256` and is
  reported as excluded rather than dropped.
- Marginal rates **as decimal fractions**: `short_term_capital_gains_rate=0.37`,
  `long_term_capital_gains_rate=0.20`. `37.0` is rejected, not read as 37%.
- For a loss year: `prior_section_1256_gains_usd` (aggregate net §1256 gain in the
  3 preceding years) and `other_capital_gains_usd`.

## Workflow

1. **Classify each position — do not let the engine guess.** A regulated futures
   contract and a broad-based index option (SPX, NDX, RUT, VIX) qualify; an
   option on a single stock or on **ETF shares** (SPY, QQQ, IWM) is an option on
   stock and therefore an *equity* option under §1256(g)(6), which does not.
   Securities futures contracts and swaps are excluded by §1256(b)(2). Decision
   point: an unclassified position is not a §1256 contract — classify it out
   explicitly so its P&L still appears in the report.
2. **Route out what §1256 Part I does not reach.** Identified §1256(e) hedges
   (ordinary, Form 6781 line 4) and mixed straddle legs (Form 6781 Part II) are
   excluded with a warning. Absorbing either into the 60/40 split misstates both
   character and amount.
3. **Mark every open contract to the last business day.** Set
   `is_open_at_year_end=True` and supply `year_end_mark_pnl_usd`. A mark on a
   position flagged closed is **rejected**, not silently ignored — that silence
   is how a year's income gets understated by the whole mark.
4. **Make the §1256(a)(2) adjustment on anything carried across a year end.**
   Supply `prior_year_end_cumulative_mark_usd` and state the year's realized and
   mark amounts **inception-to-date**; the engine subtracts the prior mark. A
   Form 1099-B box 11 figure is already broker-adjusted — do *not* set the field
   for it, or the prior mark comes off twice.
5. **Split 60/40 and read the line map.** Line 5 is net §1256 P&L, line 7 is line
   5 plus any carryback, line 8 is 40% → Schedule D line 4, line 9 is 60% →
   Schedule D line 11.
6. **In a loss year, run the waterfall before concluding anything.** Decision
   point: do not treat the excess over $3,000 as lost.
   - **§1212(c) / box D**: carry the net §1256 contracts loss back 3 years,
     against prior §1256 gains only, earliest year first, character preserved.
     Then verify by hand the per-year Schedule D line 16 cap and that no
     carryback year's NOL is increased — the engine takes an aggregate ceiling
     and warns.
   - **§1211(b)**: the remainder hits other capital gains first, then the lower of
     $3,000 ($1,500 married filing separately) or the excess.
   - **§1212(b)**: anything left carries forward indefinitely.
7. **Read `warnings` before using any figure.** Every exclusion, every prior-year
   adjustment, every uncomputed limitation lands there. An empty list is the only
   clean result.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a carried contract's whole lifetime gain again.** §1256(a)(2)
  requires "proper adjustment ... for gain or loss taken into account by reason
  of paragraph (1)". A contract marked at +$30,000 last 12/31 and closed this
  year at +$50,000 inception-to-date contributes **$20,000** this year, not
  $50,000. Two years without the adjustment overstates income by the first mark.
- **Modelling a §1256 loss as capped at $3,000 forever.** That skips the §1212(c)
  three-year carryback and the indefinite §1212(b) carryforward. On a $50,000 loss
  with $30,000 of prior §1256 gains the benefit is **$9,150**, not $1,110.
- **Calling the mark date December 31.** The statute says the **last business
  day**. In years where 12/31 falls on a weekend or holiday, marking to 12/31
  values a contract on a day with no settlement price.
- **Assuming any index option qualifies.** §1256(g)(6) makes an option on a
  *narrow-based* security index an equity option. Broad-based index options are
  nonequity options; ETF options are options on stock. The distinction, not the
  word "index", is what decides.
- **Sweeping an identified hedge into Part I.** §1256(e) removes it from the mark
  and its gain or loss is **ordinary** — a Form 6781 line 4 adjustment. Reporting
  it as 60/40 capital is wrong in both character and placement.
- **Running a mixed straddle through Part I.** §1256(a)(4) shelters a straddle
  from §1092 only when every leg is a §1256 contract. Otherwise the §1256 loss
  leg must be reduced by unrecognized gain on the non-§1256 leg first.
- **Quoting 26.8% as the effective maximum rate.** It ignores the §1411 net
  investment income tax: §1256 gain of a trader in commodities or financial
  instruments is net investment income (§1411(c)(1)(A)(ii)), so the top federal
  rate is 30.6% (0.60 × 23.8% + 0.40 × 40.8%). Because NIIT is character-blind
  the **10.2-point saving is unaffected** — but the tax bill is not.
- **Forgetting that capital losses hit capital gains first.** §1211(b) allows
  losses to the extent of gains before the $3,000 cap; omitting
  `other_capital_gains_usd` overstates what the cap costs.
- **Dropping non-qualifying positions on the floor.** They still belong on
  Form 8949 / Schedule D by their actual holding period. This engine reports them
  in `excluded_non_section_1256_pnl_usd` rather than discarding them.
- **Passing rates as percentages.** `37.0` is rejected; silently accepting it
  would overstate tax a hundredfold.

## Verification

- `Section1256ContractTaxTreatmentUsFuturesEngine()`, one `REGULATED_FUTURES`
  trade with `realized_pnl_usd=100000.0`: line 9 = **$60,000**, line 8 =
  **$40,000**, `estimated_tax_usd` = **$26,800**,
  `estimated_tax_if_all_short_term_usd` = **$37,000**,
  `tax_savings_vs_short_term_usd` = **$10,200**, `blended_rate_applied` = 0.268.
- Same trade with `net_investment_income_tax_rate=0.038`: tax **$30,600**,
  all-short-term **$40,800**, saving still **$10,200**.
- `NONEQUITY_OPTION` on SPX, `realized_pnl_usd=20000`,
  `year_end_mark_pnl_usd=30000`, `is_open_at_year_end=True`: line 5 = **$50,000**,
  line 9 = $30,000, line 8 = $20,000.
- The same mark with `is_open_at_year_end=False` must **raise `ValueError`**.
- `realized_pnl_usd=50000` with `prior_year_end_cumulative_mark_usd=30000`:
  `net_section_1256_pnl_usd` = **$20,000**, not $50,000.
- `EQUITY_OPTION` on AAPL, $50,000: line 5 = $0.00,
  `excluded_non_section_1256_pnl_usd` = **$50,000**, one warning.
- $50,000 net loss, single, `elect_section_1212c_carryback=True`,
  `prior_section_1256_gains_usd=30000`: `net_section_1256_contracts_loss_usd` =
  **$47,000**, line 6 = **$30,000**, line 7 = **-$20,000**,
  `capital_loss_carryforward_usd` = **$17,000**,
  `estimated_loss_tax_benefit_usd` = **$9,150**.
- Same loss with `other_capital_gains_usd=10000` and no election: offset $10,000,
  ordinary deduction $3,000, carryforward $37,000, benefit **$3,790**.
- Same loss, `filing_status="MARRIED_FILING_SEPARATELY"`: allowance $1,500,
  carryforward $48,500, benefit **$555**.
- `short_term_capital_gains_rate=37.0`, a duplicate `trade_id`, a NaN P&L, or
  `taxpayer_is_estate_or_trust=True` with box D must each raise.
- Run the suite:

```bash
python -m unittest discover -s skills/section-1256-contract-tax-treatment-us-futures/scripts
```

## Related Skills

- `currency-gain-loss-tax-treatment-for-forex-trading`
- `mark-to-market-election-for-active-traders-us`
- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `record-keeping-requirements-for-tax-audit-defense`

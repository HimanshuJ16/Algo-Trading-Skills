---
name: wash-sale-rule-tracking-us
description: "US federal wash sale tracking under IRC 26 U.S.C. § 1091 for a high-turnover equity ledger: FIFO tax lot matching, 61-day replacement window scanning (30 days before the disposition, the disposition date, 30 days after), § 1091(d) basis carry-forward into the replacement lot, and Form 1099-B Box 1d/1e/1g figures for one account and one security identifier."
domain: Global Tax Accounting & Regulatory Reporting
subdomain: US IRS Tax Compliance (IRC § 1091)
tags:
- wash-sale
- irs-section-1091
- cost-basis-adjustment
- form-1099-b
- capital-loss-disallowance
- tax-lots
- fifo-matching
brokers_frameworks:
- us-irc-1091
- form-1099-b
- finra
- sec
version: "2.0.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill in post-trade tax processing for a **US taxable brokerage account**
when a strategy realizes losses and re-enters the same name inside 61 days —
mean reversion, systematic tax-loss harvesting, and anything that scales in and
out of a position repeatedly. It answers two questions the raw P&L cannot:
how much of the year's realized loss is deductible now, and what basis the shares
still held are carrying into next year.

The engine:

- Matches sells against open tax lots **FIFO**, the treatment that applies absent
  an adequate specific identification.
- Scans the **§ 1091(a) 61-day window** — 30 days before the disposition, the
  disposition date itself, and 30 days after — for replacement acquisitions.
- Disallows the matched portion of each loss and **carries the disallowed amount
  into the replacement lot's basis under § 1091(d)**, so a later sale of those
  replacement shares reports the deferred loss rather than double counting it.
- Applies losses in disposition order and replacements in acquisition order, and
  never lets one replacement share absorb two losses
  (Treas. Reg. § 1.1091-1(b), (c), (e)).
- Emits Form 1099-B figures for the symbol: proceeds (Box 1d), post-adjustment
  basis (Box 1e), disallowed wash loss (Box 1g), and the loss still deferred in
  open lots at the end of the ledger.

## When NOT to Use

This engine models one narrow slice of § 1091. Do not use it, or do not rely on
it alone, in these cases:

- **Any jurisdiction other than the US.** The UK's same-day / 30-day / Section 104
  pool rules and Canada's superficial-loss rule are structurally different, not
  the same rule with a different number of days.
- **Across accounts, or where an IRA is involved.** § 1091 applies to the
  taxpayer, not the account, and a broker's Form 1099-B does not.
  Treas. Reg. § 1.6045-1(d)(6)(iii) requires a broker to report a wash sale only
  when the sale and the purchase are **in the same account** and the securities
  have the **same CUSIP**. Feed this engine one account at a time and reconcile
  the aggregate yourself. Worse, under **Rev. Rul. 2008-5** a replacement purchase
  in the taxpayer's own IRA or Roth IRA disallows the loss with **no basis
  increase anywhere** — that loss is gone permanently, and this engine will
  wrongly show it as deferred.
- **"Substantially identical" securities that are not the same identifier.**
  The statute says *substantially identical*, not *identical*. This engine treats
  the caller's `symbol` as the equivalence class, so if two ETFs tracking the
  same index are substantially identical in the taxpayer's facts, the caller must
  map them to one symbol. There is no bright-line test in § 1091 or the
  regulations; it is facts and circumstances.
- **Options, futures, and contracts to acquire.** Treas. Reg. § 1.1091-1(f)
  defines "acquired" to include entering into a **contract or option** to acquire
  within the 61-day period, and § 1091(f) extends this to contracts settling in
  cash. Buying a call or writing a deep in-the-money put inside the window can
  trigger a wash sale that this equity-only ledger will not see.
- **Short sales.** § 1091(e) applies its own rules to losses on closing a short
  sale. This engine models long-side lots only and **raises** rather than
  guessing when a sell exceeds the open long quantity.
- **Dealers in securities.** § 1091(a) excepts a loss sustained by a dealer in a
  transaction made in the ordinary course of that business.
- **Traders with a § 475(f) mark-to-market election.** A mark-to-market trader has
  ordinary gain and loss and no § 1091 problem — see
  `mark-to-market-election-for-active-traders-us`.
- **Digital assets.** § 1091 reaches "stock or securities". Spot crypto is
  treated as property, so the wash sale rule has not applied to it; extending
  § 1091 to digital assets has been proposed repeatedly and, as of this skill's
  revision, verify current law before relying on either answer. See
  `crypto-transaction-tax-lot-tracking`.
- **Filing-grade decimal accounting.** Amounts are floats, consistent with the
  rest of this repository, rounded to cents at the boundary. Reconcile against
  the broker's own Form 1099-B before filing — see
  `1099-b-and-broker-tax-reporting-reconciliation`.

## Prerequisites

- Python 3.9+, standard library only (`datetime`, `dataclasses`, `enum`,
  `logging`, `typing`).
- A **complete long-side execution history for one account and one symbol**:
  unique `trade_id`, `symbol`, `trade_date` (a `datetime.date`, not a
  `datetime.datetime`), `side`, execution `price`, and `quantity`. Incomplete
  history is the dominant failure mode — a missing early buy makes the engine
  raise, and a missing late buy silently understates Box 1g.
- Basis already inclusive of acquisition commissions and any corporate-action
  adjustment. Splits and spin-offs are not handled here; adjust upstream
  (`corporate-action-adjusted-backtesting`).
- The full window on both sides of the reporting period: **December losses need
  January purchases**, and January losses need the prior December's purchases.
  Evaluating a calendar year in isolation misses both.

## Workflow

1. **Scope the ledger before ingesting.** One account, one security identifier.
   If the taxpayer holds substantially identical securities under different
   identifiers, or holds the same name in a second account or an IRA, this
   engine's answer is a lower bound on the disallowance, not the answer.
2. **Ingest executions in execution sequence** with `add_trade(trade)`.
   Evaluation sorts by `trade_date` with a stable sort, so insertion order is
   what breaks ties between same-day executions: a same-day buy added *before* a
   same-day sell is available for that sell to consume FIFO, one added *after* it
   is not. Duplicate `trade_id`s are rejected rather than merged — replacement
   capacity and basis adjustments are keyed by trade id.
3. **Run `evaluate_wash_sales_for_symbol(symbol)`.** The pass is chronological
   and single-phase, because the order matters: a disallowed loss raises the
   replacement lot's basis, and if those shares are sold later in the same ledger
   that raised basis is the basis of that later sale. Do not compute realized P&L
   from unadjusted basis and add disallowances back afterwards — that reports the
   deferral twice.
4. **Handle an unmatched sell as a data problem, not a rounding problem.** A sell
   that exceeds the open long quantity raises `WashSaleError`. Either the ledger
   is missing a buy, or the position is short and § 1091(e) governs it. Do not
   suppress the exception: both readings understate Box 1g.
5. **Read the result as Form 1099-B lines.** `total_proceeds_usd` is Box 1d,
   `total_cost_basis_usd` is Box 1e (post-§ 1091(d) adjustment),
   `total_disallowed_wash_loss_usd` is Box 1g, and `net_allowed_taxable_pnl_usd`
   is `1d − 1e + 1g`. Each `WashSaleMatch` is the audit trail for one loss slice:
   which disposition, which replacement acquisition, how many shares, and the
   § 1091(d) basis of exactly those shares.
6. **Carry `deferred_loss_in_open_lots_usd` into next year.** It is the part of
   Box 1g still sitting in the basis of shares that are still held. If the
   position is fully closed and not repurchased within 30 days, this is zero and
   the whole year's economic loss has been recognized.
7. **Reconcile against the broker's 1099-B and investigate every difference.**
   A difference is informative: the broker sees only that account and that CUSIP,
   while the taxpayer's § 1091 exposure is wider.

## Common Pitfalls

- **Adding the disallowed loss back to unadjusted P&L.** The single most common
  implementation bug. If a replacement lot is sold later in the same year, its
  basis already contains the disallowed loss; adding Box 1g on top of a P&L
  computed from purchase price counts the deferral twice and can flip the sign of
  the year's result.
- **Treating shares you sold as their own replacement.** Liquidating a position
  built from several lots in one order is not a wash sale — nothing is held
  afterwards. An engine that lets each lot in the sale act as replacement for the
  others reports a large phantom disallowance on a completely closed position.
  For the same reason, selling part of a *single* acquisition and keeping the
  rest is not a wash sale: the retained shares were not bought to replace the
  ones sold. Two *separate* purchases are a different matter — the IRS position
  is that the second one is replacement stock.
- **Forgetting the window runs backwards too.** § 1091(a) covers the 30 days
  *before* the sale. Buying a second lot and then selling the first at a loss a
  week later is a wash sale, and it surprises people who think of the rule as a
  "don't buy it back" rule.
- **Reading the broker's Box 1g as the taxpayer's answer.** Brokers report only
  same-account, same-CUSIP wash sales (Treas. Reg. § 1.6045-1(d)(6)(iii)). Losses
  washed by a purchase in a spouse's account, another broker, or an IRA never
  appear on any 1099-B and are the taxpayer's responsibility.
- **The IRA trap is permanent, not a deferral.** Under Rev. Rul. 2008-5 a
  replacement purchase in the taxpayer's IRA or Roth IRA disallows the loss and
  **does not increase basis** in the IRA. There is nothing to recover later.
- **Year-end harvesting that reaches into January.** Selling at a loss in
  December and repurchasing within 30 days pushes the deduction into a later
  year. Buying on December 31 and selling at a loss on January 2 does it too.
- **Holding period tacking is not P&L.** § 1223(3) adds the loss lot's holding
  period to the replacement shares, which can convert a short-term position into
  a long-term one. This engine tracks basis, not holding period — classify STCG
  and LTCG separately (`fifo-vs-specific-lot-tax-accounting-methods`).
- **Ambiguity the statute does not resolve.** Where an acquisition made *before*
  the shares that were sold is treated as replacement for them, the IRS has
  applied the rule and commentators dispute it. This engine takes the
  conservative reading and disallows. See `references/standards.md`.

## Verification

```bash
python -m unittest discover -s skills/wash-sale-rule-tracking-us/scripts
```

The suite covers the ±30/±31 day window boundary in both directions, the
§ 1091(d) basis carry-forward into a later disposition, complete liquidation of a
multi-lot position (no wash sale), same-acquisition and separate-acquisition
retained shares, the Treas. Reg. § 1.1091-1(e) one-replacement-per-loss rule,
partial adjustment of an over-sized replacement lot, deferred loss reporting for
a position open at year end, unmatched sells, duplicate trade ids, and the
Box 1d/1e/1g identity. Sign off with `assets/checklist.md`.

## Related Skills

- `fifo-vs-specific-lot-tax-accounting-methods`
- `1099-b-and-broker-tax-reporting-reconciliation`
- `automated-tax-lot-reporting-pipeline`
- `mark-to-market-election-for-active-traders-us`
- `constructive-sale-rule-considerations-us`
- `cross-strategy-tax-lot-optimization`
- `crypto-transaction-tax-lot-tracking`
- `record-keeping-requirements-for-tax-audit-defense`

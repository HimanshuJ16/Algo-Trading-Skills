---
name: broker-margin-interest-accrual-tracking
description: Use when accounting for the financing cost of leveraged or short positions —
  margin loan interest on tiered blended rate schedules, short borrow fees on collateral
  value, 360 vs 365 day-count conventions, calendar-day accrual across weekends and
  holidays — and deducting that cost from gross P&L to get a net figure a backtest can be
  trusted on.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- margin-interest
- accrual-tracking
- borrowing-cost
- pnl-accounting
- leverage-cost
- institutional
brokers_frameworks:
- Margin Cost Tracker
- Python PnL Accounting
- Interactive Brokers
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when computing net P&L for a strategy that borrows — one that runs
leverage, carries positions overnight, or sells short. Backtests that ignore financing
overstate returns and Sharpe by the entire cost of the leverage that produced them, and
the overstatement scales with the benchmark rate: a book carrying a permanent debit at a
5% margin rate gives up 5% a year before it makes a single trading decision.

Use it to answer three questions with numbers rather than estimates: what does the debit
balance cost under the broker's **tiered** schedule, what does the borrow cost on the
short book, and what does gross P&L become once both are subtracted.

## When NOT to Use

- **As a substitute for the broker's own interest statement.** This computes an accrual
  from balances you supply. Reconcile it against the monthly posting before trusting it;
  a systematic gap usually means the collateral basis or the day-count is wrong.
- **To model multi-year compounding.** Brokers accrue daily and post monthly (IBKR on
  the third business day of the following month), so interest starts earning interest
  only once posted. This module sums simple daily charges over the window. Negligible
  over weeks, material over years at high rates.
- **To get the net cost of a short.** It reports the **gross** borrow fee. The economic
  cost is the fee less the rebate earned on short sale proceeds, which this does not
  model — so its short-side number is an upper bound, and for general-collateral names a
  loose one.
- **For intraday leverage.** Only end-of-day balances accrue overnight financing. A
  position opened and closed inside the session contributes nothing here.
- **As a live rate source.** Broker tiers are quoted as a spread over a benchmark and
  reprice with monetary policy. The bundled defaults are a dated illustration.

## Prerequisites

- Your broker's **current** margin schedule. IBKR publishes tiers as benchmark + spread
  (Fed Funds for USD), so absolute APRs drift — pull today's table, or build one with
  `tiers_from_benchmark(benchmark_apr, spreads)`.
- The correct day-count for the financing currency: 360 for USD and most currencies at
  IBKR, 365 for exceptions such as GBP.
- Per-security short borrow rates, which are re-struck daily and can move hundreds of
  basis points overnight.
- End-of-day debit balance and gross short market value per date — not an average.
- The exchange holiday calendar, if you want the ledger's accrual blocks to line up with
  settlement days.

## Workflow

1. **Configure the schedule, and let it reject bad input.** Build tiers with
   `tiers_from_benchmark` or explicit `MarginRateTier` values. The constructor validates
   that brackets start at zero, are contiguous, and that the **top tier is open-ended**.
   That last check is not pedantry: a schedule capped at $100k silently prices everything
   above the cap at 0%, so a $200k balance reports 2.5% instead of 5% — half the true
   cost, with no error.

2. **Feed dates, not day counts.** Call `accrue_daily_balances(balances, through_date)`
   with one `EodBalance` per observation. It derives the day count from the dates, so it
   cannot be handed a trading-day count by mistake. Use the scalar
   `calculate_interest_accrual(start_date, holding_days, ...)` only for a constant
   balance, and read `holding_days` as **calendar** days.

3. **Know why weekends cost three days.** Not because settlement is slow — because the
   balance still exists on Saturday and Sunday and interest is computed on the daily
   balance. The consequence is the part people get wrong in both directions: the total
   depends *only* on the number of calendar days in the window. Batching Sat/Sun into
   Friday's ledger row changes granularity, never the total. Applying a weekend
   multiplier on top of a calendar-day count double-charges; feeding a trading-day count
   under-charges by roughly 2/7.

4. **Register holidays for ledger alignment, not for extra cost.** `add_holidays()` makes
   the Friday before a holiday Monday carry one four-day block rather than a three-day
   block plus a row dated on a day the market was shut. The total is identical either
   way; what changes is whether the ledger reconciles line-by-line against the broker's.

5. **Charge the borrow fee on collateral, not market value.** IBKR computes it as
   `value x rate / 360` where value is **102% of the prior day's settlement price,
   rounded up to the next whole dollar**, times shares. Set `short_collateral_markup=1.02`
   to approximate it, or pass an exactly computed `EodBalance.short_collateral_usd` to
   match a statement. The default of 1.0 charges on raw market value and understates.

6. **Subtract both from gross P&L.** `adjusted_net_pnl_usd` is gross less margin interest
   less borrow fees. Report it alongside gross, never instead of it — the gap between the
   two is the number that tells you whether the leverage was worth carrying.

> Full procedure: see `references/workflows.md`.
> Sourced day-count, collateral and settlement conventions: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting trading days.** A position held from Monday's close to the following
  Monday's close costs seven days of financing, not five. Over a year of continuous
  carry this understates the cost by roughly 30% — about 2/7 for a weekday count, more
  once holidays are dropped as well.
- **Double-counting the weekend.** The mirror-image error: taking a correct calendar-day
  count and then multiplying Fridays by three. Weekend batching is presentation.
- **Assuming the lowest tier's rate applies to the whole balance.** Blended schedules
  price each slice in its own bracket, so the effective rate falls as the balance grows —
  and a flat-rate assumption misprices in whichever direction the schedule bends.
- **A tier schedule with a capped top bracket.** Anything above the cap is priced at
  zero, and the shortfall grows with the size of the loan. This now raises
  `RateScheduleError` rather than returning a plausible-looking number.
- **Using 365 where the broker uses 360.** It understates the charge by ~1.4% — small per
  day, systematic forever, and it will never reconcile against a statement.
- **Charging borrow on market value.** Collateral is marked at 102% and rounded up, so
  the fee is at least 2% higher than a market-value calculation suggests.
- **Netting a credit cash balance against borrow fees.** A credit balance earns interest
  under a separate, tiered, threshold-gated schedule this module does not model; treating
  it as negative margin interest invents income.
- **Letting NaN through.** A single NaN balance turns net P&L into NaN silently, and a
  financing cost that defaults to zero is indistinguishable from an unlevered strategy —
  it flatters every metric derived from it. Unusable input raises `FinancingDataError`.
- **Hard-coding last year's APRs.** Rates are benchmark-linked; a schedule copied from a
  2023 screenshot was roughly 170bp too high by 2026.
- **Conflating borrow fees with margin interest.** Shorting incurs a borrow fee on the
  short market value *and*, if the account's cash goes into deficit, margin interest on
  that deficit. They are separate charges with separate bases.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-margin-interest-accrual-tracking/scripts`
- Accrue a constant $100k debit at 5% from a Monday for 7 calendar days and confirm
  $97.22 (`100000 * 0.05 / 360 * 7`), with the Friday row carrying `days_accrued == 3`.
- Run the same balance for 14 days starting on a Monday and on a Friday and confirm the
  totals are identical — if they differ, weekend handling is double-counting.
- Register a holiday Monday and confirm the preceding Friday yields a single four-day
  block and no accrual row is dated on the holiday.
- Construct a tracker whose top tier is finite and confirm `RateScheduleError`; pass NaN
  as a balance and confirm `FinancingDataError`. A number coming back from either is a
  fail-open bug.
- Feed a two-day schedule of $50k then $200k and confirm the effective APR is recomputed
  per day (5% then 4.5%) rather than averaged.
- Reconcile a month of output against the broker's posted interest before relying on it.

## Related Skills

- `short-selling-borrow-cost-and-availability-modeling`
- `broker-account-margin-call-handling`
- `multi-currency-pnl-and-fx-conversion`
- `backtesting-ml-models-against-transaction-costs`
- `execution-realistic-simulation`

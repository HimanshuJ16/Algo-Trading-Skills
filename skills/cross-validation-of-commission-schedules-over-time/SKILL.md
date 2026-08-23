---
name: cross-validation-of-commission-schedules-over-time
description: Use when backtesting multi-year historical strategies to model historical
  changes in broker commission schedules over time rather than applying modern zero-commission
  or current fee structures retroactively.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- commission-schedule
- historical-fees
- transaction-costs
- broker-rates
brokers_frameworks:
- Historical Commission Modeler
- Python
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when running historical backtests spanning multiple years (e.g. 2016–2024) on US cash equities. Retail commission rates moved repeatedly over short windows — one major US broker's standard online equity rate went $8.95 → $6.95 → $4.95 → $0.00 between February 2017 and October 2019 — so a single flat commission figure is wrong for most of a multi-year sample. Applying today's zero-commission structure retroactively inflates backtested P&L for any strategy that trades frequently. This skill models date-effective fee schedules, adds the US regulatory pass-through fees that survived the move to zero commission, and quantifies the P&L difference against the naive assumption.

## When NOT to Use

- **Non-US venues.** Stamp duty, STT, and exchange transaction levies are not modelled.
- **Volume-tiered schedules** where the rate depends on trailing volume — use `exchange-fee-tier-and-rebate-structure-analysis`.
- **Maker/taker rebate economics** — use `post-only-and-maker-taker-fee-optimization`.
- **Slippage, spread, and market impact.** This skill models explicit fees only; implicit costs belong to `transaction-cost-analysis-tca-integration`.

## Prerequisites

- Trade execution log with timestamps, symbol, positive share quantity, price, and side (BUY/SELL).
- Your broker's **published historical** commission schedule with effective dates. The shipped `DEFAULT_SCHWAB_RETAIL_SCHEDULE` is a worked reference example, not a substitute.
- For US regulatory fees: SEC Section 31 rates and FINRA TAF rates covering the backtest window (`references/standards.md` lists the primary sources).

## Workflow

1. **Construct the Time-Varying Fee Schedule**: One `CommissionTier` per period during which the published rate did not change, with inclusive effective dates. Match the broker's actual *structure* — flat ticket, per-share with floor/cap, or percent-of-value. Do not blend a ticket fee with a per-share fee unless the broker charges both. Overlapping tiers are rejected at construction; gaps are logged and raise at lookup.
2. **Resolve the Tier for Each Trade Date**: Parse the timestamp to a calendar date and select the covering tier. If the date is unparseable or uncovered, **fail loudly — never fall back to the latest tier**, because the latest tier is $0.00 and that silently reintroduces the exact bias being corrected. Convert timestamps to the schedule's timezone first; a UTC timestamp near a session boundary can land on the wrong calendar day.
3. **Compute the Trade Commission**: `max(raw, min_trade_fee)` then, when the broker publishes one, `min(..., trade_value * max_pct_of_value)` — the cap is applied after the floor and dominates it. Pass share quantity as a positive number with an explicit `side`; a signed quantity would silently reduce a per-share fee.
4. **Add Regulatory Pass-Through Fees**: SEC Section 31 and FINRA TAF apply to **sales only** and both changed rates repeatedly. Supply a `RegulatoryFeeTier` list; when omitted, results carry `regulatory_fees_modeled=False` so the report states the cost was excluded rather than measured as zero.
5. **Audit Fee Schedule Impact**: Price every trade under the historical schedule and under a flat `modern_baseline`, and report the delta in dollars and as a percentage of starting capital. That delta is the P&L a naive backtest would have fabricated.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Retroactive Zero-Commission**: Charging $0.00 back to 2015. US retail zero commission began in October 2019 — and on different dates per broker (TD Ameritrade Oct 3, Schwab and E\*TRADE Oct 7, 2019). Using one broker's cutover date for another mis-costs the intervening days.
- **Treating "Zero Commission" as Zero Cost**: The SEC Section 31 fee and the FINRA Trading Activity Fee still apply to every **sale** and are passed through to the seller. A backtest that charges nothing to a post-2019 exit under-costs every exit in the sample.
- **Silent Fallback on an Unresolvable Date**: An unparseable timestamp, a date before the schedule starts, or a schedule gap must raise. If the modeler quietly applies its most recent tier instead, a whole pre-2019 backtest can be priced at $0.00 and still look like it modelled commissions.
- **Modelling a Structure the Broker Never Charged**: A flat-ticket broker charges a ticket fee and no per-share fee. Adding a per-share component "for realism" invents costs; conversely, dropping a per-share broker's floor or cap mis-prices both ends of the size distribution.
- **Ignoring Minimum Ticket Charges**: Applying $0.005/share without the $1.00 per-order minimum under-costs a 10-share trade by 20×.
- **Ignoring the Percent-of-Value Cap**: 10,000 shares at $0.10 is $1,000 of notional; an uncapped $0.005/share fee charges $50 — 5% of the trade — where the broker's 1% cap charges $10.

## Verification

- Price the same trade on 2019-10-06 and 2019-10-07 against the reference schedule and confirm $4.95 vs $0.00 — an off-by-one at the cutover is the most common schedule bug.
- Submit a trade dated before the schedule's coverage and confirm it **raises** rather than returning $0.00.
- Price a 1,000-share $200 **sell** under a Section 31 + TAF schedule and confirm the total cost is non-zero even in the zero-commission era.
- Run `python -m unittest discover -s skills/cross-validation-of-commission-schedules-over-time/scripts` and confirm a 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `post-only-and-maker-taker-fee-optimization`
- `multi-year-regime-coverage-requirement`
- `exchange-fee-tier-and-rebate-structure-analysis`

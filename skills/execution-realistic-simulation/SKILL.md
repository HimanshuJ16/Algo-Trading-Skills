---
name: execution-realistic-simulation
description: Use when building a backtest's fill/execution model, to ensure simulated
  fills, slippage, and fees reflect what would actually happen in a live order rather
  than idealized instantaneous execution at a quoted price
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
brokers_frameworks: []
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a backtest reports a fill price without an explicit, justified execution model behind it. A backtest that fills every order at the exact signal-bar close or exact quoted price, with no slippage and no fees, will systematically overstate performance — often enough to make a genuinely unprofitable strategy look profitable, especially for higher-frequency strategies or less liquid instruments (many options strikes) where the gap between "ideal" and "actual" fill is largest.

The cost stack is not a rounding error. On NSE equity options at the rates effective 1 April 2026, a round trip of ₹1,00,000 premium bought and sold costs roughly ₹284 in brokerage, STT, exchange charges, stamp duty and GST — before a single paisa of spread or impact. A strategy whose edge is smaller than its cost stack does not have an edge.

## When NOT to Use

- **For a resting passive order.** This models a *marketable* order that crosses the spread. A limit order resting at the touch may earn the spread instead of paying it, but only if it fills at all — the modelling question there is queue position and adverse selection, not impact. See `queue-position-modeling-for-passive-orders` and `adverse-selection-measurement-for-passive-orders`.
- **When the order is a large fraction of daily volume.** The square-root impact law is calibrated on metaorders small relative to ADV. Above roughly 10% participation the model is extrapolating; the helper warns rather than pretending otherwise. Size the order down or model it as a schedule (`multi-day-execution-schedules-for-very-large-orders`).
- **As a substitute for measured costs once you have live fills.** Modelled slippage is a hypothesis. Once paper or live fills exist, they replace the model — see `execution-cost-model-recalibration-cadence` and `transaction-cost-analysis-tca-integration`.
- **To model latency.** This skill's helper takes a quote snapshot; it does not delay, queue, or timestamp anything. Latency is modelled by *which quote you pass in* (step 3 below).

## Prerequisites

- Realistic bid-ask spread data for the instruments being traded, or a reasonable proxy if tick-level spread data isn't available (e.g., historical average spread by instrument/strike moneyness).
- Average daily volume for each instrument, in the same units as order size, from the same historical point in time as the trade being simulated.
- The broker's actual fee schedule **plus** the statutory stack for the venue: for Indian equity/derivatives that is STT/CTT, exchange transaction charges, SEBI turnover fee, stamp duty, and GST on brokerage + exchange + SEBI charges. Statutory rates carry an effective date and change — Indian F&O STT moved twice between October 2024 and April 2026 — so record the date each rate was verified alongside the rate.
- A defined assumption for execution latency (time between signal generation and the order reaching the exchange), used to choose which quote the fill is priced against.

## Workflow

1. Model fills at a price that reflects the bid-ask spread relative to order direction — a buy order fills at (or beyond) the ask, a sell order fills at (or beyond) the bid, never at the mid-price, since mid-price fills are only achievable with resting limit orders that may not fill at all.
2. Apply impact as a function of order size relative to available liquidity, not a flat constant. Use the square-root law, $I(Q) = \gamma \sigma \sqrt{Q/V} P_{\text{mid}}$, with $\sigma$ the daily volatility and $V$ the daily volume on the same horizon. A fixed slippage assumption that's realistic for a liquid, small order will understate real slippage for a large order or an illiquid options strike, and vice versa. Treat $\gamma$ as a parameter to be fitted, not a constant to be trusted.
3. Model realistic execution latency by **choosing the quote the fill is priced against**: the delay between the bar/tick that generated the signal and the moment an order could actually reach the exchange (the bot's own processing time, network latency to broker, and broker-to-exchange latency). Pass the mid and half-spread from that later point in time. Passing the signal bar's own quote reintroduces look-ahead no matter how carefully the rest of the model is built (`lookahead-bias-elimination`).
4. Include the complete fee stack, not just headline brokerage. For Indian derivatives that means STT/CTT, exchange transaction charges, SEBI turnover fee, stamp duty (buy side only), and GST at 18% on brokerage + exchange + SEBI charges — GST does **not** apply to STT or stamp duty, which are themselves taxes. Omitting any component systematically inflates reported net returns, and for high-turnover strategies these can be the difference between profitable and unprofitable.
5. Attach an effective date and a source to every statutory rate rather than embedding bare numbers in the fill logic. A rate with no date silently rots: a backtest run today against 2024's STT rate understates options selling costs by more than half.
6. For options strategies, verify the backtest can only fill at strikes/expiries that actually existed and were liquid at that historical moment — using a theoretical Black-Scholes price for a strike that had negligible open interest or wasn't listed at that time is not a realistic fill.
7. Simulate partial fills for larger orders relative to typical depth at that price level, rather than assuming full-size instant fills — a strategy that only backtests correctly at full-size fills may behave differently live if it regularly receives partial fills requiring follow-up logic. Charge impact and fees on the quantity that actually traded, and charge no per-order brokerage on an order that never traded at all.
8. Fail loudly on impossible market state. A zero or missing ADV, a half-spread wider than the mid, a NaN volatility, or an unrecognised side string must raise, not be silently substituted with a placeholder — a repaired input produces a plausible equity curve built on a quote that never existed.
9. Cross-check the execution model's assumptions against real historical fills once the strategy goes to paper or live trading — treat modeled slippage/latency as hypotheses to validate, not fixed truths (feed this back into `paper-to-live-promotion-checklist`).

> Full step-by-step procedure with rate provenance: see `references/workflows.md`.
> Venue/regulatory coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Filling every simulated order at the exact bar close with zero slippage — the single most common source of an unrealistically profitable backtest.
- Using a flat slippage constant, or an impact term linear in participation, regardless of order size or instrument liquidity. Linear impact and the square-root law disagree in opposite directions at each end of the size range: linear understates the cost of a small order and overstates a large one.
- Hard-coding statutory rates with no effective date. NSE options STT was 0.0625% before October 2024, 0.10% from 1 October 2024, and 0.15% from 1 April 2026 — a backtest still using the oldest figure understates the largest single cost component of an options-selling strategy by a factor of 2.4.
- Applying GST to the whole cost stack. GST is charged on brokerage, exchange transaction charges and SEBI turnover fees; STT and stamp duty are taxes and are not part of the GST base. Stamp duty is charged on the buy side only.
- Assuming an option's STT on exercise is a bigger *rate* than on sale. Since 1 April 2026 both are 0.15%, but the bases differ: sale is charged on premium, exercise on the intrinsic value of the contract. A model that charges 0.15% of premium on an exercised long call is not modelling that charge at all.
- Letting an unhandled instrument type fall through to another market's fee branch — a futures backtest silently priced with a crypto exchange's 0.1% taker fee looks fine and is wrong on every trade.
- Substituting a placeholder for an unknown ADV. Flooring `adv` at 1.0 when volume data is missing turns a small order into a 100%-participation impact estimate — a several-percent price move applied silently to every affected fill.
- Assuming the signal-bar's own price is achievable, ignoring any processing/network/exchange latency between signal and order arrival.
- Backtesting options strategies against theoretical prices for strikes that weren't actually liquid or listed at that historical moment.

## Verification

- Compare backtest-reported fill prices against actual historical bid-ask spread data for a sample of trades and confirm fills are biased toward the correct side of the spread (buys at or beyond the ask, sells at or beyond the bid), not at mid.
- Confirm the impact term actually scales as a square root: quadrupling order size must exactly double modelled impact. A model where it quadruples is linear; one where it is unchanged is a flat constant.
- Confirm the fee calculation, applied to a known sample trade, matches a hand calculation using the venue's published rates — component by component, not just the total, since two errors in opposite directions cancel in a total.
- Confirm every statutory rate in the model carries an effective date, and that the date is not older than the most recent rate change for that venue.
- Confirm that invalid inputs (unknown side string, zero ADV, NaN volatility, half-spread wider than the mid) raise rather than producing a fill.
- After a period of live/paper trading, compare actual realized slippage and fill prices against the backtest's modeled assumptions; a large systematic gap means $\gamma$ needs recalibration before further backtest results are trusted.
- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/execution-realistic-simulation/scripts`.

## Related Skills

- `lookahead-bias-elimination`
- `paper-to-live-promotion-checklist`
- `execution-cost-model-recalibration-cadence`
- `transaction-cost-analysis-tca-integration`
- `order-book-depth-processing-l2-l3`
- `queue-position-modeling-for-passive-orders`
- `backtesting-ml-models-against-transaction-costs`

---
name: execution-realistic-simulation
description: >-
  Use when building a backtest's fill/execution model, to ensure simulated fills, slippage, and fees reflect what would actually happen in a live order rather than idealized instantaneous execution at a quoted price
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology"]
brokers_frameworks: []
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a backtest reports a fill price without an explicit, justified execution model behind it. A backtest that fills every order at the exact signal-bar close or exact quoted price, with no slippage and no fees, will systematically overstate performance — often enough to make a genuinely unprofitable strategy look profitable, especially for higher-frequency strategies or less liquid instruments (many options strikes) where the gap between "ideal" and "actual" fill is largest.

## Prerequisites

- Realistic bid-ask spread data for the instruments being traded, or a reasonable proxy if tick-level spread data isn't available (e.g., historical average spread by instrument/strike moneyness)
- The broker's actual fee schedule (brokerage, exchange transaction charges, STT, GST, stamp duty for Indian equity/options specifically, or the equivalent fee stack for the relevant market)
- A defined assumption for execution latency (time between signal generation and order reaching the exchange)

## Workflow

1. Model fills at a price that reflects the bid-ask spread relative to order direction — a buy order fills at (or near) the ask, a sell order fills at (or near) the bid, never at the mid-price, since mid-price fills are only achievable with resting limit orders that may not fill at all.
2. Apply slippage as a function of order size relative to available liquidity/depth at that price level, not a flat constant — a fixed small slippage assumption that's realistic for a liquid, small order will understate real slippage for a large order or an illiquid options strike, and vice versa.
3. Model realistic execution latency: the delay between the bar/tick that generated the signal and the moment an order could actually reach the exchange (accounting for the bot's own processing time, network latency to broker, and broker-to-exchange latency) — fill against price data from that later point in time, not the signal-bar's own price.
4. Include the complete fee stack, not just headline brokerage — for Indian derivatives specifically this means STT (higher on options exercise than on sell), exchange transaction charges, SEBI charges, stamp duty, and GST on brokerage+charges; omitting any of these systematically inflates reported net returns, and for high-frequency/high-turnover strategies these can be the difference between profitable and unprofitable.
5. For options strategies, verify the backtest can only fill at strikes/expiries that actually existed and were liquid at that historical moment — using a theoretical Black-Scholes price for a strike that had negligible open interest or wasn't listed at that time is not a realistic fill.
6. Simulate partial fills for larger orders relative to typical volume at that price level, rather than assuming full-size instant fills — a strategy that only backtests correctly at full-size fills may behave differently live if it regularly receives partial fills requiring follow-up logic.
7. Cross-check the execution model's assumptions against real historical fills once the strategy goes to paper or live trading — treat modeled slippage/latency as hypotheses to validate, not fixed truths (feed this back into `paper-to-live-promotion-checklist`).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Filling every simulated order at the exact bar close with zero slippage — the single most common source of an unrealistically profitable backtest.
- Using a flat slippage constant regardless of order size or instrument liquidity.
- Omitting regulatory/statutory charges (STT, stamp duty, GST) and reporting only brokerage as "the fee," which understates costs substantially for high-turnover intraday/options strategies.
- Assuming the signal-bar's own price is achievable, ignoring any processing/network/exchange latency between signal and order arrival.
- Backtesting options strategies against theoretical prices for strikes that weren't actually liquid or listed at that historical moment.

## Verification

- Compare backtest-reported fill prices against actual historical bid-ask spread data for a sample of trades and confirm fills are biased toward the correct side of the spread (buys near ask, sells near bid), not at mid.
- Confirm the fee calculation in the backtest, applied to a known sample trade, matches a manual calculation using the broker's actual published fee schedule including all statutory charges.
- After a period of live/paper trading, compare actual realized slippage and fill prices against the backtest's modeled assumptions; a large systematic gap means the execution model needs recalibration before further backtest results are trusted.

## Related Skills

- `lookahead-bias-elimination`
- `paper-to-live-promotion-checklist`

---
name: perpetual-futures-funding-rate-handling
description: >-
  Perpetual futures funding rate handling engine calculating periodic funding payments, annualized funding APR drag, and auditing adverse funding drag limits for crypto perpetual swaps.
domain: Crypto Derivatives & Perpetual Swaps
subdomain: Funding Rate Mechanics & Carry Yield
tags: ["perpetual-futures", "funding-rate", "crypto-derivatives", "binance-futures", "okx-perpetuals", "carry-trade", "funding-arbitrage"]
brokers_frameworks: ["Binance / OKX Perpetual Swap API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when holding directional or cash-and-carry positions in crypto perpetual futures (e.g. BTCUSDT, ETHUSDT). Unlike traditional futures contracts that converge at expiration, perpetual swaps settle periodic funding payments (typically every 8 hours at 00:00, 08:00, 16:00 UTC) between long and short traders to keep the contract mark price tethered to the underlying spot index. This engine calculates funding payments, annualized drag APR ($F \cdot \frac{365 \cdot 24}{\text{Interval}} \cdot 100\%$), and audits adverse funding fee drag.

## Prerequisites

- Perpetual position details (`symbol`, `position_qty`, `side`, `entry_price`, `mark_price`).
- Funding rate update (`funding_rate`, `next_funding_timestamp_utc`, `funding_interval_hours`: default 8).

## Workflow

1. **Notional Position Value & Funding Payment Calculation**:
   - Position Notional Value: $V_{\text{notional}} = |Q| \times P_{\text{mark}}$.
   - Signed Funding Payment:
     $$\text{Payment}_{\text{LONG}} = V_{\text{notional}} \cdot F$$
     $$\text{Payment}_{\text{SHORT}} = -V_{\text{notional}} \cdot F$$
     *(Positive payment = Outflow/Fee paid; Negative payment = Inflow/Fee received)*
2. **Annualized Carry Yield / Drag APR**:
   - Compute annualized funding rate APR:
     $$\text{APR} = F \cdot \left( \frac{365 \cdot 24}{\text{IntervalHours}} \right) \cdot 100\%$$
3. **Adverse Funding Drag Audit**:
   - Audit if adverse funding fee drag exceeds policy limit ($\text{AdverseAPR} > \text{MaxAdverseAPR}$).
4. **Audit Report Generation**: Output structured `FundingRateReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Funding Timestamps**: Closing positions 1 minute after funding timestamp ($00:00:01$ UTC), incurring full 8-hour funding fee drag.
- **Wrong Sign Convention**: Treating positive funding rate as fee received by longs instead of longs paying shorts.
- **Omitting Funding Costs in Backtests**: Backtesting perpetual swap strategies assuming zero holding cost, overstating long-term profitability.

## Verification

- Instantiate `PerpetualFuturesFundingRateHandlingEngine`. Input 10 BTC Long position @ $\$50,000$ mark price with $+0.01\%$ 8-hour funding rate $\implies$ verify $\$50.00$ funding fee outflow per 8-hour period ($\approx 10.95\%$ APR drag). Input negative funding rate ($-0.02\%$) for Long position $\implies$ verify $\$100.00$ funding income inflow.
- Run `python scripts/test_perpetual_futures_funding_rate_handling.py`.

## Related Skills

- `kraken-websocket-v2-auth-and-subscriptions`
- `okx-unified-account-api`
---

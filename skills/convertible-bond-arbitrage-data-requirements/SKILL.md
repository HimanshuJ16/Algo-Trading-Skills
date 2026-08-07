---
name: convertible-bond-arbitrage-data-requirements
description: Quantitative fixed-income and equity derivatives module for evaluating
  Convertible Bond (CB) Arbitrage data requirements, parity, conversion premium, delta
  hedge ratios, and credit spread inputs.
domain: Derivatives & Fixed Income
subdomain: Convertible Securities
tags:
- convertible-bond
- arbitrage
- delta-hedging
- parity
- conversion-premium
- credit-spread
- borrow-rate
brokers_frameworks:
- NumPy
- Generic Fixed Income
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing, backtesting, or executing Convertible Bond (CB) Arbitrage strategies. CB Arbitrage involves buying a convertible bond (hybrid security = straight bond + equity option) and shorting the underlying equity to capture cheap implied volatility, yield carry, or credit spread mispricing while maintaining delta neutrality. This module defines data ingestion contracts and calculates core arbitrage metrics: Parity, Conversion Premium, Delta Hedge Ratio, and Net Carry.

## Prerequisites

- Bond Static Terms: Par Value, Conversion Ratio, Coupon Rate, Maturity Date.
- Real-time Market Feeds: CB Clean Price, Accrued Interest, Stock Spot Price, Stock Borrow Fee, Credit Default Swap (CDS) / Credit Spread.

## Workflow

1. **Data Completeness Verification**:
   - Audit required data inputs: `stock_price`, `cb_price`, `conversion_ratio`, `borrow_fee_bps`, `credit_spread_bps`.
2. **Parity & Conversion Premium Calculation**:
   - $\text{Parity} = \text{Conversion Ratio} \times \text{Stock Price}$.
   - $\text{Conversion Premium (\%)} = \frac{\text{CB Price} - \text{Parity}}{\text{Parity}} \times 100\%$.
3. **Delta & Short Equity Sizing**:
   - Calculate option delta $\Delta \in (0, 1)$.
   - $\text{Short Stock Shares} = \text{CB Quantity} \times \text{Conversion Ratio} \times \Delta$.
4. **Arbitrage Valuation**:
   - Evaluate implied volatility vs. realized/historical volatility ($IV < HV \implies$ cheap option).
   - Evaluate net carry yield: $\text{Coupon Yield} - \text{Financing Rate} - \text{Stock Borrow Fee}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Stock Borrow Fee**: Shorting a hard-to-borrow stock with a 15% borrow fee can instantly wipe out all coupon carry and volatility arbitrage profits.
- **Static Delta Hedging**: Failing to dynamically rebalance the short equity position as stock price moves (gamma effect), leaving the portfolio exposed to directional equity risk.
- **Omitting Credit Risk**: Treating the convertible bond as risk-free debt without monitoring issuer credit spread widening / default probability.

## Verification

- Instantiate `ConvertibleBondArbitrageEngine`. Input CB with Par $1,000, Conversion Ratio 20, Stock Price $45. Verify Parity = $900. If CB Market Price = $990, verify Conversion Premium = 10.0%. For Delta = 0.60 and 100 CB contracts, verify optimal short stock quantity = $100 \times 20 \times 0.60 = 1,200$ shares.
- Run `python scripts/test_cb_arbitrage.py`.

## Related Skills

- `cross-asset-hedge-execution-synchronization`
- `options-implied-volatility-surface-construction`
---

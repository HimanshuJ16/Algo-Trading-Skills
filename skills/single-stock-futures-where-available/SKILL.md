---
name: single-stock-futures-where-available
description: >-
  Production-grade Single Stock Futures (SSF) valuation, cash-and-carry arbitrage detection, ex-dividend base price adjustment, and margin efficiency engine for global derivatives exchanges (Eurex, NSE India, Euronext).
domain: Derivatives & Arbitrage Trading
subdomain: Single Stock Futures & Cash-and-Carry
tags: ["single-stock-futures", "ssf", "cash-and-carry", "eurex", "nse-india", "dividend-adjustment", "margin-efficiency"]
brokers_frameworks: ["Eurex / NSE Derivatives Rules", "Cost of Carry Model", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when trading Single Stock Futures (SSFs) on global derivatives exchanges (Eurex, National Stock Exchange of India NSE, Euronext). SSFs allow quantitative traders to take leverage on individual equities, execute market-neutral pairs trading without short-borrow availability constraints, and capture cash-and-carry arbitrage opportunities. This engine calculates theoretical SSF fair value using cost-of-carry models with discrete dividend adjustments, detects over/undervaluation signals, and models margin leverage efficiency.

## Prerequisites

- Contract specification (`SSFContractSpec`: `symbol`, `underlying_spot_symbol`, `exchange`, `lot_size`, `days_to_expiry`, `settlement_type`, `risk_free_rate_annual`, `short_borrow_rate_annual`).
- Discrete dividend schedule (`DividendEvent`: `ex_date_days`, `amount_per_share`).

## Workflow

1. **Theoretical Fair Value Calculation**:
   - Compute present value of dividends: $\text{PV}(D) = \sum D_i e^{-r t_i}$.
   - Calculate theoretical fair value: $F_{\text{fair}} = (S - \text{PV}(D)) \cdot e^{(r - q) T}$.
2. **Arbitrage Signal Detection**:
   - Compare market price $F_{\text{market}}$ vs $F_{\text{fair}}$:
     - $F_{\text{market}} \ge F_{\text{fair}} + \text{threshold} \implies$ `CASH_AND_CARRY` (Buy Spot, Sell SSF).
     - $F_{\text{market}} \le F_{\text{fair}} - \text{threshold} \implies$ `REVERSE_CASH_AND_CARRY` (Sell Spot, Buy SSF).
3. **Ex-Dividend Price Adjustment**:
   - Calculate adjusted base price on ex-dividend date: $P_{\text{adj}} = P_{\text{prev}} - D$.
4. **Margin Leverage Quantification**:
   - Calculate capital leverage multiplier ($\text{Spot Margin} / \text{SSF Margin}$, e.g. 3.33x).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Dividend Drop Adjustments**: Failing to adjust SSF fair value for upcoming ex-dividend dates, resulting in false cash-and-carry mispricing signals.
- **Assuming Physical Delivery on Cash-Settled SSFs**: Misinterpreting exchange settlement rules (e.g. NSE India SSFs are cash-settled while Eurex SSFs can be physical or cash).
- **Unmodeled Short Borrow Drag in Reverse Cash-and-Carry**: Failing to subtract short-borrow interest costs when shorting spot equity in reverse cash-and-carry trades.

## Verification

- Instantiate `SingleStockFuturesEngine`. Compute fair value for Reliance SSF ($S = 2500$, $F_{\text{market}} = 2530$) $\implies$ verify $F_{\text{fair}} \approx 2511.31$, `CASH_AND_CARRY` signal, and 3.33x leverage multiplier. Add dividend of $20 in 15 days $\implies$ verify fair value decreases. Calculate ex-dividend adjustment ($2500 - 20$) $\implies$ verify 2480 adjusted base price.
- Run `python scripts/test_ssf_handling.py`.

## Related Skills

- `short-selling-borrow-cost-and-availability-modeling`
- `corporate-action-event-calendar-integration`
---

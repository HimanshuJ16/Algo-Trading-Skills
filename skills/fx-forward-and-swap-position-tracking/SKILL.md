---
name: fx-forward-and-swap-position-tracking
description: >-
  Quantitative treasury engine for pricing FX outright forwards and FX swaps via Covered Interest Rate Parity (CIRP), calculating swap points, and tracking Mark-to-Market (MtM) PnL.
domain: Global Market Integration & FX
subdomain: FX Forwards, Swaps & Treasury Risk
tags: ["fx-forward", "fx-swap", "covered-interest-parity", "swap-points", "mark-to-market", "fx-exposure", "treasury-risk"]
brokers_frameworks: ["Covered Interest Parity (CIRP)", "IFRS 9 / US GAAP", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional FX desks, multi-currency treasury platforms, and cross-border portfolio hedging systems. An FX Outright Forward locks in an exchange rate for a future date, while an FX Swap combines a near-leg (spot) with a far-leg (forward) to roll hedges. Pricing relies on **Covered Interest Rate Parity (CIRP)**: $F = S \times \frac{1 + r_q \cdot t}{1 + r_b \cdot t}$. This module tracks open FX forward/swap positions, calculates swap points, and audits daily Mark-to-Market (MtM) valuations.

## Prerequisites

- Current spot exchange rate ($S$), base currency interest rate ($r_b$), quote currency interest rate ($r_q$).
- Contract parameters (`pair`, `notional`, `contract_forward_rate`, `days_to_maturity`, `position_side`).

## Workflow

1. **Covered Interest Parity (CIRP) Pricing**:
   - Calculate theoretical forward rate: $F_{\text{fair}} = S \times \frac{1 + r_q \cdot (T/360)}{1 + r_b \cdot (T/360)}$.
   - Compute swap points: $\text{Swap Points} = (F_{\text{fair}} - S) \times 10,000$.
2. **Mark-to-Market (MtM) Valuation**:
   - Compute remaining market forward rate $F_{\text{market}}$.
   - Long MtM PnL = $\text{Notional} \times (F_{\text{market}} - F_{\text{contract}})$.
   - Short MtM PnL = $\text{Notional} \times (F_{\text{contract}} - F_{\text{market}})$.
3. **Net Currency Exposure Aggregation**:
   - Aggregate net base and quote currency commitments across maturity buckets (1M, 3M, 6M, 1Y).
4. **Audit Report Generation**: Output structured `FxForwardPositionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Day-Count Conventions**: Using 365-day conventions for Money Market currencies (e.g. USD, EUR use 360-day Count, GBP uses 365-day Count).
- **Failing to Discount MtM PnL**: Reporting future cash flow differentials without discounting to present value using the domestic risk-free rate curve.
- **Misidentifying Swap vs Outright Exposure**: Treating FX Swaps as pure directional FX bets rather than interest rate differential rolls.

## Verification

- Instantiate `FxForwardSwapTrackingEngine`. Input EUR/USD forward ($S=1.1000$, $r_{\text{USD}}=5.0\%$, $r_{\text{EUR}}=3.0\%$, $T=90$ days, Notional = €1,000,000, Contract Rate = 1.1050). Verify engine computes $F_{\text{fair}} = 1.1055$ (55 swap points), evaluates Long MtM PnL = $+\$500.00$, and tracks net currency exposure.
- Run `python scripts/test_fx_forward_and_swap_position_tracking.py`.

## Related Skills

- `multi-currency-pnl-and-fx-conversion`
- `cross-asset-hedge-execution-synchronization`
---

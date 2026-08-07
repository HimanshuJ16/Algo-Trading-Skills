---
name: commodity-futures-storage-and-carry-cost-modeling
description: Quantitative commodity pricing model for calculating theoretical futures
  prices, extracting implied convenience yields, detecting contango vs. backwardation
  regimes, and evaluating cash-and-carry arbitrage opportunities.
domain: Derivatives & Pricing
subdomain: Commodity Futures
tags:
- commodity-futures
- cost-of-carry
- convenience-yield
- contango
- backwardation
- storage-cost
brokers_frameworks:
- NumPy
- Generic Derivatives Pricing
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when pricing physical commodity futures (Crude Oil `CL`, Natural Gas `NG`, Gold `GC`, Agriculture `ZC`) or designing term-structure roll strategies. The Cost of Carry model links spot prices ($S_0$) to futures prices ($F_T$) using financing costs ($r$), physical storage/insurance costs ($c$), and implied convenience yield ($y$). High convenience yield causes **Backwardation** ($F_T < S_0$), signaling physical inventory scarcity, whereas high storage costs relative to convenience yield lead to **Contango** ($F_T > S_0$).

## Prerequisites

- Continuous spot price $S_0$ and futures contract price $F_T$ with time to maturity $T$ (in years).
- Annualized risk-free rate $r$ and continuous storage cost percentage $c$.

## Workflow

1. **Theoretical Futures Price Calculation**:
   - Continuous compounding: $F_{theoretical} = S_0 \cdot e^{(r + c - y) T}$.
2. **Implied Convenience Yield Extraction**:
   - Solve for $y$: $y = r + c - \frac{1}{T} \ln\left(\frac{F_{market}}{S_0}\right)$.
3. **Regime Identification**:
   - If $F_{market} > S_0$, classify as `CONTANGO`.
   - If $F_{market} < S_0$, classify as `BACKWARDATION`.
4. **Arbitrage Audit (Cash-and-Carry)**:
   - If $F_{market} > F_{theoretical}$ (beyond transaction costs), trigger Cash-and-Carry: Buy spot, pay storage/financing, sell futures.
   - If $F_{market} < F_{theoretical}$ (when shorting spot is possible), trigger Reverse Cash-and-Carry.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Convenience Yield ($y$)**: Assuming futures prices are purely driven by $r + c$. In tight physical markets, convenience yield surges, causing deep backwardation that pure storage models fail to explain.
- **Fixed vs. Proportional Storage Costs**: Treating storage as a fixed dollar amount per barrel/bushel without scaling properly by time to maturity $T$.
- **Day-Count Misalignment**: Miscalculating $T$ by using calendar days instead of year fractions ($T = \text{days} / 365.0$).

## Verification

- Instantiate `CommodityCarryCostModel`. Input $S_0 = 100$, $r = 0.05$, $c = 0.02$, $y = 0.01$, $T = 1.0$. Verify theoretical futures price is $100 \cdot e^{0.06} \approx 106.18$ (Contango). Set $y = 0.10$ and verify futures price drops to $100 \cdot e^{-0.03} \approx 97.04$ (Backwardation).
- Run `python scripts/test_storage_model.py`.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `calendar-spread-and-multi-leg-order-atomicity`
---

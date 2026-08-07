---
name: dividend-futures-and-forward-modeling
description: Quantitative equity forward curve engine for modeling discrete dividends,
  present/future value calculations, fair value dividend futures pricing, and cash-and-carry
  arbitrage detection.
domain: Equity Derivatives & Forward Curves
subdomain: Dividend Risk & Index Futures
tags:
- dividend-futures
- forward-modeling
- discrete-dividends
- cash-and-carry
- cost-of-carry
- eurex-fdbx
- cme-sda
brokers_frameworks:
- Eurex FDBX
- CME Dividend Futures
- Python Math / Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in equity index desk trading, forward curve construction, and index arbitrage strategies. Equities and stock indices (S&P 500, EURO STOXX 50) distribute discrete cash dividends to shareholders. The theoretical forward price $F(0, T)$ must account for the present value of expected discrete dividends $\text{PV}(D)$. Dividend futures contracts (Eurex `FDBX`, CME `SDA`) isolate dividend risk, allowing quants to trade dividend expectations independently of equity spot movements.

## Prerequisites

- Spot price $S_0$, risk-free rate $r$ (e.g. 0.05 / 5.0%), time to maturity $T$ (in years).
- Expected discrete dividend events ($D_i$: dividend amount, $t_i$: payment time in years).
- Market forward price or dividend futures market quote.

## Workflow

1. **Discrete Dividend Present & Future Value Calculation**:
   - $\text{PV}(D) = \sum_{i=1}^{n} D_i \cdot e^{-r \cdot t_i}$.
   - $\text{FV}(D) = \sum_{i=1}^{n} D_i \cdot e^{r \cdot (T - t_i)}$.
2. **Theoretical Forward Price Calculation**:
   - $F_{\text{theoretical}}(0, T) = (S_0 - \text{PV}(D)) \cdot e^{r \cdot T} = S_0 \cdot e^{r \cdot T} - \text{FV}(D)$.
3. **Dividend Future Fair Value Calculation**:
   - $F_{\text{div\_future\_fair}} = \sum_{i=1}^{n} D_i$.
4. **Cash-and-Carry Arbitrage Audit**:
   - Spread: $\Delta_{\text{arb}} = F_{\text{market}} - F_{\text{theoretical}}$.
   - If $\Delta_{\text{arb}} > \text{Threshold} \implies$ Flag `ARBITRAGE_SHORT_FORWARD_LONG_SPOT`.
   - If $\Delta_{\text{arb}} < -\text{Threshold} \implies$ Flag `ARBITRAGE_LONG_FORWARD_SHORT_SPOT`.
5. **Audit Report Generation**: Output structured `DividendForwardAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Continuous Dividend Yield for Individual Stocks**: Applying a continuous yield $q$ to single-stock options instead of discrete dividend payment schedules, mis-pricing near-term forwards.
- **Ignoring Dividend Tax Withholdings**: Failing to adjust dividend amounts $D_i$ for cross-border withholding tax rates (e.g. 15%-30%).
- **Ex-Dividend Date Mismatches**: Incorrectly including a dividend paid at $t_i > T$ after forward contract expiration.

## Verification

- Instantiate `DividendForwardModelingEngine`. Input Spot $S_0 = 100.0$, $r = 5.0\%$, $T = 1.0$ year. Add two discrete dividends: \$2.00 at $t_1 = 0.25$ years, \$2.00 at $t_2 = 0.75$ years. Calculate $\text{PV}(D) \approx \$3.90$. Verify theoretical forward price $F(0, T) \approx (100 - 3.90) \times e^{0.05} \approx \$101.03$. Submit market forward = \$103.00, verify engine flags `ARBITRAGE_SHORT_FORWARD_LONG_SPOT`.
- Run `python scripts/test_dividend_futures.py`.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `corporate-action-event-calendar-integration`
---

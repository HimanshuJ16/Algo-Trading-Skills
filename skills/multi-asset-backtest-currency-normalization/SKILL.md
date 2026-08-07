---
name: multi-asset-backtest-currency-normalization
description: Use when backtesting global multi-asset portfolios to convert multi-currency
  cash flows, position valuations, and FX conversion rates into a single unified reporting
  currency without currency mixing errors
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- multi-currency
- fx-conversion
- portfolio-accounting
- currency-normalization
brokers_frameworks:
- Interactive Brokers Multi-Currency
- Backtrader Multi-Asset
- VectorBT FX
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever backtesting or managing portfolios that hold assets denominated in multiple fiat currencies (e.g. trading US stocks in `USD`, European stocks in `EUR`, Japanese stocks in `JPY`, and Indian stocks in `INR`). Directly summing unconverted P&L across different currencies creates catastrophic accounting distortions (e.g. treating 10,000 JPY equal to 10,000 USD). Implementing point-in-time FX rate conversion, per-currency cash ledger isolation, and dynamic conversion to a unified portfolio reporting currency (e.g. `USD`) is mandatory.

## Prerequisites

- Multi-currency historical price data and FX exchange rate series (`EUR/USD`, `USD/JPY`, `USD/INR`).
- Defined portfolio base reporting currency (e.g. `USD`).
- Per-currency cash balance accounts.

## Workflow

1. **Initialize Multi-Currency Ledger**:
   - Set base reporting currency: `reporting_currency = "USD"`.
   - Maintain separate cash balances for each currency held (`cash_balances = {"USD": 50000, "EUR": 30000, "JPY": 1000000}`).

2. **Register Historical FX Rates**:
   - Store historical FX exchange rates for date $T$: $E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$.

3. **Convert Local Asset Valuations**:
   - For an asset priced in local currency $C_{\text{local}}$ at price $P_{\text{local}}$ and quantity $Q$:
     $$\text{Value}_{\text{reporting}} = Q \cdot P_{\text{local}} \cdot E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$$

4. **Calculate Total Portfolio Net Asset Value (NAV)**:
   - Sum converted cash balances and converted position valuations:
     $$\text{NAV}_{\text{reporting}} = \sum_{c} \text{Cash}_c \cdot E(c \rightarrow \text{base}) + \sum_{i} \text{Position}_i \cdot E(c_i \rightarrow \text{base})$$

5. **Currency Leakage Guard Verification**:
   - Ensure all P&L additions invoke `convert_currency()` before aggregating into total equity.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Converted P&L Addition**: Adding P&L in JPY or EUR directly to USD cash balances without FX conversion.
- **Static FX Rate Assumption**: Using a constant fixed FX conversion rate across a multi-year backtest, ignoring currency fluctuations.
- **Ignoring FX Transaction Costs**: Omitting broker FX conversion fees and bid-ask spreads when converting cash balances.

## Verification

- Submit multi-currency portfolio holding $50,000 USD, $30,000 EUR (EUR/USD = 1.10), and 1,000,000 JPY (USD/JPY = 150.0) and verify total NAV converts accurately to USD.
- Verify `convert_currency()` raises error if FX rate is missing for target date.
- Run unit test suite `python scripts/test_currency_normalizer.py` and confirm 100% pass rate.

## Related Skills

- `survivorship-bias-free-universe-construction`
- `corporate-action-adjusted-backtesting`
- `walk-forward-optimization-window-management`
---

# Deep Workflow Reference — multi-asset-backtest-currency-normalization

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Initialize Multi-Currency Ledger:**
   - Configure reporting base currency (e.g. `USD`).
   - Track separate cash balances for each currency held (`EUR`, `JPY`, `INR`).

2. **Register Point-in-Time FX Rates:**
   - Store historical FX exchange rates $E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$ for each backtest date $T$.

3. **Convert Position Valuations & Cash Balances:**
   - Convert cash balances and position valuations to reporting currency on date $T$.

4. **Calculate Total Net Asset Value (NAV):**
   - Compute aggregate NAV:
     $$\text{NAV}_{\text{reporting}} = \sum \text{Cash}_c \cdot E(c \rightarrow \text{base}) + \sum \text{Position}_i \cdot E(c_i \rightarrow \text{base})$$

## Failure Modes Observed in Production

- **Unconverted P&L Summation:** Summing local currency P&L values directly into base currency cash balances without FX conversion.
- **Fixed FX Rate Assumptions:** Using constant exchange rates over multi-year backtest windows.

## Production Implementation Reference

- Reference code: `scripts/currency_normalizer.py` (`MultiCurrencyPortfolioNormalizer`, `PositionValuation`, `MultiCurrencyNAV`).
- Automated unit tests: `scripts/test_currency_normalizer.py`.

# Deep Workflow Reference — multi-currency-pnl-and-fx-conversion

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Native-Currency Tagging & Record Isolation:**
   - Tag every position, trade, and PnL record with its native currency tag (`CurrencyAmount(amount, currency)`). Preserve native currency records for broker statement reconciliation.

2. **Point-In-Time FX Rate Resolution:**
   - Query `PointInTimeFXResolver.get_rate()` using exact event timestamps $T$, resolving direct or USD cross-rate paths. Never use single static EOD rates for historical conversions.

3. **PnL Decomposition (Native Asset Price Return vs FX Gain/Loss):**
   - Execute `calculate_decomposed_pnl()` to isolate:
     - **Native Asset Price Return PnL:** $\text{Native PnL} \times \text{FX}_{\text{entry}}$.
     - **FX Translation Gain/Loss:** $(\text{Exit Price} \times \text{Quantity}) \times (\text{FX}_{\text{exit}} - \text{FX}_{\text{entry}})$.

4. **Currency-Specific Precision Rounding:**
   - Apply per-currency decimal rounding rules via `round_amount()` (e.g. JPY 0 decimals, USD/EUR/INR 2 decimals, BTC/ETH 8 decimals).

5. **Base-Currency Aggregate Reporting:**
   - Convert amounts to base currency at reporting/risk check aggregation time via `aggregate_in_base_currency()`.

## Failure Modes Observed in Production

- **Unconverted Summation:** Adding USD and INR PnL values directly without currency conversion.
- **Entry-Time Conversion Overwrites:** Overwriting native currency trade values at entry time, preventing broker statement reconciliation.
- **Current-Rate Historical Backtesting:** Applying today's FX spot rate to convert historical trades across a 5-year backtest.
- **Conflated FX Gains:** Blending trading strategy performance with passive FX exchange rate movements.

## Production Implementation Reference

- Reference code: `scripts/fx_convert.py` (`MultiCurrencyPnLEngine`, `PointInTimeFXResolver`, `DecomposedPnL`).
- Automated unit tests: `scripts/test_fx_convert.py`.

# Deep Workflow Reference — options-margin-span-calculation-global

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **SPAN 16-Scenario Grid Evaluation:**
   - Execute `SPANMarginCalculator.evaluate_scenario_grid()` scanning spot price shocks ($\pm 6\%$) cross-evaluated against volatility shocks ($\pm 15\%$).

2. **Multi-Leg Netting & Defined-Risk Capping:**
   - Net offsetting long and short legs across options spreads (Iron Condors, Vertical Spreads).
   - Cap maximum margin requirement for defined-risk spreads at worst-case max loss rather than sum of naive individual leg margins.

3. **Naked Short Position Exposure Additions:**
   - Apply additional exposure margin ($3\%$ of underlying notional) for unhedged naked short options positions (`is_defined_risk = False`).

4. **Intraday Margin Recalculation Monitoring:**
   - Periodically re-evaluate `calculate_span_margin()` during live trading to track intraday volatility spikes affecting available account capital.

## Failure Modes Observed in Production

- **Naive Leg Summation:** Summing individual leg margins for defined-risk spreads, over-constraining available trading capital by $300\%+$.
- **Static Entry Margin Assumptions:** Assuming margin requirements remain fixed for the duration of a trade, ignoring intraday volatility spikes.
- **Unmodeled Capital Constraints in Backtests:** Assuming infinite capital availability in backtests by ignoring SPAN margin utilization bounds.
- **Cross-Broker Methodology Discrepancies:** Treating margin requirements as identical across venues with different portfolio margin rules.

## Production Implementation Reference

- Reference code: `scripts/span_approx.py` (`SPANMarginCalculator`, `OptionLeg`, `OptionType`, `SPANMarginResult`).
- Automated unit tests: `scripts/test_span_approx.py`.

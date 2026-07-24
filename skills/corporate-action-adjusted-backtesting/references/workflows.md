# Deep Workflow Reference — corporate-action-adjusted-backtesting

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Register Corporate Action Events:**
   - Store events with `ex_date`, `action_type` (`SPLIT`, `REVERSE_SPLIT`, `CASH_DIVIDEND`), `ratio`, and `cash_amount`.

2. **Backward Cumulative Factor Calculation:**
   - Process historical bars in reverse chronological order (newest to oldest).
   - Apply current cumulative factors ($F_p, F_v$) to bar $T$.
   - If bar $T$ is an ex-date:
     - For split ratio $R$: $F_p = F_p / R$; $F_v = F_v \cdot R$.
     - For dividend cash $D$: $F_d = F_d \cdot (1 - D/P_{\text{close}})$.

3. **Adjust Historical OHLCV Series:**
   - Adjusted Price: $P_{\text{adj}} = P_{\text{raw}} \cdot F_p$.
   - Adjusted Volume: $V_{\text{adj}} = V_{\text{raw}} \cdot F_v$.

4. **Account Dividend Crediting:**
   - On ex-dividend date, credit cash: $\text{Cash} = \text{Position Quantity} \cdot D$.

## Failure Modes Observed in Production

- **Double-Adjusting Vendors Data:** Applying split adjustments to data that was already pre-adjusted by data providers.
- **Un-Adjusted Volume:** Adjusting historical prices for a stock split without adjusting historical volumes proportionally.

## Production Implementation Reference

- Reference code: `scripts/corporate_action_adjuster.py` (`CorporateActionAdjuster`, `CorporateActionEvent`, `ActionType`).
- Automated unit tests: `scripts/test_corporate_action_adjuster.py`.

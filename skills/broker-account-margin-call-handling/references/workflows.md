# Deep Workflow Reference — broker-account-margin-call-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Margin Utilization Calculation:**
   - Ingest `net_liquidation_value` and `maintenance_margin`.
   - Calculate Margin Utilization Ratio: $M_{\text{ratio}} = \frac{\text{Maintenance Margin}}{\text{Net Liquidation Value}}$.

2. **Evaluate Multi-Tier Risk Gates:**
   - **NORMAL** ($M_{\text{ratio}} < 0.85$): Proceed with strategy execution.
   - **WARNING** ($0.85 \le M_{\text{ratio}} < 0.95$): Block new position entries. Issue operator alert.
   - **CRITICAL** ($0.95 \le M_{\text{ratio}} < 1.0$): Cancel all open pending orders to release reserved margin.
   - **BREACH** ($M_{\text{ratio}} \ge 1.0$): Trigger automated de-leveraging.

3. **Systematic De-Leveraging Plan Generation:**
   - Sort existing positions by highest margin requirement or volatility.
   - Calculate units to liquidate to restore margin buffer $\le 0.80$.

4. **Order Placement Interception:**
   - Intercept outbound order requests with `guard_new_order()`. Veto any order that increases margin when $M_{\text{ratio}} \ge 0.85$.

## Failure Modes Observed in Production

- **Passive Waiting for Broker Liquidation:** Allowing broker RMS to liquidate positions at market, incurring high slippage.
- **Uncanceled Pending Orders:** Leaving active limit orders in the market that consume margin during adverse price moves.

## Production Implementation Reference

- Reference code: `scripts/margin_call_engine.py` (`BrokerMarginCallEngine`, `AccountMarginSnapshot`).
- Automated unit tests: `scripts/test_margin_call_engine.py`.

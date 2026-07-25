# Deep Workflow Reference — broker-account-margin-call-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Margin Utilization Calculation:**
   - Ingest `net_liquidation_value` (NLV) and `maintenance_margin`.
   - Ensure NLV is floored at `0.01` to prevent division by zero.
   - Calculate Maintenance Margin Utilization Ratio: $M_{\text{ratio}} = \frac{\text{Maintenance Margin}}{\text{Net Liquidation Value}}$.

2. **Predictive New Order Veto Guard:**
   - Intercept outbound order requests with `guard_new_order(margin_impact)`.
   - Calculate projected margin: `projected_maint_margin = maintenance_margin + margin_impact`.
   - Calculate projected ratio. If projected ratio $\ge 0.85$, veto order.

3. **Evaluate Multi-Tier Risk Gates:**
   - **NORMAL** ($M_{\text{ratio}} < 0.85$): Proceed with strategy execution.
   - **WARNING** ($0.85 \le M_{\text{ratio}} < 0.95$): Block new position entries. Issue operator alert.
   - **CRITICAL** ($0.95 \le M_{\text{ratio}} < 1.0$): Cancel all open pending limit/stop orders to release reserved initial margin.
   - **BREACH** ($M_{\text{ratio}} \ge 1.0$): Trigger automated de-leveraging.

4. **Systematic De-Leveraging Plan Generation:**
   - Calculate `required_reduction = (maintenance_margin - target_margin) * buffer_multiplier`. Target margin is usually $75\%$ of NLV.
   - Score existing positions using a multi-factor model:
     - *Tail Risk*: Short options receive maximum priority (+1000).
     - *Margin Density*: Maintenance margin requirement divided by notional value (+100 * density).
     - *Liquidity*: High ADV assets receive higher priority, as they can be exited with minimal slippage (+ADV scaled).
   - Cap the liquidation units per position to a percentage of its Average Daily Volume (e.g. 10%) to prevent localized flash crashes.
   - Generate execution slices to send to the execution algorithms (e.g., TWAP/VWAP).

## Failure Modes Observed in Production

- **Passive Waiting for Broker Liquidation:** Allowing broker RMS to liquidate positions at market, incurring massive slippage.
- **Uncanceled Pending Orders:** Leaving active limit orders in the market that consume margin during adverse price moves.
- **Illiquidity Liquidation Trap:** Dumping an illiquid micro-cap stock to cover margin, causing the stock to drop 20%, which further drops NLV, triggering a secondary margin call. ADV caps solve this.
- **Selling the Winners:** Naively selling highly liquid, low-margin-density winners first, leaving the portfolio concentrated in high-risk, illiquid losers.

## Production Implementation Reference

- Reference code: `scripts/margin_call_engine.py` (`BrokerMarginCallEngine`, `AccountMarginSnapshot`).
- Automated unit tests: `scripts/test_margin_call_engine.py`.

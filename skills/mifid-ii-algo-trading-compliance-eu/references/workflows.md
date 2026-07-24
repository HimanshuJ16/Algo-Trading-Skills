# Deep Workflow Reference — mifid-ii-algo-trading-compliance-eu

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Pre-Trade Risk Control Verification (RTS 6 Article 13/15):**
   - Execute `MiFID2ComplianceManager.validate_pretrade_order()` before routing any order to an EU trading venue.
   - Enforce:
     - **Price Collar Check:** Reject orders deviating $> 5\%$ from reference price.
     - **Max Order Value Check:** Limit maximum monetary notional per order.
     - **Max Volume Check:** Limit maximum units per order.
     - **Message Rate Limit Check:** Throttles order messages per second (e.g. $\le 10$ msgs/sec).

2. **RTS 6 Article 18 Emergency Kill Switch & Order Cancellation:**
   - Implement `trigger_rts6_kill_switch(user, reason)` to halt order placement AND automatically invoke `cancel_resting_orders_fn()` to purge all active resting orders from the venue.

3. **Mandatory MiFID II Order Tagging:**
   - Attach `MiFID2OrderTag` (`algo_id`, `client_id`, `trading_capacity`, `short_selling_flag`, `timestamp_ns`) to every outbound order payload.

4. **RTS 6 Annex I Testing & Audit Log:**
   - Retain structured audit records for annual RTS 6 self-assessment compliance reviews.

## Failure Modes Observed in Production

- **Partial Kill Switch Execution:** Halting new order placement without cancelling active resting orders on the venue.
- **Missing Price Collars:** Omitting price deviation checks, allowing fat-finger orders far outside current market bid-ask spreads.
- **Generic Algo Tagging:** Using non-compliant or missing algorithm ID flags, resulting in venue regulatory fines.
- **Unrecorded Pre-Trade Audits:** Failing to retain audit logs of pre-trade risk decisions for annual compliance reviews.

## Production Implementation Reference

- Reference code: `scripts/pretrade_risk_checks.py` (`MiFID2ComplianceManager`, `RTS6PreTradeResult`, `MiFID2OrderTag`).
- Automated unit tests: `scripts/test_pretrade_risk_checks.py`.

# Pre-Flight / Sign-off Checklist — backpressure-drop-degrade-policy

Use this before considering the skill's implementation complete.

- [ ] **Risk Tier Isolation (`NEVER_DROP`):** Under sustained simulated overload (replay at multiples of peak historical tick rate), confirm risk-critical streams (position/margin/kill-switch) never drop data and instead trigger rate-limited alerts.
- [ ] **Alert Cooldown Verification:** Confirm that alert rate-limiting prevents alert flooding during sustained volatility spikes.
- [ ] **Degradation Verification (`DEGRADE`):** Confirm non-critical indicator streams switch gracefully to 1-second OHLC bar aggregation via `TickAggregator` without dropping summary volume/price bounds or blocking the pipeline.
- [ ] **Sampling Verification (`SAMPLE` / `DROP_OLDEST`):** Confirm UI/dashboard streams drop stale head ticks while retaining the latest LTP update.
- [ ] **Post-Session Telemetry Audit:** Confirm `get_metrics_summary()` outputs valid drop rates, watermark breaches, and policy activation statistics post-session.
- [ ] **Automated Tests:** Execute `python scripts/test_backpressure_policy.py` and confirm all unit tests pass.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

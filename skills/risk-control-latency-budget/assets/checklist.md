# Risk-Control Latency Sign-off Checklist

- [ ] Event, decision, dispatch, acknowledgement, cancellation, and containment semantics are explicit.
- [ ] Timestamp sources share a monitored clock domain; invalid ordering is uncertain/error, never clamped.
- [ ] Budgets, percentile windows, sample counts, scopes, alerts, and escalation actions are approved.
- [ ] Dispatch and acknowledgement are measured separately where the control requires external confirmation.
- [ ] Trace retention/export is bounded, non-blocking, and monitored.
- [ ] Breach and uncertainty paths verify the fail-safe action and broker/exchange state.
- [ ] Non-production fault tests cover queue, clock, store, network, data, rate-limit, and acknowledgement failures.
- [ ] Run `python scripts/test_risk_latency_budgeter.py`.

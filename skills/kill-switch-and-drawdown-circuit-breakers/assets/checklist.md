# Pre-Flight / Sign-off Checklist — kill-switch-and-drawdown-circuit-breakers

Use this before considering the skill's implementation complete.

- [ ] **Independent Risk Veto:** Confirm `KillSwitchCircuitBreaker` operates outside strategy signal logic and vetoes order execution.
- [ ] **Limits Are Sane On Construction:** Confirm `max_drawdown_pct` is a fraction (`0.10` = 10%, not `10`) and that constructing with an out-of-range limit raises `ValueError` rather than silently disabling a breaker.
- [ ] **Daily Loss & Drawdown Halting:** Confirm daily loss breaches and peak-equity drawdown breaches halt trading and trigger `flatten_fn()`.
- [ ] **Fail-Closed On Bad Data:** Confirm a `NaN`/`Inf` P&L or equity value halts with `HALTED_INVALID_INPUT` instead of passing every threshold comparison.
- [ ] **Liquidation Is Not Self-Blocked:** Confirm a position-reducing order is still approved while halted (`REDUCE_ONLY_ALLOWED`), and that a reversal or an exposure-increasing order is not.
- [ ] **Capital Flows Adjusted:** Confirm `record_capital_flow()` is called on every deposit/withdrawal, and that a scheduled withdrawal does not trip the drawdown breaker.
- [ ] **Broker Position Reconciliation:** Confirm position desync between internal logs and broker account triggers `HALTED_DESYNC`.
- [ ] **Alerting Survives Failure:** Confirm a raising `alert_fn` does not prevent the force-flatten, and that a failed `flatten_fn` fires the escalated `FORCE-FLATTEN FAILED` alert and records `flatten_succeeded=False`.
- [ ] **Human Re-Enable Gate:** Confirm the system remains halted until `human_re_enable()` returns `True`, that a blank identity/reason or an unlisted operator is refused, and that every attempt lands in `re_enable_log`.
- [ ] **Drawdown Re-Baseline Is Deliberate:** Confirm resuming after a drawdown halt requires an explicit operator-supplied `new_peak_equity`, and is never reset automatically.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/kill-switch-and-drawdown-circuit-breakers/scripts` and confirm 100% test pass rate.
- [ ] **Live-Fire Drill:** Confirm each trigger has been deliberately engineered in a paper/sandbox environment — not merely unit-tested — with the out-of-band alert observed by a human.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

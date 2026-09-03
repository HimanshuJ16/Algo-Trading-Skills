---
name: risk-control-latency-budget
description: >-
  Use when auditing the timing of a risk control rather than its logic, because a
  correct kill switch that takes seconds to get a cancel in front of the matching engine
  has already failed; budgets observation, decision, dispatch and acknowledgement.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, latency-budget, risk-control-sla, circuit-breaker-latency, kill-switch-latency, clock-synchronization, mifid-ii-rts-6
  brokers_frameworks: "Python Dataclasses; CLOCK_MONOTONIC_RAW; MiFID II RTS 6; MiFID II RTS 25; SEC Rule 15c3-5"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or auditing the *timing* of a live risk control — a kill switch, a drawdown circuit breaker, a position-limit check, a margin breaker, a cancel-all path. A control that is logically correct but takes 2.5 seconds to evaluate and get a cancel-all in front of the matching engine during a collapse does not protect capital; it documents the loss. The engine decomposes each decision into ingestion, evaluation, transmission and acknowledgement, audits the decomposition against a budget, and names the stage that consumed the most of it.

Three things make a latency budget report wrong in ways nothing complains about, and this skill exists to make each of them visible:

1. **The measurement may stop at the wrong place.** `order_sent` is a local timestamp. It proves the process wrote to a socket. It does not prove the venue cancelled anything, and for a kill switch the venue acting *is* the control. Declare the required end state per control and the engine audits that window, not the convenient one.
2. **The clock may not support the comparison.** A duration computed across two hosts whose clocks may disagree by more than the budget is not a measurement. Mark the trace `clock_synchronized=False` and it is reported `UNCERTAIN` — never `PASS`.
3. **The absence of a breach may be the absence of measurement.** An empty audit is reported as *unhealthy*, because instrumentation that stopped emitting looks exactly like a risk pipeline that never breaches.

## When NOT to Use

- **As the risk control itself.** This module measures and grades; it does not evaluate exposure, cancel orders, or trip anything. The control and the fail-safe action live elsewhere — see `kill-switch-and-drawdown-circuit-breakers` and `execution-algorithm-kill-switch-integration`.
- **With the shipped 50 ms budget unchanged.** `default_sla_budget_ms = 50.0` is an engineering placeholder. **No regulator publishes a numeric latency budget for a pre-trade risk check.** SEC Rule 15c3-5 requires the controls and prescribes no speed; MiFID II RTS 6's only numeric deadline is Article 16(5)'s five seconds for *real-time alert generation*, which is a monitoring deadline, not a check deadline. Calibrate against your own measured capacity, or the verdict means nothing. See `references/standards.md`.
- **As a general latency percentile service.** The `p99` here is a coarse health indicator over recorded traces. For tail analysis proper — P99.9, coordinated-omission correction, sample-count resolution gates, fleet pooling — use `latency-monitoring-percentile-based-slas`.
- **On wall-clock timestamps.** `time.time()` / `CLOCK_REALTIME` is "affected by discontinuous jumps in the system time" and by NTP frequency adjustment; a duration taken across an NTP step can come out negative or implausibly small. Use `time.perf_counter_ns()` / `CLOCK_MONOTONIC_RAW`.
- **To profile CPU time.** Ingestion and transmission delay are queueing and I/O, not compute. A profiler that reports only function execution time will show a healthy risk control that misses its budget by an order of magnitude.

## Prerequisites

- Four timestamps in milliseconds from **one** synchronized, monotonic clock domain: event observation `t_event_ms`, evaluation start `t_start_ms`, evaluation finish `t_end_ms`, order dispatch `t_order_sent_ms`; plus `t_ack_ms` where the control's outcome depends on the venue.
- A declared end state per control (`LatencyEndState.DISPATCH` or `LatencyEndState.ACKNOWLEDGEMENT`) and an approved budget `sla_budget_ms` for that window, derived from measured capacity rather than inherited from this skill's default.
- A clock-health signal to pass as `clock_synchronized`. If your monitoring cannot tell you whether the domain is synchronized, that is the first gap to close — see `clock-synchronization-ptp-for-trading-hosts` and `clock-drift-monitoring-alerting-thresholds`.
- An approved fail-safe action to invoke on breach. This module raises the finding; it does not act on it.

## Workflow

1. **Declare the required end state before instrumenting anything.** For a pre-trade rejection that never leaves the process, `DISPATCH` is the honest boundary. For a kill switch or cancel-all, it is `ACKNOWLEDGEMENT` — RTS 6 Article 12 obliges a firm to "cancel immediately, as an emergency measure, any or all of its unexecuted orders", and only the venue's acknowledgement evidences that the orders are gone. Set it once per budgeter via `default_end_state`, override per call where a control differs.
2. **Stamp every boundary in one clock domain, and record whether that domain is healthy.** Under MiFID II RTS 25 (Commission Delegated Regulation (EU) 2017/574), Annex Table 2, a firm engaged in high-frequency algorithmic trading may diverge from UTC by up to 100 µs, and all other trading activity by up to 1 ms. Two such clocks bracketing one measurement can contribute twice that — which is the whole of a sub-millisecond budget. If the boundaries are stamped on different hosts, either put them on one synchronized domain or pass `clock_synchronized=False`.
3. **Record the trace and let invalid input fail closed.** Non-finite timestamps, a non-positive budget, and timestamps that go backwards all raise `LatencyError`. A backwards interval is a clock fault, not a fast risk control: clamping it to zero converts a broken measurement into a flattering one, so it is rejected instead.
4. **Read the status, not the number.**
   - `PASS` — the audited window was measured on a trusted clock and came in at or under budget. Equality passes; the budget is a maximum.
   - `BREACH` — measured, trusted, over budget. Invoke the approved fail-safe action and verify it completed; an alert alone is not containment.
   - `UNCERTAIN` — either the clock was not trusted, or the required end state was never observed (`ACKNOWLEDGEMENT` demanded, no `t_ack_ms`). This is not a pass. A cancel with no acknowledgement is the single most dangerous trace in the set, because the position may still be live.
5. **On an `UNCERTAIN` trace, check `budget_exceeded` before you dismiss it.** Clock skew of a few milliseconds does not explain a 5-second decision. `budget_exceeded` records the raw comparison independently of clock health precisely so a gross overrun is not filed away as a measurement problem.
6. **Read `primary_bottleneck` as a stage within the audited window.** It names `ACKNOWLEDGEMENT` only when acknowledgement is what you budgeted for. `INGESTION` points at queueing and feed handling; `EVALUATION` at the check itself, including any synchronous I/O that should not be there; `TRANSMISSION` at serialization, gateway and network; `ACKNOWLEDGEMENT` at the venue, throttling and retries.
7. **Aggregate with `summarize_audit()` and check `measured_traces` and `p99_resolvable` before quoting the percentile.** `UNCERTAIN` traces are excluded from the mean and P99 — folding an untrustworthy duration into a percentile corrupts it silently. Nearest-rank P99 needs at least 100 measured samples; below that the reported "P99" is arithmetically the maximum, and `p99_resolvable` is `False`.
8. **Treat an empty summary as a finding.** `total_traces == 0` returns `is_risk_pipeline_healthy=False`. Absence of breach evidence is not evidence of compliance, and a risk pipeline that has gone quiet is indistinguishable from one that is behaving.
9. **Re-certify the budget under stress, not at rest.** RTS 6 Article 10 requires stress testing against "the highest number of messages received and sent by the investment firm during the previous six months, multiplied by two". A budget met on a quiet afternoon is not a budget met during the event the control exists for.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the send timestamp as containment.** The most consequential error this skill guards against. A cancel-all dispatched in 5 ms and acknowledged 3 seconds later leaves the position exposed for 3 seconds; audited on `DISPATCH` it reports a comfortable pass. Budget cancel and kill paths on `ACKNOWLEDGEMENT`.
- **Recording no acknowledgement and reading silence as success.** A missing `t_ack_ms` on an acknowledgement-budgeted control means nobody knows whether the venue acted. That is `UNCERTAIN` and must be escalated, not filtered out of the dashboard as an incomplete record.
- **Blocking DB or HTTP calls inside the evaluation window.** A synchronous audit-log write or a position lookup over the network puts a dependency's tail latency directly into the risk control's budget. Export asynchronously; keep the critical path bounded.
- **Profiling only the check function.** Measuring `t_end - t_start` and calling it the risk latency ignores queue age before the check and gateway/network time after it — usually the two largest stages.
- **Certifying a budget on unsynchronized clocks.** A 500 µs budget measured between two hosts each permitted 1 ms of divergence under RTS 25 has an error bar larger than the budget. The number renders; it means nothing.
- **Clamping a negative interval to zero.** It turns the clearest possible signal of a clock fault into the best latency figure in the dataset.
- **Quoting a P99 over a handful of traces.** Below 100 measured samples the nearest-rank P99 *is* the maximum wearing a percentile label. Check `p99_resolvable`.
- **Averaging away the uncertain traces by including them.** An unsynchronized 1000 ms trace mixed into a series of 10 ms measurements moves the mean by two orders of magnitude on the strength of a number you already decided not to trust.
- **Reading an empty audit as green.** A stopped instrumentation thread produces exactly the same "no breaches" report as a perfectly healthy pipeline.
- **Alerting instead of acting on a breach.** RTS 6 Article 16(5) requires real-time alerts "within five seconds after the relevant event" — but an alert is the notification, not the containment. On a safety-critical breach, invoke the approved fail-safe and verify broker/exchange state.

## Verification

- Stage decomposition, hand-derived: event 1000, start 1010, end 1015, sent 1030, ack 1040 $\implies$ ingest 10 ms, eval 5 ms, transmission 15 ms, ack 10 ms, total-to-send 30 ms, total-to-ack 40 ms; the three send-window stages sum to `total_to_send_ms`.
- End-state regression: the trace (event 0, sent 5, ack 3005) against a 50 ms budget $\implies$ `PASS` on `DISPATCH` and `BREACH` on `ACKNOWLEDGEMENT`. The same timestamps must not produce the same verdict for both end states.
- Missing acknowledgement: `ACKNOWLEDGEMENT` end state with no `t_ack_ms` $\implies$ `UNCERTAIN`, `audited_latency_ms is None`, `budget_exceeded is None` — never `PASS`.
- Budget boundary: audited 50.0 ms against a 50 ms budget $\implies$ `PASS`; 50.001 ms $\implies$ `BREACH`; 50.0004 ms $\implies$ `BREACH` while `audited_latency_ms` displays 50.0, confirming the comparison runs unrounded.
- Clock trust: `clock_synchronized=False` $\implies$ `UNCERTAIN` regardless of the number; a 5000 ms trace on an unsynchronized clock $\implies$ `UNCERTAIN` with `budget_exceeded=True` and `budget_exceeded_count == 1`.
- Fail-closed input: backwards timestamps, NaN/±Inf timestamps, a boolean or non-numeric budget, a non-positive budget, a blank control name, a non-boolean clock flag and a non-enum end state each raise `LatencyError` and record nothing.
- Distribution hygiene: traces of 10 ms and 20 ms plus one unsynchronized 1000 ms trace $\implies$ `measured_traces == 2`, mean 15.0 ms, P99 20.0 ms — the untrusted sample is excluded.
- Percentile resolution: 99 measured samples of 1..99 ms $\implies$ `p99_resolvable is False`; 100 samples of 1..100 ms $\implies$ `p99_resolvable is True`, P99 = 99.0, mean = 50.5.
- Fail-open regression: `summarize_audit()` with no traces $\implies$ `is_risk_pipeline_healthy is False`.
- Critical-path logging: a `PASS` trace emits at `DEBUG`; `BREACH` and `UNCERTAIN` emit at `WARNING`.
- Bounded, concurrent storage: `max_traces=2` retains 2 of 3 traces; 8 threads × 100 traces $\implies$ 800 recorded.
- Run `python -m unittest discover -s skills/risk-control-latency-budget/scripts`.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `execution-algorithm-kill-switch-integration`
- `risk-control-unit-testing-framework`
- `latency-monitoring-percentile-based-slas`
- `strategy-latency-budget-decomposition`
- `tick-to-trade-latency-measurement`
- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `order-placement-idempotency`

# Risk-Control Latency Sign-off Checklist

## End state and semantics

- [ ] Every control has a **declared required end state** (`DISPATCH` or `ACKNOWLEDGEMENT`),
      recorded in configuration, not implied by the code.
- [ ] Every cancel, kill-switch, and exposure-reducing control is budgeted on
      `ACKNOWLEDGEMENT`. No such control is signed off on the send timestamp alone.
- [ ] Event, decision, dispatch, acknowledgement, cancellation, and effective-containment
      semantics are written down and distinguished.

## Clock domain

- [ ] All boundaries for a given control are stamped in one synchronized clock domain, from a
      monotonic source (`CLOCK_MONOTONIC_RAW` / `time.perf_counter_ns()`), never `time.time()`.
- [ ] A live clock-health signal feeds `clock_synchronized`; it is not hard-coded `True`.
- [ ] The budget is comfortably larger than the combined permitted clock divergence for the
      hosts involved (RTS 25: 100 µs HFT / 1 ms other, doubled across two clocks).
- [ ] Invalid ordering is rejected as an error, never clamped to zero.

## Budgets

- [ ] The shipped 50 ms default has been **replaced** with a calibrated value per control,
      scope, and session, derived from measured capacity.
- [ ] Per-stage budgets (ingestion, evaluation, transmission, acknowledgement, retries,
      fail-safe actuation) exist and sum to less than the end-to-end budget.
- [ ] Budgets were validated under stress volume, not at rest (RTS 6 Art. 10: 2× the peak
      message rate of the previous six months).
- [ ] Budgets, percentile windows, minimum sample counts, alert thresholds, and escalation
      actions are documented and approved.

## Critical path

- [ ] No synchronous DB, HTTP, or file I/O inside the evaluation window.
- [ ] Trace recording is bounded (`max_traces`) and non-blocking; export is asynchronous;
      retention drops are monitored.
- [ ] Passing traces do not emit operator-visible log lines.

## Reporting

- [ ] Reports publish `measured_traces` and `uncertain_count` alongside `total_traces`.
- [ ] `p99_resolvable` is checked before any P99 is quoted (≥ 100 measured samples).
- [ ] Uncertain traces are excluded from the distribution, not averaged in.
- [ ] Raw breach traces are preserved for investigation.
- [ ] Reports are segmented by control, venue, account, strategy, session, and deployment
      version.

## Alerting and action

- [ ] Alerts fire on breach, on `UNCERTAIN`, on `budget_exceeded` under an untrusted clock, on
      missing acknowledgements, on queue age, on clock skew, on retries, on stale control
      configuration, and on actuator failure.
- [ ] Alerts also fire on the **absence** of traces — an empty audit is treated as unhealthy.
- [ ] On a safety-critical breach the approved fail-safe action is invoked and its completion
      verified against broker/exchange state. An alert alone is not sign-off.

## Fault testing (non-production)

- [ ] Stalled queue, clock skew, clock-source change, slow trace store, stale market data,
      network loss, broker throttling, rate-limit rejection, acknowledgement never arriving,
      and fail-safe actuator failure each produce the correct status and escalation — never a
      `PASS`.
- [ ] Run `python -m unittest discover -s skills/risk-control-latency-budget/scripts`.

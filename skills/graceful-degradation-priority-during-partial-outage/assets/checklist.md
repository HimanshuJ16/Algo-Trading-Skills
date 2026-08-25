# Pre-Flight Checklist — Priority Load Shedding

## Task classification
- [ ] Every task entering the chokepoint is tagged `P1_CRITICAL` / `P2_HIGH` / `P3_MEDIUM` / `P4_LOW` at creation time.
- [ ] An unparseable priority raises and rejects the batch — no `else` branch guesses a tier.
- [ ] P1 and P4 do not share a thread pool, connection pool, or event loop.

## Health telemetry
- [ ] Unreadable metrics are reported as `None`, never as `0.0` or a frozen last value.
- [ ] Non-finite values (`NaN`, `inf`) are rejected before reaching the router.
- [ ] Every sample carries `sample_age_seconds`, and `max_health_sample_age_seconds` is set to a small multiple of the sampling interval.
- [ ] The sampler's own failure has been tested: it escalates, it does not read as healthy.

## Thresholds and policy
- [ ] CPU and packet-loss thresholds calibrated against this host's measured percentiles, with the rationale recorded.
- [ ] DB-latency thresholds either calibrated and enabled, or deliberately left disabled.
- [ ] Inclusive (`>=`) boundary semantics understood: a sample exactly on a threshold degrades.
- [ ] Any custom policy still processes P1 in every mode and sheds monotonically.

## Shed work
- [ ] `DEFER` and `DROP` are handled differently downstream — a deferred exit is re-queued, not counted as done.
- [ ] `manual_intervention_required` is wired to a pager, not just logged.
- [ ] An alternative arrangement exists for outstanding orders and positions that does not depend on the degraded component (RTS 6 Art. 14(2)(g)).
- [ ] Deferred tasks are re-validated against current price, position and signal before replay.
- [ ] Backlog replay is staggered with randomised backoff and a retry budget.

## Recovery
- [ ] `recovery_confirmation_samples` set deliberately; recovery steps one level at a time.
- [ ] Mode flapping has been checked with metrics oscillating around a threshold.
- [ ] `reset_mode_state()` is restricted to operator-confirmed recovery.

## Audit and rehearsal
- [ ] `audit_notes`, `classification_reasons` and mode transitions are persisted.
- [ ] Per-session reconciliation: received = processed + deferred + dropped, with every deferred P1/P2 closed out.
- [ ] Degradation and recovery paths exercised on a schedule, not first executed during a real outage.

# Pre-Flight Checklist — Model Inference Latency Budget

Sign-off gate before an ML model serves signals on a latency-sensitive path.

## Budget derivation

- [ ] `max_inference_budget_ms` derived by subtracting the other terms of the
      tick-to-trade budget from the strategy's end-to-end tolerance — **not** inherited
      from this skill's `1.0 ms` default.
- [ ] `warning_threshold_ms` set below the max, wide enough to serve as the re-entry
      band for fallback hysteresis.
- [ ] The rest of the path (market data, features, risk, wire) budgeted too, so the
      inference budget is not the only measured term.

## Measurement hygiene

- [ ] Samples measured with a monotonic clock (`time.perf_counter_ns()`), never
      `time.time()`.
- [ ] Warm-up runs excluded from the sample series (first `Run()` allocates, captures
      and replays the CUDA graph; `trtexec` discards ≥ 200 ms by default).
- [ ] GPU clocks locked / throttling ruled out for the benchmark window.
- [ ] The timed region covers what the strategy actually pays for, including tensor
      marshalling.
- [ ] One window = one model version, one runtime version, one hardware profile, one
      batch size.
- [ ] No sample reads exactly 0.0 ms (if any do, the clock is coarser than the
      inference — the report warns about this).

## Sample count

- [ ] At least 100 samples, or the P99 is the observed maximum and the report returns
      `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES`.
- [ ] At least 1,000 samples if a P99.9 figure will be quoted anywhere.
- [ ] `is_p99_resolvable` is `True` on the report used for the promotion decision.

## Audit result

- [ ] `status` read, not just `is_sla_compliant`: `INSUFFICIENT_SAMPLES` is neither a
      breach nor an approval.
- [ ] `warnings` read even on a passing report.
- [ ] Jitter recorded as both σ and IQR; a large gap between them means a stall, not
      broad variance.
- [ ] Percentile method recorded with the figures (`NEAREST_RANK` unless there is a
      reason to reconcile with NumPy-based tooling).

## Fallback readiness

- [ ] `fallback_action` is one of the recognised actions — a typo is silently ignored
      by the model router.
- [ ] The fallback model profiled on the **deployment** hardware and its P99 supplied
      as `fallback_profiled_p99_ms`.
- [ ] Fallback P99 is genuinely below both the primary model's P99 and the budget
      (INT8 is not faster without int8 instruction support).
- [ ] Fallback model's *signal* backtested in its own right — quantization is not
      loss-less, and the switch changes what the position sizer receives.
- [ ] Switch procedure drains in-flight inferences and re-evaluates positions sized on
      the previous model's signal.
- [ ] Re-entry to the primary model requires several consecutive windows below the
      warning threshold, so the router cannot flap.

## Governance

- [ ] Report persisted with model version, runtime version, hardware, batch size and
      window boundaries.
- [ ] A real-time latency circuit breaker exists separately from this windowed audit
      (`risk-control-latency-budget`).

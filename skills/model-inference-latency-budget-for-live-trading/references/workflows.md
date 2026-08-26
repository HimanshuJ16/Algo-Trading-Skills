# Workflows for Model Inference Latency Budgeting

## 0. Derive the budget before measuring anything

An inference budget is a subtraction, not a preference. Start from the end-to-end
latency the strategy can tolerate, subtract market-data handling, feature construction,
pre-trade risk checks and wire time, and what remains is
$\tau_{\text{inference}}$. Set `max_inference_budget_ms` to that number and
`warning_threshold_ms` somewhere below it — the gap between the two is the band
caller-side hysteresis will use. The shipped `1.0 / 0.8` defaults are placeholders with
no published authority behind them. See `strategy-latency-budget-decomposition`.

## 1. Collect samples that describe the production path

- **Measure with a monotonic clock.** `time.perf_counter_ns()` in Python. A duration
  computed from two `time.time()` readings can come out negative when the system clock
  is stepped, and the engine will reject the whole window if it does.
- **Discard warm-up.** ONNX Runtime's first `Run()` allocates CUDA memory, captures the
  CUDA graph and replays it; `trtexec` throws away at least 200 ms by default. One cold
  sample in a 100-sample window lands squarely on the P99.
- **Measure the call the strategy actually makes**, including any pre/post-processing
  inside the timed region if the strategy pays for it. A P99 that excludes tensor
  marshalling is not the P99 the tick-to-trade path experiences.
- **Lock the GPU clocks** for a benchmark run. Floating clocks and thermal throttling
  make consecutive kernels run at different frequencies and turn the measurement into a
  measurement of the cooling.
- **Collect enough samples.** P99 needs 100; P99.9 needs 1,000. Below that the engine
  returns `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` rather than an approval.
- **Do not mix hardware, batch sizes, or model versions in one window.** Each is a
  different distribution; pooling them produces a percentile that describes nothing.

## 2. Audit the window

```python
from inference_latency_budgeter import (
    InferenceBudgetConfig,
    ModelInferenceLatencyBudgeterEngine,
)

config = InferenceBudgetConfig(
    model_id="XGB_ALPHA_01",
    max_inference_budget_ms=0.6,        # derived in step 0, not inherited
    warning_threshold_ms=0.45,
    fallback_action="LINEAR_HEURISTIC_FALLBACK",
    fallback_profiled_p99_ms=0.08,      # measured, on this hardware
)

report = ModelInferenceLatencyBudgeterEngine().evaluate_inference_latency_budget(
    config, sample_latencies_ms
)
```

The engine validates the series first and raises `InferenceSampleError` — a `ValueError`
subclass — on NaN, infinity, negative, boolean, non-numeric, absurd-magnitude or empty
input. It rejects rather than filters, because each of those classes otherwise produces
a *passing* report instead of an error.

## 3. Read the verdict

| Status | Meaning | Action |
|---|---|---|
| `INFERENCE_LATENCY_SLA_BREACH` | P99 exceeded the budget. Reported at any sample count. | Execute `recommended_fallback_action`, after reading the `warnings` about whether that fallback helps. |
| `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` | Nothing breached, but the window cannot resolve P99. | Profile for longer. Do not promote on this report. |
| `INFERENCE_LATENCY_WARNING_NEAR_LIMIT` | P99 inside the budget but above the warning threshold. | Compliant. Investigate before the tail grows. |
| `INFERENCE_LATENCY_NORMAL` | P99 inside the warning threshold, on a resolvable window. | Proceed. |

`is_sla_compliant` is the affirmative and is `True` only for the last two.
`is_sla_breached` is the separate positive finding. Both `False` means the window
proved nothing either way — that is the `INSUFFICIENT_SAMPLES` case, and it is a
distinct outcome from compliance.

Always read `warnings` even on a passing report: it carries the P99.9-is-really-the-
maximum notice, the coarse-clock notice, and every fallback-headroom finding.

## 4. Act on a breach

1. **Check the fallback is real.** If `fallback_profiled_p99_ms` was unset, the report
   says there is no evidence the fallback is faster. Profile it on the deployment
   hardware before wiring it to an automatic trigger — INT8 quantization is not faster
   on hardware without int8 instruction support.
2. **Check the fallback is safe.** Quantization is not loss-less. The fallback model
   emits a different signal distribution from the one the position sizer and risk
   limits were calibrated against. A latency fallback that has never been backtested in
   its own right is an unreviewed strategy change. See
   `model-versioning-and-rollback` and `paper-to-live-promotion-checklist`.
3. **Switch cleanly.** Drain in-flight inferences, swap the session, and re-evaluate
   open positions whose sizing came from the previous model's signal.
4. **Debounce the return.** The engine holds no state and will flip verdicts window by
   window for a P99 sitting on the boundary. Restore the primary model only after
   several consecutive windows below `warning_threshold_ms`.

## 5. Investigate the tail, not the mean

When P99 breaches while P50 is healthy, the cause is almost always one of:

- **A garbage collection pause.** CPython starts a cyclic collection when allocations
  minus deallocations exceed `threshold0`, so per-inference allocation sets the pause
  rate. Pre-allocate and reuse input/output buffers via IOBinding; consider
  `gc.freeze()` after warm-up.
- **A cold or invalidated kernel.** A changed input shape invalidates a CUDA Graph
  capture — "shapes and addresses of inputs/outputs cannot change across inference
  calls for the same graph annotation id" — so a batch size that varies with the number
  of active instruments silently leaves the fast path.
- **Contention.** Another process on the same core or GPU. See
  `feed-handler-cpu-pinning-and-numa-awareness`.
- **Throttling.** Check clocks and temperature before blaming the model.

When P50 itself has moved, the model or the runtime changed — that is a whole-
distribution shift, not a tail event, and the answer is in `model-versioning-and-rollback`
rather than in a fallback trigger.

## 6. Record the audit

Persist the `InferenceBudgetReport` alongside the model version, runtime version,
hardware identity, batch size and sample-window boundaries. A percentile without those
five facts cannot be reproduced or compared against the next window.

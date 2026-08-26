---
name: model-inference-latency-budget-for-live-trading
description: >-
  Auditing an ML model's inference latency against a percentile budget on a live tick-to-trade path: HdrHistogram-compatible nearest-rank P25-P99.9 percentiles, a sample-count resolution gate that refuses to approve a tail it cannot measure, unrounded budget comparison, jitter as both sigma and IQR, and a fallback recommendation that says out loud whether the fallback was ever shown to be faster.
domain: Market Microstructure Latency
subdomain: ML Inference Budgeting & Real-Time SLA Governance
tags: ["model-inference", "latency-budget", "onnx", "tensorrt", "p99-latency", "sla-governance", "quantized-fallback", "tick-to-trade"]
brokers_frameworks: ["ONNX Runtime", "TensorRT", "CUDA Graphs", "HdrHistogram", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when an ML model — a gradient-boosted ensemble, an LSTM, a transformer — sits on a latency-sensitive path and you need to decide whether it is fast enough to keep serving live signals. Inference is one term in the tick-to-trade budget ($\tau_{\text{total}} = \tau_{\text{market\_data}} + \tau_{\text{features}} + \tau_{\text{inference}} + \tau_{\text{risk}} + \tau_{\text{wire}}$), and it is the term most likely to have a fat tail: the same model is 0.3 ms on a warm cache and 9 ms when a cyclic garbage collection lands mid-forward-pass. The mean absorbs that; the P99 does not.

Getting a P99 *number* out of a list of samples is easy. This module exists because four things routinely make that number wrong in ways nothing complains about:

1. **The sample count may not be able to resolve the percentile.** A "P99" over 60 inference calls is not a 1-in-100 event — it is the worst of 60 calls wearing a label it did not earn. Below 100 samples the P99 rank *is* the maximum, arithmetically.
2. **The estimator may report a latency the model never produced.** Linear interpolation — NumPy's default, and what this module's previous revision used — blends neighbouring observations. On a model that is either 0.3 ms or 2.5 ms and nothing between, it reports a median of **1.4 ms**.
3. **The comparison may be made on a rounded value.** A P99 of 1.0004 ms displayed to three decimals is 1.0 ms, and 1.0 is not greater than a 1.0 ms budget. The previous revision recorded that case as compliant.
4. **The fallback may not be faster.** `QUANTIZED_ONNX_FALLBACK` is a recommendation, not a guarantee: ONNX Runtime documents that quantization "has overhead (from quantizing and dequantizing), so it is not rare to get worse performance on old devices."

The engine reports nearest-rank percentiles, jitter as both $\sigma$ and IQR, and a status that distinguishes *breached*, *compliant*, and *not measurable*.

## When NOT to Use

- **As a latency collector or a profiler.** This module reads no clock, loads no model, and calls no inference runtime. It audits a sample series you already captured; every guarantee it offers is about arithmetic on those samples.
- **As the thing that switches models.** The engine *recommends* a fallback action. Executing it — draining in-flight inferences, swapping the session, re-checking that positions sized on the full model's signal are still valid — belongs to the model router. See `model-versioning-and-rollback`.
- **With the shipped budgets unchanged.** `max_inference_budget_ms = 1.0` and `warning_threshold_ms = 0.8` are engineering starting points. **No regulator, exchange or standards body publishes a model inference latency SLA** — see `references/standards.md`. Derive yours by subtracting the other terms of $\tau_{\text{total}}$ from the end-to-end budget the strategy actually has; `strategy-latency-budget-decomposition` is the skill for that.
- **On a window shorter than the audited percentile needs.** The engine says `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` rather than guessing, but the fix is a longer profiling run, not a looser reading of the report.
- **On samples that include the warm-up runs.** The first inference is not representative and should not be in the series — see the pitfalls below.
- **As a real-time circuit breaker.** This is a windowed audit over a completed sample series, with no state between calls and therefore no debounce. Tripping live trading on a latency excursion belongs in a dedicated risk control — see `risk-control-latency-budget` and `execution-algorithm-kill-switch-integration`.

## Prerequisites

- A series of inference latency samples in milliseconds (`sample_latencies_ms`). Samples must be finite, non-negative and numeric; the engine rejects the series otherwise rather than reporting percentiles over corrupted data.
- **Samples measured with a monotonic clock**, in Python `time.perf_counter_ns()` or `time.monotonic_ns()` — never `time.time()`, whose value can step backwards when NTP adjusts it and produce a negative duration.
- **Samples collected after warm-up.** ONNX Runtime documents that "the very first `Run()` performs a variety of tasks under the hood like making CUDA memory allocations, capturing the CUDA graph for the model, and then performing a graph replay ... the latency associated with the first `Run()` is bound to be high." `trtexec` warms up for at least 200 ms by default before it times anything.
- An `InferenceBudgetConfig`: `model_id`, `max_inference_budget_ms`, `warning_threshold_ms` (must not exceed the max), `fallback_action` (one of `QUANTIZED_ONNX_FALLBACK`, `LINEAR_HEURISTIC_FALLBACK`, `SKIP_SIGNAL`, `ALERT_ONLY`), and optionally `percentile_method` and `fallback_profiled_p99_ms`.
- Python 3.9+ for `math.nextafter`. No third-party dependencies.

## Workflow

1. **Validate before computing anything.** The engine rejects four input classes outright, because each produces a confidently wrong report rather than an error:
   - **NaN/Inf** — a NaN compares `False` against every bound, so `sorted()` silently leaves the list unordered *and* `NaN > budget` is `False` for every budget. Left unchecked, a corrupted series reads as a passing audit.
   - **Negative** — an elapsed inference time cannot be negative. It means the measuring clock was stepped mid-measurement, so the positive samples in the same window are wrong by an unknown amount too. Reject the window; do not filter the negatives and keep the rest.
   - **Booleans and non-numerics** — `True` is arithmetically 1, so a series of booleans yields percentiles instead of an error.
   - **Absurd magnitudes and empty series** — above 1e9 ms the value is a unit error, not a latency.
2. **Compute percentiles by nearest rank.** `ceil(p/100 × N)` into the ascending-sorted series, matching HdrHistogram's `getValueAtPercentile`, including its one-ULP `nextAfter` nudge. Every reported figure is a latency the model actually produced. `PERCENTILE_LINEAR` stays available for parity with NumPy/Excel-based tooling, with the caveat in point 2 of *When to Use*.
3. **Check that P99 is resolvable at this sample count.** `is_percentile_resolvable(n, 99.0)` is true only when the nearest rank falls *strictly below* N; when it lands on N the "P99" is just the maximum. P99 needs 100 samples, P99.9 needs 1,000. P99.9 is reported but never audited, because most inference profiling windows cannot resolve it — when they cannot, the report says so in `warnings`.
4. **Audit the budget, and mind the asymmetry between proving a breach and proving compliance**:
   - A breach is reported at **any** sample count — an over-budget inference was genuinely observed, and ten samples are enough to observe one.
   - Approval requires resolution. If nothing breached but the window cannot resolve P99, the verdict is `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` and `is_sla_compliant` is `False`. *No breach observed* is not *compliant*, and `is_sla_breached` stays `False` so the two are never confused.
   - Precedence: `INFERENCE_LATENCY_SLA_BREACH` > `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` > `INFERENCE_LATENCY_WARNING_NEAR_LIMIT` > `INFERENCE_LATENCY_NORMAL`. A near-limit reading on an unresolvable window is demoted to a `warnings` entry rather than presented as a verdict.
   - Comparisons run on unrounded values; rounding is applied to the report fields only, at four decimal places (0.1 µs). A P99 of 1.0004 ms against a 1.0 ms budget is a breach, not a 1.000 ms pass. Equality passes: the SLA is "must not exceed".
5. **On breach, check the fallback before trusting it.** The engine recommends the configured action and, in the same report, states whether that action was ever shown to help. With `fallback_profiled_p99_ms` set it flags a fallback that is no faster than the model it replaces, or that is faster but still over budget. With it unset it says plainly that there is no evidence either way. `ALERT_ONLY` is flagged too: it means the breaching model keeps serving until a human intervenes.
6. **Decide, with hysteresis you supply yourself.** The engine is stateless and audits one window at a time, so it cannot debounce anything. A model whose P99 sits on the boundary will alternate breach/normal window after window; use `warning_threshold_ms` as the re-entry band — fall back above `max`, and only restore the primary model once P99 has been below `warning_threshold_ms` for several consecutive windows.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Approving a model on a P99 the window cannot support.** Below 100 samples the P99 nearest rank *is* the maximum. The number renders, the dashboard is green, and it describes the worst of a short run rather than a 1-in-100 event. This is the failure mode most likely to survive code review, because nothing about the output looks wrong. The same applies at 10× the scale to the P99.9 that this skill's own earlier revision told readers to compute from 100 samples.
- **Rounding the percentile before comparing it to the budget.** Round for the report, compare on the raw value, or a 1.0004 ms P99 is displayed as 1.000 and recorded as a pass.
- **Assuming the quantized fallback is faster.** INT8 helps on "x86-64 with VNNI, GPU with Tensor Core int8 support and Arm®-based processors with dot-product instructions"; elsewhere the quantize/dequantize overhead can make it slower. Profile the fallback on the *deployment* hardware before wiring it to an automatic trigger, and pass that number as `fallback_profiled_p99_ms`.
- **Treating the fallback as latency-only.** "Quantization is not a loss-less transformation. It may negatively affect a model's accuracy." An automatic switch to INT8 changes the signal distribution the position sizer and risk limits were calibrated on. A latency fallback is a silent strategy change unless the fallback model's signal has been backtested in its own right.
- **Benchmarking with warm-up runs in the sample series.** ONNX Runtime's first `Run()` allocates CUDA memory, captures the CUDA graph and replays it; `cudnn_conv_algo_search` defaults to exhaustive benchmarking. `trtexec` discards at least 200 ms of warm-up before timing. One cold sample in a 100-sample window lands squarely on the P99.
- **Letting the GPU clocks float.** NVIDIA notes that "running TensorRT workloads with floating clocks or with throttling taking place can lead to more non-determinism in tactic selections and unstable performance measurements across inferences because every CUDA kernel may run at slightly different clock frequencies." A P99 measured on a thermally throttling card is a measurement of the cooling, not the model.
- **Allocating per inference in Python.** CPython's cyclic collector starts a pass when "the number of allocations minus the number of deallocations exceeds threshold0" — so allocation rate on the hot path directly sets GC pause frequency, and the pause lands on a random inference. Pre-allocate input/output buffers, reuse them via IOBinding, and consider `gc.freeze()` after warm-up to move long-lived objects out of the collector's reach.
- **Changing input shape under CUDA Graphs.** ONNX Runtime requires that "shapes and addresses of inputs/outputs cannot change across inference calls for the same graph annotation id", and "multi-threaded usage is currently not supported". A batch size that varies with the number of active instruments silently invalidates the capture the benchmark was run under.
- **Flapping between the primary and fallback model.** A P99 sitting on the boundary trips a fallback in one window and clears in the next. Each switch is a discontinuity in the signal series. Use the warning threshold as a re-entry band and require several consecutive clean windows.
- **Auditing the model in isolation from the path it sits on.** A 0.9 ms P99 inside a 1.0 ms inference budget is worthless if feature construction upstream takes 4 ms of a 2 ms end-to-end budget. Budget the whole path first — see `strategy-latency-budget-decomposition`.

## Verification

- Percentile arithmetic against hand-derived values: over the samples 1..100 ms, nearest rank returns P25 = 25, P50 = 50, P75 = 75, P99 = 99 $\implies$ every figure is an observed sample.
- Resolution boundary: `is_percentile_resolvable(1000, 99.9)` is `True` and `is_percentile_resolvable(999, 99.9)` is `False`; `min_samples_for_percentile(99.0) == 100` and `min_samples_for_percentile(99.9) == 1000`. A healthy 50-sample series $\implies$ `INFERENCE_LATENCY_INSUFFICIENT_SAMPLES` with `is_sla_compliant` `False` and `is_sla_breached` `False`; the same series at 100 samples $\implies$ `INFERENCE_LATENCY_NORMAL`.
- Breach/approval asymmetry: 9 samples at 0.3 ms plus one at 5.0 ms against a 1.0 ms budget $\implies$ `INFERENCE_LATENCY_SLA_BREACH` despite `is_p99_resolvable` being `False`.
- Estimator divergence: 50 samples at 0.3 ms plus 50 at 2.5 ms $\implies$ nearest-rank median 0.3 ms, `PERCENTILE_LINEAR` median 1.4 ms — a latency the model never produced.
- Rounding regression: 100 samples at 1.0004 ms against a 1.0 ms budget $\implies$ `INFERENCE_LATENCY_SLA_BREACH`, with `p99_ms` displayed as 1.0004. Exactly 1.0 ms $\implies$ not a breach.
- Standing breach case: 95 samples at 0.3 ms plus 5 at 2.5 ms, budget 1.0 ms $\implies$ `INFERENCE_LATENCY_SLA_BREACH` with `recommended_fallback_action == 'QUANTIZED_ONNX_FALLBACK'` and `p99_ms == 2.5`.
- Fallback headroom: the same breach with `fallback_profiled_p99_ms=3.0` $\implies$ warnings that the fallback is *not faster* and is *above the budget*; with `1.5` $\implies$ over-budget only; with `0.4` $\implies$ neither. `ALERT_ONLY` $\implies$ a warning that the breaching model keeps serving.
- Input rejection: NaN, Inf, negative, boolean, non-numeric, absurd-magnitude, empty and `None` series each raise `InferenceSampleError` (a `ValueError` subclass) rather than producing a report. A sample of exactly 0.0 ms raises no error but warns that the clock is coarser than the inference.
- Config rejection: a non-positive budget, a `warning_threshold_ms` above the budget, an unrecognised `fallback_action` (a typo the model router would silently ignore), an unknown `percentile_method`, and a blank `model_id` each raise `InferenceBudgetConfigError`.
- Jitter: over 1..100, the Bessel-corrected $\sigma$ is $\sqrt{83325/99}$ (sum of squared deviations $n(n^2-1)/12$) and the IQR is 50.
- Run `python -m unittest discover -s skills/model-inference-latency-budget-for-live-trading/scripts`.

## Related Skills

- `latency-monitoring-percentile-based-slas`
- `strategy-latency-budget-decomposition`
- `risk-control-latency-budget`
- `tick-to-trade-latency-measurement`
- `model-versioning-and-rollback`
- `model-staleness-detection`
- `gradient-boosted-tree-vs-neural-net-tradeoffs`
- `feature-engineering-cost-benefit-tracking`
- `offline-train-online-infer-deployment`

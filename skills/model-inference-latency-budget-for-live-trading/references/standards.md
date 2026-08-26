# Standards & Sources for Model Inference Latency Budgeting

## There is no published inference latency SLA to comply with

**No regulator, exchange or standards body publishes a model inference latency SLA.**
The `1.0 ms` budget and `0.8 ms` warning threshold shipped as defaults in this module
are engineering starting points, not requirements, and an earlier revision of this
skill was wrong to present its engineering choices in a "MUST" table without saying
where they came from. An inference budget is derived by subtracting the other terms of
a strategy's tick-to-trade budget from the end-to-end latency it can actually tolerate
— it is a property of that strategy and its venue, not of ML inference in general.

What regulation does say about algorithmic trading systems is about *governance*, not
about inference milliseconds. In the EU, RTS 6 (Commission Delegated Regulation (EU)
2017/589) Art. 5–8 require testing before deployment or substantial update in an
environment separated from production, and Art. 16(5) requires real-time monitoring
alerts within five seconds of the relevant event — a deadline on the *alerting* path,
not on model inference. RTS 25 (Commission Delegated Regulation (EU) 2017/574)
constrains business-clock accuracy, which bounds how tight a latency figure can
credibly be claimed to have been measured at all. Article-level detail for RTS 6 lives
in `mifid-ii-algo-trading-compliance-eu`; the clock tolerances live in
`latency-monitoring-percentile-based-slas`. None of these prescribes an inference
budget.

## Percentile semantics

| Area | Documented behaviour | Source |
|---|---|---|
| Nearest-rank percentile | `getValueAtPercentile` "Returns the largest value that (100% - percentile) [+/- 1 ulp] of the overall recorded value entries in the histogram are either larger than or equivalent to." Implemented as `countAtPercentile = ceil(requestedPercentile * totalCount / 100)`, where `requestedPercentile` is `Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)`. | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |

The one-ULP nudge is not a detail. `99.9 / 100.0` evaluates to `0.9990000000000001` in
IEEE-754 double precision, so `ceil(0.999... × 1000)` is `1000`, not `999` — which pins
P99.9 to the observed maximum at exactly the sample count that should first resolve it.
This module reproduces HdrHistogram's `nextAfter` guard for that reason, and matches the
estimator used by `latency-monitoring-percentile-based-slas` so the two skills' numbers
reconcile.

**Coordinated omission is out of scope here and that is a deliberate choice.** Its
correction requires a sampler with a known fixed cadence. Inference latency is normally
measured *around the call itself*, on whatever cadence ticks arrive, so there is no
expected interval to compare against. If inference latency is being measured by a
fixed-rate load generator — a pre-production benchmark harness rather than the live
path — the correction does apply, and
`latency-monitoring-percentile-based-slas` implements it.

## Inference runtime behaviour

| Claim | Documented behaviour | Source |
|---|---|---|
| The first inference is not representative | "The very first `Run()` performs a variety of tasks under the hood like making CUDA memory allocations, capturing the CUDA graph for the model, and then performing a graph replay to ensure that the graph runs. Due to this, the latency associated with the first `Run()` is bound to be high." | [ONNX Runtime CUDA Execution Provider](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) |
| CUDA Graphs constrain the shape of the hot path | "Shapes and addresses of inputs/outputs cannot change across inference calls for the same graph annotation id"; usage "requires using IOBinding"; "Multi-threaded usage is currently not supported"; models with `If`, `Loop`, `Scan` are unsupported. | [ONNX Runtime CUDA Execution Provider](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) |
| Convolution algorithm search costs time on the first run | `cudnn_conv_algo_search` default involves "expensive exhaustive benchmarking". | [ONNX Runtime CUDA Execution Provider](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) |
| Quantization is not automatically faster | "The performance improvement depends on your model and hardware." "x86-64 with VNNI, GPU with Tensor Core int8 support and Arm®-based processors with dot-product instructions can get better performance in general." "Old hardware has none or few of the instructions needed to perform efficient inference in int8." "quantization has overhead (from quantizing and dequantizing), so it is not rare to get worse performance on old devices." | [ONNX Runtime — Quantize ONNX models](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html) |
| Quantization changes the model's outputs | "Quantization is not a loss-less transformation. It may negatively affect a model's accuracy." | [ONNX Runtime — Quantize ONNX models](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html) |
| Benchmarks discard warm-up by default | "By default, `trtexec` warms up for at least 200 ms and runs inference for at least 10 iterations or at least 3 seconds, whichever is longer." | [NVIDIA TensorRT Developer Guide — Best Practices](https://developer.nvidia.com/docs/drive/drive-os/7.0.3/public/drive-os-tensorrt-developer-guide/best-practices.html) |
| Floating GPU clocks destabilise the measurement | "Running TensorRT workloads with floating clocks or with throttling taking place can lead to more non-determinism in tactic selections and unstable performance measurements across inferences because every CUDA kernel may run at slightly different clock frequencies." | [NVIDIA TensorRT Developer Guide — Best Practices](https://developer.nvidia.com/docs/drive/drive-os/7.0.3/public/drive-os-tensorrt-developer-guide/best-practices.html) |
| Allocation rate drives GC pause frequency | "When the number of allocations minus the number of deallocations exceeds *threshold0*, collection starts." `gc.freeze()` "Freeze[s] all the objects tracked by the garbage collector; move them to a permanent generation and ignore them in all the future collections." | [CPython `gc` module documentation](https://docs.python.org/3/library/gc.html) |

**Consequences for this module.** The two claims that most often go unchecked in
practice are the quantization ones. A `QUANTIZED_ONNX_FALLBACK` wired to an automatic
trigger assumes both that INT8 is faster on the deployment hardware and that its
outputs are close enough to the full-precision model's to keep the strategy's
calibration valid. ONNX Runtime's own documentation says neither is guaranteed. That
is why `fallback_profiled_p99_ms` exists, and why the report states explicitly when it
is unset.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is published
by a regulator, an exchange, or a standards body.**

| Rule | Requirement | Why |
|---|---|---|
| SLA metric | The budget MUST be audited against P99, never a mean or median. | A median hides the stalls the budget exists to catch. |
| Estimator | Percentiles MUST default to nearest rank, matching HdrHistogram. | Every reported figure is then a latency actually observed, and reconciles with HdrHistogram-based collectors. Interpolation blends neighbours and can report a value the model never produced. |
| Resolution gate | A percentile whose nearest rank equals N MUST NOT support an approval. | That "percentile" is the observed maximum; the window contains no rarer event to measure. P99 needs 100 samples, P99.9 needs 1,000. |
| Breach/approval asymmetry | A breach MUST be reported at any sample count; an approval MUST NOT. | Observing one over-budget inference proves a breach. Observing none over a short window proves nothing. |
| Comparison precision | Budget comparisons MUST use unrounded values. | Rounding to 3 dp before comparison turns a 1.0004 ms P99 into a 1.000 ms pass. |
| Threshold direction | `warning_threshold_ms` MUST NOT exceed `max_inference_budget_ms`. | Otherwise the warning band is unreachable and every breach arrives unannounced. |
| Fallback vocabulary | `fallback_action` MUST be one of the recognised actions. | An unrecognised action is silently ignored by the model router, leaving an over-budget model serving live signals. |
| Fallback evidence | A recommended fallback MUST be reported as unproven when it has no profiled P99, and flagged when its profiled P99 does not relieve the budget. | The remedy is only a remedy if it is measurably faster on the deployment hardware. |
| Non-finite samples | NaN/Inf MUST be rejected, not filtered. | NaN breaks `sorted()` silently and compares `False` against every budget, so a corrupted series reads as passing. |
| Negative samples | A negative duration MUST reject the whole window. | It proves the measuring clock was stepped; the positive samples share that error by an unknown amount. |
| Jitter | Both σ and IQR MUST be reported. | σ is tail-sensitive, IQR describes the body; one GC pause separates them by orders of magnitude. |
| Statefulness | The engine MUST hold no state between audits. | Hysteresis is a policy decision belonging to the model router, not a hidden property of the auditor. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `max_inference_budget_ms` | `1.0` | Engineering starting point. Not published by anyone. Derive from the strategy's tick-to-trade budget. |
| `warning_threshold_ms` | `0.8` | Engineering starting point; also the re-entry band for caller-side hysteresis. |
| `fallback_action` | `QUANTIZED_ONNX_FALLBACK` | Assumes INT8 is faster on the deployment hardware — verify before relying on it. |
| `percentile_method` | `NEAREST_RANK` | HdrHistogram-compatible. `LINEAR_INTERPOLATION` available for NumPy/Excel parity. |
| `fallback_profiled_p99_ms` | `None` (off) | Set it to the fallback's measured P99 on the deployment hardware to enable the headroom check. |
| `SLA_PERCENTILE` | `99.0` | Module constant. P99.9 is reported but not audited: resolving it needs 1,000 samples. |
| `MAX_PLAUSIBLE_LATENCY_MS` | `1e9` | Unit-error guard (~31.7 years), the same wall-clock bound `latency-monitoring-percentile-based-slas` applies in microseconds. |

## Scope boundary

This module reads no clock, loads no model, and calls no inference runtime. It audits a
sample series that was captured elsewhere, and every guarantee it offers concerns
arithmetic over those samples. It recommends a fallback action; it does not execute
one. It is not a compliance artifact, asserts no regulatory requirement, and its
budgets carry no authority beyond the operator who sets them.

Requires Python 3.9+ for `math.nextafter`. No third-party dependencies.

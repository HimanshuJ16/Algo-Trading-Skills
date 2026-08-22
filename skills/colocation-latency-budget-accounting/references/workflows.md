# Workflows for Latency Budget Accounting

## 1. Hot-path instrumentation

- Capture $T_0$ and $T_5$ as NIC hardware timestamps (`SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_TX_HARDWARE`).
- Capture $T_1 \dots T_4$ with `CLOCK_MONOTONIC` (or a calibrated invariant TSC).
- Store the six values in a fixed-size, lock-free slot. No allocation, no locks, no logging in this thread.

## 2. Clock-domain normalisation

- $T_0/T_5$ live in the NIC PHC domain; $T_1 \dots T_4$ live in the host monotonic domain. Convert both onto one nanosecond base before constructing a trace.
- Record the conversion (offset source, refresh cadence, residual error) as part of the telemetry configuration. An undocumented offset produces phase durations that look plausible and are wrong.

## 3. Asynchronous offload and ingestion

- Drain completed slots to a background accounting thread.
- Construct `HotPathTrace` there. Construction validates that the six timestamps are non-decreasing and raises `ValueError` otherwise.
- Wrap construction per trace:

```python
rejected = 0
traces = []
for raw in drain(ring_buffer):
    try:
        traces.append(HotPathTrace(*raw))
    except (ValueError, TypeError):
        rejected += 1          # quarantine counter — alert on the rate
```

- Do **not** clamp a rejected trace to zero and do not abort the batch. Rejects correlate with the slow path, so an uncounted reject biases the tail downwards.

## 4. Budget configuration

- `total_sla_ns` is the end-to-end budget; `phase_slas_ns` must supply a value for every name in `PHASE_NAMES`.
- Unknown or missing phase keys raise. A missing key must never fall through to a 0 ns budget, which would make an innocent phase the apparent bottleneck.
- If $\sum \text{SLA}_k > \text{Total\_SLA}$, the engine warns: the allocation permits a total breach with no phase over its own budget.

## 5. Phase and SLA evaluation

- Phase durations: $\Delta_k = T_k - T_{k-1}$ for the five phases; total $= T_5 - T_0$.
- Breach test: $\text{total} > \text{Total\_SLA}$ (strict — equality is inside budget).

## 6. Bottleneck diagnostics

- For a breached trace, compute $\text{Excess}_k = \Delta_k - \text{SLA}_k$ for every phase and return the full vector as `phase_excess_ns`.
- `primary_bottleneck_phase` $= \arg\max_k \text{Excess}_k$, ties broken by hot-path order.
- A breach whose winning excess is **negative** is a budget-allocation finding, not a code finding: re-allocate the phase budgets or raise the total.

## 7. Jitter reporting

- Aggregate hourly/daily: `mean`, $P_{50}, P_{95}, P_{99}, P_{99.9}$ and `count`, for the total and every phase.
- Gate the tail figures on `count`: ~100 samples before $P_{99}$ is a measurement, ~1,000 before $P_{99.9}$ is. Below that the engine logs a warning and the figure is an interpolation between the top observations.
- Track the quarantine rate from step 3 next to the percentiles. A rising reject rate invalidates the tail before it invalidates the mean.

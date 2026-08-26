# Workflows for Percentile-Based Latency SLA Monitoring

## 1. Capture the samples correctly (upstream of this module)

- Measure each interval with a **monotonic** clock (`CLOCK_MONOTONIC_RAW`,
  `time.perf_counter_ns()`), never a wall clock. `CLOCK_REALTIME` is subject to
  discontinuous jumps and can yield a negative interval mid-measurement.
- For a two-host measurement (tick in at the NIC, order out at the gateway), a monotonic
  clock is not usable — the hosts need synchronised real-time clocks, and the achievable
  SLA resolution is bounded by their combined divergence from UTC.
- Record the sampler's **intended cadence** if it has one. You cannot correct for
  coordinated omission after the fact without it.
- Size the window to the tightest percentile you intend to audit: **100 samples for P99,
  1,000 for P99.9**. Below that the percentile is arithmetically the maximum.

## 2. Validate the series

`audit_latency_sla` rejects, rather than repairs:

| Input | Why it is rejected outright |
|---|---|
| Empty | Nothing to audit. |
| NaN / Inf | Breaks `sorted()` silently; `NaN <= budget` is `False` for every budget, so a corrupted series reads as a *pass*. |
| Negative | A duration cannot be negative; the bracketing clocks disagree, so the positive samples share an unknown error. |
| Non-numeric / bool | `True` would otherwise be read as 1 µs. |

Do not filter negatives and audit the remainder — the whole window is suspect.

## 3. Correct for coordinated omission (opt-in)

Set `expected_sample_interval_us` **only** when the sampler has a known fixed cadence.
Per HdrHistogram, each value above the interval generates a decreasing series down to
the interval:

```
50,000 µs stall at a 1,000 µs cadence
  -> 50,000, 49,000, 48,000, ... , 1,000   (50 records, not 1)
```

Apply once. A second correction double-counts the stall. Leave it off for event-driven
samplers, where there is no expected interval to compare against.

## 4. Compute percentiles and jitter

- Nearest rank: `ceil(p/100 × N)` into the ascending-sorted series, with HdrHistogram's
  one-ULP nudge so P99.9 resolves at exactly 1,000 samples.
- Reported: P25, P50, P75, P90, P95, P99, P99.9, plus min, max and mean.
- Jitter as **both** population σ (tail-sensitive) and IQR = P75 − P25 (describes the
  body). One stall separates the two by orders of magnitude; that separation is the
  signal.

## 5. Audit the budgets

Evaluated on **unrounded** values; rounding applies to report fields only.

| Order | Status | Condition |
|---|---|---|
| 1 | `SLA_BREACH_P999_CRITICAL` | P99.9 > `sla_p999_target_us` |
| 2 | `SLA_BREACH_P99_WARNING` | P99 > `sla_p99_target_us` |
| 3 | `SLA_BREACH_P50_WARNING` | P50 > `sla_p50_target_us` (tails healthy: a distribution shift) |
| 4 | `INSUFFICIENT_SAMPLES_FOR_SLA` | No breach, but an audited percentile is unresolvable at this N |
| 5 | `SLA_COMPLIANCE_APPROVED` | No breach and every audited percentile resolvable |

The asymmetry between 1–3 and 4 is deliberate: a short window can prove a breach but
cannot prove compliance.

## 6. Annotate the measurement noise floor

With `clock_uncertainty_us` set, any percentile within that distance of its budget is
reported in `warnings` as undecidable. The **status is never changed** by this check —
the budget is policy, the noise floor is measurement, and conflating them hides one.

## 7. Aggregate across nodes

```python
fleet = LatencySampleSeries("FLEET", pool_latency_samples([node_a, node_b]))
report = engine.audit_latency_sla(fleet)
```

Pool the raw samples and re-rank. Never average per-node percentiles: two gateways at a
uniform 10 µs and 900 µs have a mean-of-P99 of 455 µs and a true fleet P99 of 900 µs.

## 8. Read the report

`LatencySlaReport` carries the verdict *and* the conditions under which it was reached:
`percentile_method`, `coordinated_omission_corrected`, `clock_uncertainty_us`,
`is_p99_resolvable`, `is_p999_resolvable`, and a `warnings` list. Persist those
alongside the percentiles — a P99.9 without its sample count and correction state is not
a reproducible measurement.

# Workflows for Per-Vendor Market Data Latency Monitoring

## 1. Normalise the four timestamps to one epoch (upstream of this module)

This is the step that actually decides whether the audit means anything. The four
stamps come from four clocks owned by three organisations, and each arrives in its own
convention:

| Stamp | Written by | Typical native form |
|---|---|---|
| `t_exchange_us` | The venue | ITCH 5.0: nanoseconds since local midnight, no date, no timezone. CME MDP 3.0: nanoseconds since the Unix epoch. |
| `t_vendor_us` | The vendor's gateway | Vendor convention; carries no regulated accuracy guarantee. |
| `t_local_nic_us` | The NIC | The NIC's PTP hardware clock (PHC), *not* the system clock. |
| `t_app_us` | Your process | The system clock. |

Before constructing a `LatencySample`:

- Convert every stamp to **microseconds on one epoch**. For an ITCH-style
  midnight-relative stamp you must supply the session date and the venue's local
  timezone yourself; nothing in the feed carries them.
- Handle the midnight rollover. A midnight-relative stamp wraps to zero, which turns a
  positive latency into a large negative one at the session boundary.
- Discipline the PHC to the system clock (`phc2sys`) or convert hardware timestamps
  before use. An undisciplined PHC drifting against the system clock produces a
  steadily growing app-queue segment that has nothing to do with queueing, and
  eventually a negative one.
- Prefer a **session-relative** epoch over microseconds-since-Unix-epoch if you need
  sub-microsecond figures — at 1.8e15 the float64 spacing is 0.25 µs, so sub-microsecond
  latencies are not evidenced whatever the NIC measured. `timestamp_quantum_us` reports
  the spacing on every audit.

## 2. Let the engine reject rather than repair

`audit_vendor_latencies` refuses these outright, because each one produces a
confidently wrong report rather than an error:

| Input | Why it is rejected |
|---|---|
| Empty sample list | Nothing to audit. |
| NaN / Inf | NaN breaks `sorted()` silently and compares `False` against every budget; a corrupted series then reads however the audit happens to look at it. |
| `bool` | `True` is an `int` in Python and would be read as a 1 µs timestamp. |
| Non-numeric | A string timestamp is a pipeline bug, not a latency. |
| Blank `vendor_id` | Latency that cannot be attributed to a vendor cannot support a vendor SLA verdict. |
| \|timestamp\| > 1e17 µs | A nanosecond stamp in a microsecond field, or an unconverted epoch. |

## 3. Understand the clock-domain verdict

A **negative segment delta is the headline finding of this module**, not an edge case.
It is not a small latency; it is proof that the two clocks bracketing that segment
disagree, which means the *positive* deltas from the same window are wrong by the same
unknown, unsigned amount.

The default (`reject_clock_inconsistent_windows=True`) returns
`VENDOR_CLOCK_DOMAIN_ERROR` for that vendor and publishes **no percentiles** for it.
Withholding the numbers is deliberate: printing them next to the warning invites exactly
the reading the warning exists to prevent.

Diagnose in this order — the segment that went negative names the pair of clocks:

| Negative segment | Clocks in disagreement | Usual cause |
|---|---|---|
| `VENDOR_TRANSPORT` | Venue vs vendor gateway | Epoch or timezone conversion error on `t_exchange_us`; session/midnight rollover. |
| `NETWORK_WIRE` | Vendor gateway vs local NIC | Vendor gateway clock offset; your PHC not disciplined to UTC. |
| `APP_QUEUE` | Local NIC (PHC) vs system clock | PHC not disciplined; hardware timestamp used raw. |

One vendor's broken clocks do not invalidate the others: the verdict is per vendor, and
the report separates `sla_breaching_vendors` from `unmeasurable_vendors`.

## 4. Compute percentiles and jitter

- Nearest rank, `ceil(p/100 × N)` into the ascending-sorted series, with HdrHistogram's
  one-ULP rank guard so P99.9 resolves at exactly 1,000 samples. Every figure reported
  is a latency that was actually observed.
- `PERCENTILE_LINEAR` remains available for parity with NumPy/Excel tooling, but on a
  bimodal feed (10 µs fast path, 900 µs slow path) it reports a median of **455 µs** —
  a latency the feed never produced.
- Jitter as **both** population σ and IQR = P75 − P25. One stall moves σ by orders of
  magnitude and leaves the IQR untouched; that separation is the signal.

## 5. Audit the budget

Verdicts, in precedence order:

| Order | Status | Condition |
|---|---|---|
| 1 | `VENDOR_CLOCK_DOMAIN_ERROR` | Any negative segment delta. An untrustworthy breach is not a breach, so this outranks one. |
| 2 | `VENDOR_LATENCY_SLA_BREACH_ALERT` | Audited percentile > budget. Reported at **any** sample count. |
| 3 | `INSUFFICIENT_SAMPLES_FOR_SLA` | No breach, but the sample count cannot resolve the audited percentile. |
| 4 | `VENDOR_LATENCY_HEALTHY` | No breach and the audited percentile is resolvable. |

The asymmetry between 2 and 3 is deliberate: a short window can prove a breach but
cannot prove its absence. Size the window to the tightest percentile you intend to
audit — **100 samples for P99, 1,000 for P99.9**.

Comparisons run on unrounded values; rounding applies to report fields only. A P99 of
500.004 µs against a 500 µs budget is a breach, not a 500.00 µs pass.

## 6. Attribute the tail to a segment

This is the reason to decompose at all, and it is not done by comparing segment P99s.
**Percentiles are not additive**: the segment with the highest standalone P99 need not
be the one that was slow during the ticks that actually blew the budget, because those
observations can come from disjoint samples.

Attribution therefore runs over the **tail subset** — the samples whose end-to-end
latency landed at or above the audited percentile — and reports each segment's mean
contribution across just those ticks:

```
985 fast ticks : transport 200 µs, wire  10 µs, app 10 µs
 15 slow ticks : transport 200 µs, wire 5000 µs, app 10 µs

by mean          -> transport 200.00 µs  >  wire 84.85 µs   (points at the wrong hop)
by tail subset   -> wire 5000 µs (95.97% of the tail)       -> dominant_tail_segment
```

`dominant_tail_segment` is the party to raise the ticket with. `segment_stats[...]`
carries `mean_us`, `p50_us`, `p99_us`, `max_us`, `tail_mean_us` and `tail_share_pct`
for each of `VENDOR_TRANSPORT`, `NETWORK_WIRE` and `APP_QUEUE`.

## 7. Annotate the measurement noise floor

Set `clock_uncertainty_us` to the combined uncertainty of the two clocks bracketing the
measurement. Any audited percentile landing within that distance of the budget is
reported in `warnings` as undecidable. The **status is never changed** by this check —
the budget is policy, the noise floor is measurement, and conflating them hides one.

RTS 25's 100 µs is a defensible per-clock ceiling for your own HFT hosts. It does not
apply to a vendor's gateway clock, which is not a party to that regulation, so the
uncertainty on the `VENDOR_TRANSPORT` segment is the largest and the least knowable of
the three.

## 8. Pool, never average

`audit_vendor_latencies` pools every sample carrying the same normalised `vendor_id`
across symbols and hosts into one distribution before ranking, which is the only correct
way to obtain a feed-level percentile. Vendor IDs are matched case- and
whitespace-insensitively so a stray space does not split one feed into two
distributions whose P99s someone would then be tempted to average.

If you need a cross-region or cross-vendor figure, concatenate the raw
`LatencySample` lists and audit once. Never average per-node or per-symbol percentiles.

## 9. Read the report

`VendorLatencyReport` carries the verdict *and* the conditions under which it was
reached: `audited_percentile`, `percentile_method`, and per vendor
`sample_count`, `min_samples_required`, `is_audited_percentile_resolvable`,
`clock_inconsistent_sample_count`, `timestamp_quantum_us` and a `warnings` list.

Persist those alongside the percentiles. A P99 without its sample count, estimator and
clock-integrity state is not a reproducible measurement, and it is not something to put
in front of a vendor in a contractual conversation.

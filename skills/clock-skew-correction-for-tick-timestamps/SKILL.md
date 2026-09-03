---
name: clock-skew-correction-for-tick-timestamps
description: >-
  Use when captured ticks carry both a venue send time and a local receive time and the
  local clock drifts. Estimates rate offset from windowed minimum one-way delays and
  re-expresses local stamps on the venue timescale, monotonically.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: clock-skew, minimum-delay-filtering, timestamps, market-data, hft, monotonicity
  brokers_frameworks: "Generic Infrastructure; NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you hold a capture of market data in which each event carries both
a **venue-stamped send time** and a **locally stamped receive time**, and the two clocks
run at measurably different rates. Uncorrected skew shows up as a delay series that
trends across the session: the same event appears to take longer and longer to arrive,
when in fact the local clock is losing or gaining time. Typical consumers:

- Merging captures from several hosts into one ordered event stream for order-book
  reconstruction or backtesting.
- Latency analysis where a drifting recorder would otherwise produce a fake trend in
  tick-to-trade or feed-arrival measurements.
- Re-stamping an archive taken while a host was running on plain NTP, or before PTP was
  deployed.

`ClockSkewCorrector` fits `delay ≈ alpha + beta·t` to the **minimum** delay in each time
window, then subtracts the fitted line from the local timestamps and forces the output
to increase strictly.

## When NOT to Use

- **To satisfy a clock-synchronisation obligation.** Under MiFID II RTS 25 the
  timestamps a firm records for reportable events must come from a clock synchronised
  and traceable to UTC — 100 µs maximum divergence and 1 µs granularity for
  high-frequency algorithmic trading. That is achieved by disciplining the clock
  (`clock-synchronization-ptp-for-trading-hosts`), not by regressing timestamps after
  the fact. Statistically corrected timestamps are a research artifact; never file them
  or present them as an RTS 25 record. See `references/standards.md`.
- **To measure one-way latency after removing the constant term.** From one-way data the
  minimum transit delay and the constant clock offset are *not separable* — they enter
  the measurement only as a sum. The default correction removes both, so
  `corrected − exchange_ts` is excess delay above the session minimum, not latency. Use
  `remove_constant_offset=False` for latency work, which removes drift only.
- **When the host is already PTP-disciplined to sub-microsecond accuracy.** Fitting
  skew to a hardware-disciplined clock mostly fits your own measurement noise. Monitor
  it instead (`clock-drift-monitoring-alerting-thresholds`).
- **On ticks merged from multiple venues or multiple capture hosts.** Skew is a property
  of one clock *pair*. One line fitted across a mixed series describes no real pair of
  clocks. Split by (venue, host) and fit each separately; the implementation rejects
  input that is not ordered by exchange time, which catches the common form of this
  mistake.
- **Across a clock step.** NTP *slews* small offsets but *steps* the clock once the
  offset exceeds 128 ms by default, and a leap second is a 1 s step. A single straight
  line fitted across a step is wrong on both sides of it — split the series at the step.
- **When the venue timestamp is coarser than the drift you are chasing.** A venue that
  stamps to the millisecond cannot evidence tens of microseconds of drift over a short
  window; you need a much longer span or a better source timestamp.

## Prerequisites

- Paired `exchange_ts` and `local_ts` per event, **both** from the same venue/host pair,
  sorted by exchange time, in a single unit (`s`, `ms`, `us`, `ns`).
- Timestamps as **UTC epoch counts**, never local wall-clock — a DST transition in a
  wall-clock series is a one-hour "skew" that this model will happily fit. See
  `daylight-saving-time-transition-handling`.
- For tick-resolution work, **int64 nanoseconds** with `time_unit="ns"`. Float64 POSIX
  seconds have a ULP of ~238 ns near 1.7e9 and physically cannot hold two events 100 ns
  apart, whatever the correction does.
- Enough events per window to make a minimum meaningful — the default
  `min_points_per_window=10` drops sparser windows rather than trusting them.
- NumPy. No other runtime dependency.

## Workflow

1. **Split the series by clock pair.** One fit per (venue, capture host). Sort by
   exchange timestamp; `fit` raises rather than silently returning zero drift if you do
   not.
2. **Compute one-way delays** `local_ts − exchange_ts`, differencing in the input's
   integer unit before scaling to seconds so the difference is not swallowed by float
   rounding at epoch magnitudes.
3. **Take the minimum per window, not the mean or median.** Queueing delay is one-sided
   and strictly positive, so any central statistic is biased upward by congestion and
   moves with it. Anchor each retained point at the exchange time of the sample that
   *achieved* the minimum, not at the window's start edge.
4. **Drop windows with too few samples.** The minimum of *k* draws falls as *k* grows, so
   a window holding one tick contributes a raw jitter draw as though it were a lower
   bound. Under a U-shaped intraday volume profile that converts tick density into fake
   skew.
5. **Fit the line and then interrogate it before believing it.** Check `diagnostics`:
   `reliable` False means the fit fell back to zero drift, not that the clock is good.
   A low `r_squared`, a large `residual_std_sec`, or a step warning in the log means
   the lower envelope is not a straight line. (A step mid-series inflates the residual
   spread too, so `fit` detects steps from jumps between consecutive window minima
   rather than from residual outliers.) A drift beyond
   `max_drift_ppm` (default 1000 ppm) raises — the Unix kernel discipline NTP relies on
   slews at most 500 ppm, so a larger *sustained* rate is a data problem: mixed units,
   mixed hosts, or a step inside the window.
6. **Decide what to subtract.** Default (`remove_constant_offset=True`) puts the output
   on the venue's timescale and origin. For latency analysis pass `False` to remove
   drift only and keep delays positive.
7. **Apply, then enforce monotonicity.** `T_final,i = max(T_corr,i, T_final,i−1 + ε)`.
   ε is clamped up to one output tick in integer mode; in float mode the guarantee is
   still enforced, falling back to one-ULP separation with a warning when the epsilon is
   below what the magnitude can represent.
8. **Keep the model causal in production.** `fit` records its first exchange timestamp
   as the reference epoch and `transform` measures against *that* epoch, so calibrating
   on a warm-up window and applying to later ticks is correct. `fit_transform` is
   in-sample by construction — offline research only.

> Full procedure: see `references/workflows.md`.
> Standards and evidence: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Regressing on all delay points.** Network queueing is positive-only. Ordinary least
  squares over every sample is biased upward by the mean queueing delay and reads a
  congestion episode as clock drift. Filter to window minima first.
- **Re-basing the correction on each batch.** If `transform` measures elapsed time from
  its *own* first tick rather than the epoch used at `fit` time, the intercept is applied
  at the wrong origin. At 100 ppm, a 300 s gap between calibration and application is a
  silent **30 ms** error — larger than most of the effects being studied.
- **Calling the intercept a clock offset.** It is `minimum_transit + clock_offset`, and
  the two are not separable from one-way data. Reporting it as an offset overstates the
  clock error by the whole propagation delay.
- **Trusting a "strict monotonicity" guarantee in float64 seconds.** Adding a 1 ns
  epsilon to a POSIX-epoch float64 is a no-op — the ULP there is ~238 ns — so a naive
  implementation emits equal timestamps while claiming they increase. Use int64
  nanoseconds.
- **Fitting across a clock step.** A 128 ms NTP step or a leap second is a discontinuity,
  not drift; one line across it is wrong everywhere. Detect and segment.
- **Treating a sustained latency regime change as clock skew.** If *every* sample in a
  run of windows is delayed — a route change, a switch upgrade, a saturated link — the
  lower envelope itself moves, and one-way data cannot distinguish that from the clock.
  Minimum filtering only rejects congestion that leaves quiet samples in each window.
  Corroborate large fitted drifts against `clock-drift-monitoring-alerting-thresholds`
  telemetry before believing them.
- **Extrapolating a stale fit.** Crystal drift changes with temperature. Applying a
  morning calibration to an afternoon session accumulates error; the implementation
  warns when asked to extrapolate beyond its calibration span.
- **In-sample correction leaking into a backtest.** `fit_transform` over a whole day uses
  future ticks to stamp earlier ones. In any research pipeline that feeds a strategy,
  calibrate on a prior window only. See `lookahead-bias-elimination`.
- **Silently accepting unsorted input.** Shuffled exchange timestamps used to collapse
  the windowing and return zero drift with no error at all, which looks exactly like a
  healthy clock.

## Verification

- `python -m unittest discover -s skills/clock-skew-correction-for-tick-timestamps/scripts`
  (30 tests). Coverage includes: recovery of a known injected drift rate (positive,
  negative and zero) to within 5 ppm; rejection of intermittent congestion that would
  push naive OLS past 100 ppm; the reference-epoch regression (chunked `transform` must
  equal a single call); strict monotonicity with an epsilon below the float64 ULP;
  int64-nanosecond mode preserving 100 ns separation; and rejection of unsorted, NaN,
  empty and mismatched input.
- Generate a synthetic feed with a known drift (e.g. 100 µs/s) plus strictly positive
  jitter, then confirm `drift_ppm` matches the injected rate within a few ppm and
  `np.all(np.diff(out) > 0)` holds.
- Confirm scale: a 2M-tick session fits and transforms in well under a second. If it
  takes tens of seconds, the windowing has degraded to a per-window scan of the whole
  array.
- Cross-check any material fitted drift against independent clock telemetry
  (`chronyc tracking`, `pmc`/`ptp4l` offsets). A regression on market data alone cannot
  tell a drifting clock from a drifting network path.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `cross-vendor-timestamp-precision-reconciliation`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `historical-order-book-reconstruction-from-message-logs`
- `lookahead-bias-elimination`

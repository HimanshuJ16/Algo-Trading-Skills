# Workflows for Clock Skew Correction

## A. Offline re-stamping of an archived capture

Use when a stored capture needs corrected timestamps and there is no causality
constraint (reconstruction, forensics, latency archaeology).

1. **Partition by clock pair.** Group the capture by `(venue, capture_host)`. Skew is a
   property of one pair of clocks; a fit across a mixed series describes nothing real.
2. **Normalise units and ordering.** Convert to int64 nanoseconds (`time_unit="ns"`) —
   float64 seconds cannot hold nanosecond tick data. Sort by `exchange_ts`. Confirm both
   series are UTC epoch counts, not local wall-clock.
3. **Segment at discontinuities.** Plot the raw delay `local_ts − exchange_ts`. A vertical
   jump is a clock step (NTP steps rather than slews past 128 ms) or a leap second, and
   must split the series — a straight line fitted across a step is wrong on both sides.
4. **Fit.**
   ```python
   c = ClockSkewCorrector(window_size_sec=10.0, time_unit="ns").fit(ex_ns, loc_ns)
   ```
   Windows must be wide enough to usually contain a quiet moment and numerous enough to
   define a slope: aim for tens of windows across the segment, each with well over
   `min_points_per_window` samples.
5. **Interrogate the fit before applying it.**
   - `diagnostics.reliable is False` → the fit fell back to zero drift. That is an absence
     of measurement, not a clean clock. Widen windows or lower `min_points_per_window`,
     or accept that this segment cannot be calibrated.
   - `diagnostics.n_windows_dropped` large → tick density is too low for the window size.
   - A step warning in the log → go back to step 3.
   - `ValueError` on `max_drift_ppm` → mixed units, mixed hosts, or a step. Do not just
     raise the ceiling.
6. **Choose what to remove.**
   - Re-stamping for merge/reconstruction: default `remove_constant_offset=True`.
   - Latency analysis: `remove_constant_offset=False`, which removes drift only and keeps
     delays positive. The default would absorb the minimum transit into the correction,
     leaving *excess* delay rather than latency.
7. **Apply and verify.** `out = c.transform(ex_ns, loc_ns, ...)`; assert
   `np.all(np.diff(out) > 0)` and that the ordering is unchanged
   (`np.argsort(out, kind="stable") == np.arange(out.size)`).

## B. Causal (rolling) correction for a live or research pipeline

Use whenever corrected timestamps feed a strategy, a backtest, or anything else that must
not see the future. `fit_transform` is **in-sample** and is not appropriate here.

1. **Warm-up.** Buffer a calibration span — long enough that drift is visible above the
   jitter floor: at 20 ppm, 300 s of span produces 6 ms of movement against a jitter floor
   of perhaps a few hundred microseconds.
2. **Fit on the warm-up window only.**
   ```python
   c = ClockSkewCorrector(window_size_sec=10.0, time_unit="ns")
   c.fit(warmup_ex, warmup_loc)
   ```
   `fit` stores its first exchange timestamp as the reference epoch, and `transform`
   measures against *that* epoch — which is what makes applying the model to later,
   separately batched ticks correct. Chunked application equals a single call.
3. **Apply forward** to each subsequent batch with `c.transform(...)`. Batch boundaries do
   not affect the result, but note that monotonicity is enforced *within* a call: carry
   the last emitted timestamp across batches yourself if you need the guarantee to hold
   across the seam.
4. **Refit on a schedule**, on a window that ends at or before the ticks being corrected —
   never one that spans them. Crystal drift moves with temperature, so a morning
   calibration decays through the afternoon; `transform` warns when asked to extrapolate
   beyond its calibration span.
5. **Handle a refit that fails.** If `fit` raises on the plausibility ceiling or returns
   `reliable=False`, keep applying the previous model and alert — do not silently fall
   back to zero correction, and do not silently widen the ceiling.
6. **Never let a correction reorder events.** The monotonicity pass guarantees a strictly
   increasing output in input order; sequence numbers, not corrected timestamps, remain
   the authority on ordering.

## C. Reading the model's parameters

| Attribute | Meaning | Trap |
|---|---|---|
| `beta` / `drift_ppm` | Seconds the local clock gains per second of exchange time. The only quantity one-way data identifies. | A drifting network path produces the same observation as a drifting clock. Corroborate against host clock telemetry. |
| `alpha` | Constant term: `minimum_one_way_transit + clock_offset_at_reference`. | **Not** a clock offset. The two components are not separable from one-way data. |
| `diagnostics.min_delay_sec` | Smallest observed delay. | Also includes transit; it is a floor on the pair, not an offset. |
| `diagnostics.residual_std_sec` | Spread of window minima about the line. | Large values mean the lower envelope is not linear — segment the series. |
| `diagnostics.r_squared` | Fit quality on the minima. `nan` when the minima are constant. | A high R² on a *sustained* congestion ramp is still a wrong answer; R² measures linearity, not causation. |

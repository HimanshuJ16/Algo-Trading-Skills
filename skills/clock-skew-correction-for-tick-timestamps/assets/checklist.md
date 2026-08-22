# Pre-Flight Checklist — Clock Skew Correction

## Scope and inputs

- [ ] Is every fit confined to a single `(venue, capture_host)` clock pair? Skew belongs
      to one pair of clocks; a fit across mixed sources describes no real pair.
- [ ] Are both series UTC epoch counts rather than local wall-clock? A DST transition in
      a wall-clock series is a one-hour "skew" the model will happily fit.
- [ ] Are tick-resolution timestamps int64 nanoseconds (`time_unit="ns"`)? Float64 POSIX
      seconds have a ~238 ns ULP and cannot represent them.
- [ ] Is the input sorted by exchange timestamp, with local timestamps in receive order?
- [ ] Has the raw delay series been plotted and checked for a step (NTP steps past
      128 ms; a leap second is 1 s) before fitting a single line across it?

## The estimate

- [ ] Is the regression on **window minima**, never the mean or median of all delays?
- [ ] Are sparse windows excluded (`min_points_per_window`), so a single-tick window
      cannot contribute a raw jitter draw as a lower bound?
- [ ] Is each retained point anchored at the exchange time of the sample that achieved
      the minimum, not the window's start edge?
- [ ] Does the calibration span produce enough windows to define a slope, and enough
      drift to be visible above the jitter floor?
- [ ] Has `diagnostics.reliable` been checked? `False` means the fit fell back to zero
      drift — an absence of measurement, not a healthy clock.
- [ ] Is the fitted drift plausible (default ceiling 1000 ppm, against a 500 ppm kernel
      slew limit)? If it raised, was the cause investigated rather than the ceiling
      raised?
- [ ] Has a material fitted drift been corroborated against independent clock telemetry
      (`chronyc tracking`, `ptp4l`/`pmc`)? One-way data cannot separate a drifting clock
      from a drifting network path.

## Interpretation

- [ ] Is the constant term described as `transit + offset` rather than as a clock offset?
      The two are not separable from one-way measurements.
- [ ] For latency analysis, is `remove_constant_offset=False` used, so delays stay
      positive and comparable rather than becoming excess-over-minimum?

## Application

- [ ] Is the output strictly increasing, **verified** on the returned array rather than
      assumed from the epsilon?
- [ ] Is the event ordering unchanged after correction?
- [ ] Does `transform` measure elapsed time from the epoch recorded at `fit` time, so a
      model calibrated on a warm-up window applies correctly to later batches?
- [ ] For anything feeding research or a strategy, is the calibration window strictly
      earlier than the ticks being corrected (no `fit_transform` over the whole session)?
- [ ] Is the model refit on a schedule, and does a failed refit alert rather than
      silently degrade to zero correction?

## Boundaries

- [ ] Is it clearly documented that these corrected timestamps are **not** a MiFID II
      RTS 25 record? UTC traceability comes from disciplining the clock, not from a
      post-hoc regression.

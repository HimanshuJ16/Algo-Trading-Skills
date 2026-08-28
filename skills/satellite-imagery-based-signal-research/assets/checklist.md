# Pre-Flight Checklist — Satellite Imagery Signal Research

## Data provenance
- [ ] Vendor contract reviewed for permitted use, redistribution limits, and the **actual** capture-to-delivery lag (not this skill's 2-day placeholder).
- [ ] Data derivation and collection method diligenced — how the metric is produced, not just what it claims to measure (cf. the SEC's App Annie action, `references/standards.md`).
- [ ] Panel composition (which lots / tanks / fields) is versioned, and the baseline is rebuilt whenever it changes.
- [ ] Coverage gap against the benchmark statistic understood and written down — e.g. shadow methods see external floating-roof tanks only, while EIA counts fixed-roof, pipeline fill and in-transit volumes too.

## Baseline construction
- [ ] Baseline mean and standard deviation are **point-in-time**: computed only from observations knowable at the acquisition time.
- [ ] `baseline_window_end_iso` supplied so the look-ahead check actually runs.
- [ ] `baseline_historical_std` strictly positive for every observation — no zero-dispersion baselines reaching the engine.
- [ ] `baseline_observation_count` recorded; the window is deep enough that a standard deviation is meaningful.
- [ ] Seasonality handled where the metric has a strong annual or weekly cycle (day-of-week retail traffic, crop phenology).

## Observation quality
- [ ] `usable_pixel_fraction` derived from the product's own mask (Sentinel-2 L2A Scene Classification Layer or equivalent), not eyeballed.
- [ ] `min_usable_pixel_fraction` chosen and fixed **before** looking at which scenes it drops.
- [ ] Obscured scenes treated as missing observations, never as low readings.
- [ ] Metric range-checked against its physical domain (NDVI within `[−1, +1]`, fill fraction within `[0, 1]`).
- [ ] Signal cadence does not exceed sensor revisit (Sentinel-2 ~5 days, Landsat 8/9 16 days per satellite); no interpolation across gaps.

## Point-in-time discipline
- [ ] Backtest keys on `tradeable_from_iso`, **never** on `timestamp_iso`.
- [ ] All acquisition timestamps timezone-aware.
- [ ] `availability_lag_days` sourced from the contract; no negative lags.
- [ ] Vendor restatement policy known, and as-delivered snapshots replayed where history is overwritten.

## Signal semantics
- [ ] Directional mapping verified against the actual traded instrument for each signal type, and every `ImagerySignalType` has a `HIGH_READING_DIRECTION` entry.
- [ ] `z_score_threshold` calibrated to this panel's measured signal-to-noise, not left at the 1.5 placeholder.
- [ ] Signal compared against **consensus expectations**, not only against its own history.
- [ ] `confidence_pct` treated as an uncalibrated rank — not used for position sizing.
- [ ] Domain-specific limits acknowledged: NDVI saturates over dense canopy and is phenology-dependent; floating-roof fill is a **ratio** of interior to exterior shadow, which is what cancels solar-angle seasonality.

## Verification
- [ ] `python -m unittest discover -s skills/satellite-imagery-based-signal-research/scripts` passes 100%.
- [ ] `python tools/validate_skills.py` passes.

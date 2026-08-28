---
name: satellite-imagery-based-signal-research
description: >-
  Use when turning processed Earth-observation metrics — retail parking-lot car counts, external floating-roof crude tank fill, agricultural NDVI — into Z-scored directional research signals with an explicit point-in-time availability stamp, a cloud/usable-pixel gate, and a look-ahead check on the baseline window.
domain: Alternative Data & Quantitative Research
subdomain: Satellite Imagery & Computer Vision Signals
tags: ["satellite-imagery", "alternative-data", "car-counts", "oil-tank-shadows", "ndvi", "quant-signals", "point-in-time"]
brokers_frameworks: ["Earth Observation Alternative Data", "Sentinel-2 / Landsat", "Pandas DataFrames", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you already have a **processed** computer-vision metric out of an Earth-observation pipeline — a vehicle count for a retailer's lots, an estimated fill fraction for a cluster of external floating-roof crude tanks, an NDVI composite over a growing region — and you need to turn it into a research signal that a backtest can consume without cheating.

The attraction is real: these metrics observe physical activity directly, ahead of the official statistics the market trades on. Concretely, the two headline benchmarks publish on fixed, verifiable schedules:

- **EIA Weekly Petroleum Status Report** — released Wednesdays after **10:30 a.m. ET** (summary, overview, and Tables 1–14 in CSV/XLS; the remaining PDF/HTML after 1:00 p.m. ET), reporting stocks **as of the previous Friday**. Holiday weeks slip a day.
- **USDA NASS Crop Progress** — released **4:00 p.m. ET on the first business day of each week**, April 1 through November 30, with reporters responding **as of Sunday**. WASDE lands at **12:00 p.m. ET between the 8th and 12th** of each month.

So a Monday tank read genuinely precedes the Wednesday print. That head start is the entire economic case for the dataset, and it is also precisely what evaporates the moment a backtest is sloppy about *when* the imagery-derived number actually became usable. This engine exists to make that boundary explicit rather than assumed.

The engine emits `tradeable_from_iso` (acquisition + vendor pipeline lag), rejects a baseline window that reaches past the observation being scored, and gates on the fraction of the footprint that was actually visible.

## When NOT to Use

- **Not a computer-vision pipeline.** It does not read imagery, detect vehicles, segment tank shadows, or compute NDVI from bands. It consumes the scalar those steps produce. Everything about detection quality is upstream and out of scope.
- **Not a forecast, and not aware of expectations.** `trading_signal_direction` is a supply/demand **sign convention**: a high reading is bullish for the retailer, bearish for crude, bearish for the crop. Equities and futures trade against *consensus*, not against a metric's own 52-week history. A record parking count in a quarter where the street already modelled a record is not a bullish surprise. Pair with a consensus-relative construction — see `credit-card-transaction-data-signal-construction` for that pattern.
- **Not a substitute for a point-in-time backtest harness.** `tradeable_from_iso` is one timestamp on one observation. Enforcing it across a whole dataset, handling vendor restatements, and replaying as-delivered snapshots belong to `backtesting-alt-data-strategies-with-realistic-availability-lag`.
- **Not a licensing, contract, or MNPI clearance.** Whether you may trade on a dataset is a separate question from whether the arithmetic is sound. The SEC's first enforcement action against an alternative-data provider (*In re App Annie Inc. and Bertrand Schmitt*, Sept. 14, 2021, §10(b)/Rule 10b-5, $10M corporate penalty) turned on how the data was *derived and represented*, not on how a subscriber normalised it. Diligence the provenance — see `alternative-data-vendor-due-diligence-checklist` and `insider-trading-controls-for-alternative-data-usage`.
- **Not a proxy for the official statistic.** Shadow-based tank estimates see **external floating-roof tanks only**. EIA commercial crude stocks additionally include fixed-roof tanks, pipeline fill, and volumes in transit. The satellite series measures a subset and will diverge from the print it is supposed to front-run.
- **Not usable on a single cloudy scene.** Optical sensors return nothing through cloud. One obscured acquisition is not a low reading; it is an absent one. Set `min_usable_pixel_fraction` rather than treating a masked scene as data.
- **Not a signal on its own for thin baselines.** A Z-score against three prior observations is arithmetic, not normalisation.

## Prerequisites

- A processed metric per observation: vehicle count, tank fill fraction, or NDVI. NDVI is `(NIR − Red) / (NIR + Red)` and is bounded to `[−1, +1]` (Landsat 8/9: `(B5 − B4)/(B5 + B4)`; Landsat 4–7: `(B4 − B3)/(B4 + B3)`). Values outside that range indicate an upstream scaling error, not a strong signal.
- A **point-in-time** rolling baseline (`baseline_historical_mean`, `baseline_historical_std`) built only from observations knowable at the acquisition time. `baseline_historical_std` must be strictly positive.
- A **timezone-aware** ISO-8601 image **acquisition** timestamp. Naive timestamps are rejected — with a delivery lag added on top, an unzoned time is ambiguous by up to a day in exactly the direction that flatters a backtest.
- `availability_lag_days` taken from **your vendor contract**. The 2-day default is a placeholder, not a measured constant.
- Optionally `usable_pixel_fraction` (1.0 − cloud/shadow/snow/no-data). Sentinel-2 L2A ships a Scene Classification Layer flagging cloud shadow, low/medium/high-probability cloud, thin cirrus, and saturated or defective pixels — derive the fraction from it rather than eyeballing the scene.
- Optionally `baseline_window_end_iso` and `baseline_observation_count`, so the look-ahead check and the baseline-depth audit can actually run.

## Workflow

1. **Ingest the processed metric.** One `SatelliteObservation` per asset per acquisition. Sensor cadence bounds how often this can update: Sentinel-2 revisits every 5 days with two satellites (2–3 days at mid-latitudes); Landsat 8/9 repeat every 16 days per satellite. A "daily" signal on a 5-day sensor is interpolation, and interpolation across a gap is a forecast you did not intend to make.
2. **Declare the quality gate before looking at results.** Set `min_usable_pixel_fraction` on the engine. It defaults to `0.0` (disabled) because no threshold is universally correct — it depends on footprint size and on how much masking the vendor already did. Choosing it *after* seeing which scenes it would drop is fitting the gate to the answer.
3. **Normalise against the point-in-time baseline.** The engine computes `Z = (X − μ) / σ` unrounded. If `σ ≤ 0` it **raises** rather than substituting 1.0 — a degenerate baseline would otherwise rescale the raw deviation into a full-conviction signal (2,000 extra cars becoming "Z = 2000").
4. **Let the look-ahead check run.** Supply `baseline_window_end_iso`. A window ending *after* the acquisition time raises; a window ending exactly *at* it logs a warning, because the observation is then inside its own baseline and its Z-score is pulled toward zero.
5. **Map the deviation to a direction.** `|Z| ≥ z_score_threshold` takes a side, using the sign convention in `HIGH_READING_DIRECTION`; anything inside the band is `0.0`. Thresholding uses the unrounded Z — a 1.4951 must not round into a trade. A signal type absent from that table raises rather than inheriting another domain's economics.
6. **Stamp the availability boundary.** `tradeable_from_iso = acquisition + availability_lag_days`, normalised to UTC. **This is the value the backtest must honour** — not `timestamp_iso`. Feeding the acquisition time to the backtest is the failure this whole skill is built to prevent.
7. **Read `confidence_pct` as a rank, not a probability.** It is `min(|Z| / strength_saturation_z, 1) × 100`, an uncalibrated monotone ordering. Never size a position from it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Backtesting from the acquisition timestamp.** The image exists at capture; the *number* does not. Detection, mosaicking, QA, and delivery sit in between. Use `tradeable_from_iso`, and take the lag from the contract rather than from this skill's default.
- **A degenerate baseline silently becoming conviction.** If `σ` is 0 (a constant or unpopulated baseline) and the code substitutes 1.0, the "Z-score" is just the raw deviation in raw units — always far past any threshold, always maximum strength, always wrong. This engine raises instead.
- **Rounding the Z-score before thresholding it.** Displaying Z to two decimals is fine. Deciding on the rounded value promotes every observation in `[1.495, 1.5)` into a full-conviction trade.
- **Treating cloud as a low reading.** A 90%-obscured scene is a missing observation. Scored as data it manufactures a spurious deviation, and because cloud is seasonally and geographically correlated, the resulting bias is systematic rather than random.
- **Correcting floating-roof shadows the wrong way.** The exterior shadow (on the tank's shaded side) scales with total tank height; the interior shadow scales with how far the roof has sunk below the rim. Both stretch and shrink together as solar elevation changes through the year. The published approach therefore takes the **ratio** — fill ≈ `1 − (interior shadow area / exterior shadow area)` — which cancels the sun-angle dependence. Subtracting a seasonal adjustment from a bare shadow *length* leaves the confound in.
- **Assuming NDVI maps linearly to yield.** NDVI saturates over dense canopy: past closure, more biomass barely moves the index, which is why EVI exists. It is also phenology-dependent — the same 0.75 means different things at emergence and at grain fill — and cannot distinguish a large low-quality harvest from a small high-quality one.
- **Comparing a satellite series against an official statistic with different coverage.** Shadow methods see external floating-roof tanks; EIA counts far more than that. A persistent gap between the two is usually coverage, not alpha.
- **Confusing panel growth with activity growth.** A vendor adding lots, tanks, or fields mid-history raises the aggregate metric with no change in the underlying economy. Recompute the baseline whenever panel composition shifts.
- **Trading the metric instead of the surprise.** Retail equities respond to revenue versus consensus. Traffic versus its own 52-week mean is only half the comparison.
- **Reading `confidence_pct` as a win probability.** It saturates at an arbitrary `|Z| = 3` by default. It ranks observations; it does not price them.

## Verification

- Process a retail observation at `Z = +2.0` and assert `trading_signal_direction == +1.0`; at `Z = −2.5` assert `−1.0`.
- Process an oil-storage observation at `Z = +2.5` (inventory build) and assert `−1.0`; at `Z = −2.0` (draw) assert `+1.0`. Repeat both signs for NDVI and assert the crop-price convention (`+Z → −1.0`).
- Assert `Z` exactly at the threshold fires, and that `observed_metric` giving `Z = 1.4951` stays neutral even though it displays as `1.50`.
- Assert `baseline_historical_std = 0.0` raises rather than returning a direction, and that a NaN or infinite metric raises rather than producing `direction = 0.0` with a NaN strength.
- Assert a naive `timestamp_iso` raises, and that `2026-08-05T09:00:00-03:00` and `2026-08-05T12:00:00Z` produce the identical `tradeable_from_iso`.
- Assert `baseline_window_end_iso` after `timestamp_iso` raises with a look-ahead message, and that a window ending exactly at acquisition warns.
- Assert that with `min_usable_pixel_fraction=0.7`, a scene at `0.3` returns `direction 0.0` and `quality_gate_passed False` while still reporting its Z-score for audit.
- Assert every `ImagerySignalType` member has a `HIGH_READING_DIRECTION` entry.
- Run `python -m unittest discover -s skills/satellite-imagery-based-signal-research/scripts` and confirm a 100% pass rate.

## Related Skills

- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `credit-card-transaction-data-signal-construction`
- `weather-data-signal-research-for-commodity-strategies`
- `alternative-data-vendor-due-diligence-checklist`
- `insider-trading-controls-for-alternative-data-usage`
- `lookahead-bias-elimination`
- `web-scraped-sentiment-data-pipeline`

# Workflows for Satellite Imagery Based Signal Research

This engine occupies one narrow stage: **processed metric → normalised directional
signal with a point-in-time availability stamp**. Detection sits upstream;
portfolio construction sits downstream.

```
imagery → [CV pipeline]  → metric ─┐
                                   ├─→ [THIS ENGINE] → QuantitativeSatelliteSignal
   point-in-time rolling baseline ─┘                        │
                                                            ▼
                                          backtest keyed on tradeable_from_iso
```

## 1. Satellite metric ingestion

Build one `SatelliteObservation` per asset per **acquisition**. Required: a
timezone-aware acquisition timestamp, the processed metric, and a point-in-time
baseline mean and standard deviation.

Sanity-check the metric against its own physical domain before normalising. NDVI
outside `[−1, +1]` and a fill fraction outside `[0, 1]` are upstream scaling
errors that will otherwise present as enormous, entirely spurious Z-scores.

Do not resample to a cadence the sensor cannot support. Sentinel-2 revisits every
5 days with two satellites (2–3 days at mid-latitudes) and Landsat 8/9 every 16
days per satellite; a daily series over a 5-day sensor is interpolation, and
interpolating forward across a gap reintroduces exactly the look-ahead this
pipeline is trying to eliminate.

## 2. Quality gating

Set `min_usable_pixel_fraction` on the engine **before** inspecting results.
Deciding the threshold after seeing which scenes it drops fits the gate to the
answer.

There is no universally correct value — it depends on footprint size, target type,
and how aggressively the vendor already masks. That is why the default is `0.0`
(disabled): an unexamined default here would be a fabricated constant presented as
a standard.

Derive `usable_pixel_fraction` from the product's own mask (for Sentinel-2 L2A, the
Scene Classification Layer, which flags cloud shadow, low/medium/high-probability
cloud, thin cirrus, and saturated or defective pixels).

A blocked observation returns `trading_signal_direction = 0.0` and
`quality_gate_passed = False`, but still reports its Z-score so the observation
remains auditable rather than vanishing.

## 3. Z-score normalisation

```
Z = (observed_metric − baseline_historical_mean) / baseline_historical_std
```

Constraints the engine enforces:

- **`baseline_historical_std > 0`, strictly.** A zero or negative dispersion has no
  Z-score. Substituting `1.0` would silently rescale the raw deviation into
  units of one — 2,000 extra cars would read as "Z = 2000", clearing every
  threshold at maximum strength.
- **All inputs finite.** A NaN metric otherwise yields a NaN Z-score that fails
  both threshold comparisons and reports as a clean neutral, indistinguishable
  from a genuinely unremarkable observation.
- **`Z` is not rounded before use.** The returned `z_score` is the exact quotient.
  Rounding to two decimals for display is fine; deciding on the rounded value
  promotes everything in `[1.495, 1.5)` into a full-conviction trade.

The baseline itself is the caller's responsibility. The 52-week rolling window this
skill's checklist references is a convention, not a requirement — what matters is
that it is **point-in-time**: computed only from observations already knowable at
the acquisition time, and deep enough that a standard deviation means something.
Record the depth in `baseline_observation_count` so the audit note can surface it.

## 4. Look-ahead check on the baseline window

Supply `baseline_window_end_iso` — the acquisition time of the most recent
observation feeding the baseline. The engine then enforces:

| Relationship to `timestamp_iso` | Behaviour |
|---|---|
| Strictly before | Accepted |
| Equal | Warning — the scored observation is inside its own baseline, shrinking its Z-score toward zero |
| After | **Raises** — the baseline contains observations that had not yet happened |

Omitting the field skips the check entirely. That is a deliberate opt-out, not a
clean bill of health.

## 5. Directional mapping

`|Z| ≥ z_score_threshold` takes a side; anything inside the band is neutral.
The side comes from `HIGH_READING_DIRECTION`, which records the direction to take
**in the traded instrument** when the metric prints high:

| Signal type | High print → |
|---|---|
| `RETAIL_PARKING_OCCUPANCY` | +1.0 (long the retailer) |
| `FLOATING_ROOF_OIL_STORAGE` | −1.0 (short crude) |
| `AGRICULTURAL_NDVI` | −1.0 (short the crop) |

A signal type missing from that table **raises**. Falling through to a default
branch would apply one domain's economics to another — a new tanker-tracking type
would silently inherit the crop convention.

This mapping is a sign convention, not a forecast, and it is blind to consensus.
Converting it into a tradeable view requires a comparison against what the market
already expects.

## 6. Availability lag enforcement

```
tradeable_from_iso = acquisition_timestamp (UTC) + availability_lag_days
```

`availability_lag_days` must come from the vendor contract. The 2-day default is a
placeholder. Fractional days are accepted; negative values raise.

**The backtest must key on `tradeable_from_iso`, never on `timestamp_iso`.** This
is the single highest-value line in the whole pipeline. Acquisition time is when
the photons arrived; the derived number does not exist until detection,
mosaicking, QA and delivery have run.

Naive timestamps are rejected outright. Adding a multi-day lag to an unzoned time
is ambiguous by up to a day, and the ambiguity resolves in whichever direction
flatters the backtest.

Enforcing this boundary across a full dataset — including vendor restatements and
as-delivered snapshots — belongs to
`backtesting-alt-data-strategies-with-realistic-availability-lag`.

## 7. Signal strength scoring

```
confidence_pct = min(|Z| / strength_saturation_z, 1) × 100
```

An uncalibrated monotone rank in `[0, 100]`. It orders observations by how unusual
they were; it does not estimate a win probability, and `strength_saturation_z`
(default 3.0) is a display constant rather than an estimated quantity. Do not size
positions from it. For a genuinely uncertainty-aware output, see
`quantile-regression-for-uncertainty-aware-signals`.

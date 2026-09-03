"""Satellite (Earth-observation) imagery metrics -> Z-scored directional research signals.

Takes an already-processed computer-vision metric for one observation of one
asset -- retail parking-lot vehicle count, external floating-roof crude tank
fill fraction, or an agricultural NDVI composite -- normalises it against a
point-in-time historical baseline, maps the deviation to a directional bias in
the *traded* instrument, and stamps the earliest timestamp at which the signal
could honestly have been acted on.

Scope (deliberate):
- This is a **signal-research** helper, not a computer-vision pipeline. It does
  not read imagery, detect cars, segment tank shadows, or compute NDVI. It
  consumes the scalar those upstream steps produce.
- One observation in, one signal out. Rolling-baseline construction, panel
  aggregation, and cross-sectional ranking live upstream.

Limitations (documented, deliberate):
- `confidence_pct` is an uncalibrated monotone rank of |Z| in [0, 100], NOT a
  probability that the trade wins. It saturates at `strength_saturation_z`,
  which is a display constant, not an estimated quantity. Never size positions
  off it.
- The +/-1.5 Z threshold carried as the default is an engineering placeholder
  inherited from this skill's reference table, not a validated constant.
  Calibrate it against the specific vendor panel's measured signal-to-noise.
- Directional mapping is a *supply/demand sign convention*, not a forecast. It
  assumes a high reading is economically bullish (retail traffic) or bearish
  (crude inventory, crop vigour) for the named instrument. It does not know
  what the market already expects; a record parking count into a quarter where
  consensus already expected a record is not bullish.
- `availability_lag_days` produces `tradeable_from_iso` at whole/fractional-day
  resolution from the acquisition timestamp. It is a floor, not a guarantee:
  the vendor contract is the authority on actual delivery.
- The engine has no notion of vendor revisions. If the vendor restates a metric
  or its baseline, backtests must replay the as-delivered snapshot -- see
  `backtesting-alt-data-strategies-with-realistic-availability-lag`.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ImagerySignalType(str, Enum):
    RETAIL_PARKING_OCCUPANCY = "RETAIL_PARKING_OCCUPANCY"
    FLOATING_ROOF_OIL_STORAGE = "FLOATING_ROOF_OIL_STORAGE"
    AGRICULTURAL_NDVI = "AGRICULTURAL_NDVI"


# Sign convention: the direction to take in the TRADED instrument when the
# observed metric prints HIGH relative to its baseline. A low print inverts it.
# Every member of ImagerySignalType must appear here; an unmapped type raises
# rather than silently inheriting another domain's economics.
HIGH_READING_DIRECTION: Dict[ImagerySignalType, float] = {
    # More cars in the lot -> more footfall -> more revenue -> long the retailer.
    ImagerySignalType.RETAIL_PARKING_OCCUPANCY: 1.0,
    # Fuller floating-roof tanks -> more crude sitting in storage -> short crude.
    ImagerySignalType.FLOATING_ROOF_OIL_STORAGE: -1.0,
    # Greener canopy -> larger expected harvest -> more supply -> short the crop.
    ImagerySignalType.AGRICULTURAL_NDVI: -1.0,
}


def _require_finite(name: str, value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _parse_iso_utc(name: str, value: str) -> datetime:
    """
    Parse a timezone-aware ISO-8601 timestamp into UTC.

    Naive timestamps are rejected. An image acquisition time without a zone is
    ambiguous by up to a day once a delivery lag is added to it, and placing the
    signal on the correct side of a point-in-time boundary is this engine's
    whole purpose.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty ISO-8601 string, got {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware (e.g. '2026-08-05T12:00:00Z'); "
            f"a naive acquisition time cannot be lagged safely, got {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def _format_utc(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@dataclass
class SatelliteObservation:
    """
    One processed Earth-observation metric for one asset at one acquisition time.

    Attributes:
        timestamp_iso: Timezone-aware image **acquisition** time (not delivery
            time). Naive timestamps are rejected.
        observed_metric: Vehicle count, tank fill fraction, NDVI, etc.
        baseline_historical_mean / baseline_historical_std: Point-in-time
            rolling baseline. Both MUST be computed only from observations
            already knowable at `timestamp_iso`; a full-sample baseline is
            look-ahead bias. `baseline_historical_std` must be strictly
            positive -- a degenerate baseline has no Z-score.
        availability_lag_days: Capture-to-usable pipeline delay in days, taken
            from the vendor contract. Drives `tradeable_from_iso`.
        usable_pixel_fraction: Fraction of the target footprint actually
            observable (1.0 - cloud/shadow/snow/no-data). Optical sensors see
            nothing through cloud, so a 0.1 scene is noise wearing a number.
            Gated by the engine's `min_usable_pixel_fraction`.
        baseline_window_end_iso: Optional acquisition time of the most recent
            observation feeding the baseline. When supplied it is enforced
            against `timestamp_iso` to catch a baseline built with hindsight.
        baseline_observation_count: Optional number of observations behind the
            baseline, for audit. A 3-observation "52-week" baseline is not one.
    """
    timestamp_iso: str
    asset_id: str
    signal_type: ImagerySignalType
    observed_metric: float
    baseline_historical_mean: float
    baseline_historical_std: float
    availability_lag_days: float = 2.0
    usable_pixel_fraction: float = 1.0
    baseline_window_end_iso: Optional[str] = None
    baseline_observation_count: Optional[int] = None


@dataclass
class QuantitativeSatelliteSignal:
    """
    Attributes:
        z_score: Unrounded standardised deviation. Thresholding uses this value
            directly; it is deliberately not pre-rounded, because rounding
            1.4951 up to 1.50 turns a neutral observation into a full-conviction
            trade.
        trading_signal_direction: -1.0 short / 0.0 neutral / +1.0 long, in the
            traded instrument.
        confidence_pct: Uncalibrated monotone rank of |z_score| in [0, 100].
            Not a probability. Not for position sizing.
        tradeable_from_iso: Earliest timestamp at which a backtest or live
            strategy may act on this signal (acquisition + availability lag).
        quality_gate_passed: False when `usable_pixel_fraction` fell below the
            engine's configured minimum; direction is then forced to 0.0.
    """
    timestamp_iso: str
    asset_id: str
    signal_type: ImagerySignalType
    raw_metric: float
    z_score: float
    trading_signal_direction: float
    confidence_pct: float
    audit_notes: str
    tradeable_from_iso: str
    usable_pixel_fraction: float
    quality_gate_passed: bool


class SatelliteImageryBasedSignalResearchEngine:
    """
    Converts a processed satellite imagery metric into a Z-scored directional
    research signal with an explicit point-in-time availability stamp.

    Args:
        z_score_threshold: |Z| at or above which a direction is taken. The 1.5
            default matches this skill's reference table and is a placeholder,
            not a validated constant.
        strength_saturation_z: |Z| at which `confidence_pct` reaches 100. A
            display scaling constant only.
        min_usable_pixel_fraction: Cloud/obstruction gate. Defaults to 0.0
            (disabled) so the caller opts in deliberately -- there is no
            universal correct value; it depends on the target footprint and on
            how the vendor already masks. Observations below it return
            direction 0.0 with `quality_gate_passed=False`.
    """

    def __init__(
        self,
        z_score_threshold: float = 1.5,
        strength_saturation_z: float = 3.0,
        min_usable_pixel_fraction: float = 0.0,
    ) -> None:
        if _require_finite("z_score_threshold", z_score_threshold) <= 0:
            raise ValueError(f"z_score_threshold must be > 0, got {z_score_threshold!r}")
        if _require_finite("strength_saturation_z", strength_saturation_z) <= 0:
            raise ValueError(
                f"strength_saturation_z must be > 0, got {strength_saturation_z!r}")
        if not 0.0 <= _require_finite(
                "min_usable_pixel_fraction", min_usable_pixel_fraction) <= 1.0:
            raise ValueError(
                "min_usable_pixel_fraction must be within [0.0, 1.0], "
                f"got {min_usable_pixel_fraction!r}")

        self.z_score_threshold = float(z_score_threshold)
        self.strength_saturation_z = float(strength_saturation_z)
        self.min_usable_pixel_fraction = float(min_usable_pixel_fraction)

    def compute_satellite_signal(
        self,
        obs: SatelliteObservation,
    ) -> QuantitativeSatelliteSignal:
        """
        Validate the observation, Z-score it against its point-in-time baseline,
        map the deviation to a directional bias, and stamp the earliest
        tradeable timestamp.

        Raises:
            ValueError: on a non-finite metric or baseline, a non-positive
                baseline standard deviation, a negative availability lag, a
                `usable_pixel_fraction` outside [0, 1], a naive or malformed
                timestamp, or a baseline window extending past the acquisition
                time (look-ahead).
            KeyError: on a signal type with no declared directional convention.
        """
        acquisition = _parse_iso_utc("timestamp_iso", obs.timestamp_iso)

        metric = _require_finite("observed_metric", obs.observed_metric)
        mean = _require_finite("baseline_historical_mean", obs.baseline_historical_mean)
        std = _require_finite("baseline_historical_std", obs.baseline_historical_std)
        if std <= 0.0:
            raise ValueError(
                "baseline_historical_std must be > 0; a zero or negative baseline "
                "dispersion has no Z-score. Substituting 1.0 would silently rescale "
                f"the raw deviation into a full-conviction signal. Got {std!r}"
            )

        lag_days = _require_finite("availability_lag_days", obs.availability_lag_days)
        if lag_days < 0.0:
            raise ValueError(
                "availability_lag_days must be >= 0; a negative lag publishes the "
                f"observation before it was captured. Got {lag_days!r}"
            )

        usable = _require_finite("usable_pixel_fraction", obs.usable_pixel_fraction)
        if not 0.0 <= usable <= 1.0:
            raise ValueError(
                f"usable_pixel_fraction must be within [0.0, 1.0], got {usable!r}")

        if obs.baseline_window_end_iso is not None:
            baseline_end = _parse_iso_utc(
                "baseline_window_end_iso", obs.baseline_window_end_iso)
            if baseline_end > acquisition:
                raise ValueError(
                    "baseline_window_end_iso is after timestamp_iso: the baseline "
                    f"({_format_utc(baseline_end)}) contains observations that had not "
                    f"happened at acquisition ({_format_utc(acquisition)}). That is "
                    "look-ahead bias, not a rounding detail."
                )
            if baseline_end == acquisition:
                logger.warning(
                    "Baseline window for %s ends at the acquisition time; the "
                    "observation being scored is inside its own baseline, which "
                    "shrinks the Z-score toward zero.", obs.asset_id,
                )

        # ImagerySignalType is a str enum, so callers legitimately pass the bare
        # string value. Normalise before lookup: an un-normalised string would hash
        # equal to the enum member and pass the mapping check, then crash later on
        # `.value`.
        try:
            signal_type = ImagerySignalType(obs.signal_type)
        except (ValueError, TypeError) as exc:
            raise KeyError(
                f"No directional convention declared for signal type "
                f"{obs.signal_type!r}; add it to ImagerySignalType and "
                "HIGH_READING_DIRECTION rather than letting it inherit another "
                "domain's economics."
            ) from exc
        if signal_type not in HIGH_READING_DIRECTION:
            raise KeyError(
                f"No directional convention declared for signal type "
                f"{signal_type!r}; add it to HIGH_READING_DIRECTION rather than "
                "letting it inherit another domain's economics."
            )

        z_score = (metric - mean) / std

        quality_gate_passed = usable >= self.min_usable_pixel_fraction
        if not quality_gate_passed:
            direction = 0.0
        elif abs(z_score) >= self.z_score_threshold:
            direction = (
                math.copysign(1.0, z_score) * HIGH_READING_DIRECTION[signal_type]
            )
        else:
            direction = 0.0

        confidence = round(
            min(abs(z_score) / self.strength_saturation_z, 1.0) * 100.0, 2)

        try:
            tradeable_from_iso = _format_utc(acquisition + timedelta(days=lag_days))
        except (OverflowError, OSError) as exc:
            raise ValueError(
                f"availability_lag_days={lag_days!r} pushes the tradeable timestamp "
                f"outside the representable date range from {obs.timestamp_iso!r}"
            ) from exc

        gate_note = (
            "OFF" if self.min_usable_pixel_fraction <= 0.0
            else ("PASS" if quality_gate_passed else "BLOCKED")
        )
        baseline_note = (
            f"n={obs.baseline_observation_count}"
            if obs.baseline_observation_count is not None else "n=unverified"
        )
        notes = (
            f"SATELLITE SIGNAL [{signal_type.value}] ({obs.asset_id}): "
            f"Observed = {metric}, Z-Score = {round(z_score, 4)}, "
            f"Direction = {direction}, Strength = {confidence}% (uncalibrated), "
            f"Lag = {lag_days}d, Tradeable from = {tradeable_from_iso}, "
            f"Usable pixels = {usable:.2%}, Quality gate = {gate_note}, "
            f"Baseline {baseline_note}."
        )
        logger.info(notes)

        return QuantitativeSatelliteSignal(
            timestamp_iso=obs.timestamp_iso,
            asset_id=obs.asset_id,
            signal_type=signal_type,
            raw_metric=metric,
            z_score=z_score,
            trading_signal_direction=direction,
            confidence_pct=confidence,
            audit_notes=notes,
            tradeable_from_iso=tradeable_from_iso,
            usable_pixel_fraction=usable,
            quality_gate_passed=quality_gate_passed,
        )

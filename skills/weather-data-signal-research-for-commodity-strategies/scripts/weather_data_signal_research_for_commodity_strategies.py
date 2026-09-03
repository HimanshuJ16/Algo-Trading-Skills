"""Weather data signal research helpers for commodity strategies.

Standalone module - no imports from other skills or from a shared package.

Degree-day definitions follow NOAA Climate Prediction Center convention:
  * HDD / CDD use a 65 deg F base applied to the daily mean (Tmax + Tmin) / 2.
  * GDD uses a 50 deg F base with the "Modified Growing Degree Day" adjustment
    (Barger, 1969): Tmax is capped at 86 deg F and Tmin is floored at 50 deg F
    *before* the daily mean is taken, because no appreciable crop growth occurs
    outside that band.
See references/standards.md for the source citations.
"""

import logging
import math
import statistics
import datetime
from dataclasses import dataclass
from typing import List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

# Degree-day base temperatures (degrees Fahrenheit), NOAA CPC convention.
DEFAULT_HDD_CDD_BASE_F = 65.0
DEFAULT_GDD_BASE_F = 50.0
# Modified-GDD clamp band. Tmax above the cap and Tmin below the floor are
# clamped before averaging; growth is not credited outside this band.
DEFAULT_GDD_UPPER_CAP_F = 86.0
DEFAULT_GDD_LOWER_FLOOR_F = 50.0

# Seasonal day-of-year matching uses a fixed non-leap reference year so that
# leap years do not shift the day index by one.
_NON_LEAP_REFERENCE_YEAR = 2001
_DAYS_IN_NON_LEAP_YEAR = 365
_MEAN_DAYS_PER_YEAR = 365.2425


class CommoditySector(Enum):
    ENERGY_NATGAS = "ENERGY_NATGAS"             # Natural Gas (Henry Hub / TTF)
    ENERGY_POWER = "ENERGY_POWER"               # Electricity (ERCOT / PJM / CAISO)
    AGRI_CORN = "AGRI_CORN"                     # Corn Futures (CBOT, Globex code ZC)
    AGRI_SOYBEANS = "AGRI_SOYBEANS"             # Soybean Futures (CBOT, Globex code ZS)


class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class WeatherEngineError(Exception):
    """Base exception for Weather Signal Engine errors."""
    pass


def _require_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf before it can propagate silently into a trading signal.

    A NaN temperature yields a NaN degree day, a NaN Z-score, and - because every
    comparison against NaN is False - a spurious NEUTRAL signal that looks like a
    deliberate decision. Fail loudly at the boundary instead.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise WeatherEngineError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise WeatherEngineError(f"{label} must be a finite number, got {value!r}.")
    return numeric


def _require_plain_date(value: object, label: str) -> datetime.date:
    """Requires a ``datetime.date``, explicitly rejecting ``datetime.datetime``.

    ``datetime.datetime`` is a subclass of ``datetime.date``, so a naive isinstance
    check admits it - and the first ``date >= datetime`` comparison then raises an
    opaque "can't compare datetime.datetime to datetime.date" TypeError deep inside
    the baseline filter. Reject it here with an actionable message instead.
    """
    if isinstance(value, datetime.datetime):
        raise WeatherEngineError(
            f"{label} must be a datetime.date, not a datetime.datetime; call .date() first."
        )
    if not isinstance(value, datetime.date):
        raise WeatherEngineError(f"{label} must be a datetime.date, got {type(value).__name__}.")
    return value


def _seasonal_day_index(day: datetime.date) -> int:
    """Maps a date onto a 1-365 day index in a fixed non-leap reference year."""
    month, day_of_month = day.month, day.day
    if month == 2 and day_of_month == 29:       # Fold Feb 29 onto Feb 28.
        day_of_month = 28
    return datetime.date(_NON_LEAP_REFERENCE_YEAR, month, day_of_month).timetuple().tm_yday


def _circular_day_distance(index_a: int, index_b: int) -> int:
    """Distance in calendar days between two seasonal day indices, wrapping at year end."""
    raw = abs(index_a - index_b)
    return min(raw, _DAYS_IN_NON_LEAP_YEAR - raw)


@dataclass
class WeatherObservation:
    """A single station-day observation.

    :param population_weight: Relative demand weight for the station. Use census
        population (or gas/power consumption) weights for HDD/CDD, and harvested
        acreage / production weights for GDD. Weights are normalised internally,
        so any positive scale works. Must be >= 0.
    :param precipitation_inches: Recorded for downstream soil-moisture work; it is
        not consumed by the degree-day calculation.
    """
    station_id: str
    region: str
    date: datetime.date
    t_min_f: float
    t_max_f: float
    precipitation_inches: float = 0.0
    population_weight: float = 1.0


@dataclass
class DegreeDayMetrics:
    """Degree-day aggregation for a single calendar date.

    ``raw_*`` fields are the unweighted arithmetic *mean* across the stations in
    the cluster (not the sum). ``weighted_*`` fields are the weight-normalised
    means, i.e. sum(w_i * DD_i) / sum(w_i). To build the 14-day cumulative index
    the model-shift workflow uses, sum these per-day values across dates.
    """
    date: datetime.date
    raw_hdd: float
    raw_cdd: float
    raw_gdd: float
    weighted_hdd: float
    weighted_cdd: float
    weighted_gdd: float
    station_count: int


@dataclass
class ClimateBaseline:
    """Strictly-trailing climate norm for one seasonal window."""
    as_of: datetime.date
    mean: float
    std: float
    sample_size: int
    lookback_years: int
    day_window: int


@dataclass
class WeatherSignalResult:
    symbol: str
    sector: CommoditySector
    signal_date: datetime.date
    anomaly_zscore: float
    direction: SignalDirection
    confidence_score: float                     # 0.0 to 1.0 scale; 0.0 when NEUTRAL
    rationale: str


class WeatherCommoditySignalEngine:
    """
    Institutional Weather Data Signal Research Engine for Commodity Strategies.

    Ingests weather station observations and model forecasts (GFS / ECMWF), calculates Heating Degree Days (HDD),
    Cooling Degree Days (CDD), and Modified Growing Degree Days (GDD), computes population-weighted regional
    aggregations, derives strictly-trailing historical anomaly Z-scores, and outputs quantitative directional
    trading signals for commodities.
    """

    def __init__(self, zscore_threshold: float = 1.5):
        """
        :param zscore_threshold: Z-score standard deviation anomaly threshold to trigger LONG/SHORT signals.
            Must be strictly positive - a zero or negative threshold would fire a directional signal on
            every observation and make the confidence scaling undefined (division by zero).
        """
        zscore_threshold = _require_finite(zscore_threshold, "zscore_threshold")
        if zscore_threshold <= 0:
            raise WeatherEngineError(
                f"zscore_threshold must be strictly positive, got {zscore_threshold}."
            )
        self.zscore_threshold = zscore_threshold
        logger.info(f"Initialized Weather Commodity Signal Engine (Z-Score Threshold = +/-{zscore_threshold})")

    def calculate_degree_days(
        self,
        observations: List[WeatherObservation],
        base_temp_f: float = DEFAULT_HDD_CDD_BASE_F,
        gdd_base_f: float = DEFAULT_GDD_BASE_F,
        gdd_upper_cap_f: float = DEFAULT_GDD_UPPER_CAP_F,
        gdd_lower_floor_f: float = DEFAULT_GDD_LOWER_FLOOR_F,
    ) -> DegreeDayMetrics:
        """
        Calculates raw and weight-normalised Heating Degree Days (HDD), Cooling Degree Days (CDD),
        and Modified Growing Degree Days (GDD) across a cluster of weather stations for ONE date.

        HDD = max(0, base - (Tmax + Tmin) / 2)
        CDD = max(0, (Tmax + Tmin) / 2 - base)
        GDD = max(0, (min(Tmax, cap) + max(Tmin, floor)) / 2 - gdd_base)   [NOAA CPC / Barger 1969]

        Pass ``gdd_upper_cap_f=math.inf`` and ``gdd_lower_floor_f=-math.inf`` to recover the
        unclamped simple-average GDD variant used by some agronomic models.

        Every observation must carry the same date: aggregating several dates into one
        DegreeDayMetrics would silently produce a meaningless index stamped with one of them.
        """
        if not observations:
            raise WeatherEngineError("Observations list cannot be empty.")

        base_temp_f = _require_finite(base_temp_f, "base_temp_f")
        gdd_base_f = _require_finite(gdd_base_f, "gdd_base_f")
        if math.isnan(gdd_upper_cap_f) or math.isnan(gdd_lower_floor_f):
            raise WeatherEngineError("GDD cap and floor must not be NaN.")
        if gdd_lower_floor_f > gdd_upper_cap_f:
            raise WeatherEngineError(
                f"GDD lower floor ({gdd_lower_floor_f}) cannot exceed upper cap ({gdd_upper_cap_f})."
            )

        target_date = observations[0].date
        seen_station_ids = set()

        for obs in observations:
            if obs.date != target_date:
                raise WeatherEngineError(
                    f"All observations must share one date; got {obs.date} for station "
                    f"'{obs.station_id}' but expected {target_date}. Group observations by "
                    f"date before aggregating."
                )
            if obs.station_id in seen_station_ids:
                raise WeatherEngineError(
                    f"Duplicate station_id '{obs.station_id}' for {target_date}; a repeated "
                    f"station would be double counted in the weighted aggregate."
                )
            seen_station_ids.add(obs.station_id)

            _require_finite(obs.t_min_f, f"t_min_f for station '{obs.station_id}'")
            _require_finite(obs.t_max_f, f"t_max_f for station '{obs.station_id}'")
            weight = _require_finite(
                obs.population_weight, f"population_weight for station '{obs.station_id}'"
            )
            if weight < 0:
                raise WeatherEngineError(
                    f"population_weight for station '{obs.station_id}' must be non-negative, got {weight}."
                )
            if obs.t_max_f < obs.t_min_f:
                raise WeatherEngineError(
                    f"t_max_f ({obs.t_max_f}) is below t_min_f ({obs.t_min_f}) for station "
                    f"'{obs.station_id}' on {target_date}; the station feed is transposed or corrupt."
                )

        total_weight = sum(o.population_weight for o in observations)
        if total_weight <= 0:
            raise WeatherEngineError("Total population weight must be positive.")

        sum_raw_hdd = 0.0
        sum_raw_cdd = 0.0
        sum_raw_gdd = 0.0
        sum_weighted_hdd = 0.0
        sum_weighted_cdd = 0.0
        sum_weighted_gdd = 0.0

        for obs in observations:
            t_mean = (obs.t_max_f + obs.t_min_f) / 2.0

            # HDD = max(0, 65 - T_mean)
            hdd = max(0.0, base_temp_f - t_mean)
            # CDD = max(0, T_mean - 65)
            cdd = max(0.0, t_mean - base_temp_f)
            # Modified GDD: clamp Tmax to the cap and Tmin to the floor, then average.
            gdd_t_mean = (
                min(obs.t_max_f, gdd_upper_cap_f) + max(obs.t_min_f, gdd_lower_floor_f)
            ) / 2.0
            gdd = max(0.0, gdd_t_mean - gdd_base_f)

            w = obs.population_weight / total_weight

            sum_raw_hdd += hdd
            sum_raw_cdd += cdd
            sum_raw_gdd += gdd
            sum_weighted_hdd += hdd * w
            sum_weighted_cdd += cdd * w
            sum_weighted_gdd += gdd * w

        n = len(observations)

        logger.info(
            f"Degree Days [{target_date}] across {n} stations: Weighted HDD={sum_weighted_hdd:.2f}, "
            f"Weighted CDD={sum_weighted_cdd:.2f}, Weighted GDD={sum_weighted_gdd:.2f}"
        )

        return DegreeDayMetrics(
            date=target_date,
            raw_hdd=round(sum_raw_hdd / n, 2),
            raw_cdd=round(sum_raw_cdd / n, 2),
            raw_gdd=round(sum_raw_gdd / n, 2),
            weighted_hdd=round(sum_weighted_hdd, 2),
            weighted_cdd=round(sum_weighted_cdd, 2),
            weighted_gdd=round(sum_weighted_gdd, 2),
            station_count=n,
        )

    def compute_climate_baseline(
        self,
        history: List[Tuple[datetime.date, float]],
        as_of: datetime.date,
        lookback_years: int = 10,
        day_window: int = 7,
        min_observations: int = 5,
    ) -> ClimateBaseline:
        """
        Builds the climate norm (mean, sample standard deviation) for the seasonal window
        around ``as_of``, using ONLY observations dated strictly before ``as_of``.

        This is the look-ahead guard for the anomaly Z-score. Computing mu and sigma over a
        full historical series - including the point being scored, or any point after it -
        leaks future information into every backtested signal. Observations dated on or after
        ``as_of`` are excluded outright, never merely down-weighted.

        Matching is seasonal: a historical date qualifies when it falls within +/- ``day_window``
        calendar days of the ``as_of`` day-of-year (wrapping across the year boundary) and no
        earlier than ``lookback_years`` before it. Sigma is the sample standard deviation
        (ddof=1), the correct estimator for a baseline inferred from a finite sample.

        :param history: (date, metric value) pairs for the same metric being scored, e.g.
            daily population-weighted HDD. Order does not matter; entries outside the window
            are ignored.
        :raises WeatherEngineError: if fewer than ``min_observations`` qualify, or if the
            qualifying sample has zero dispersion, which makes the Z-score undefined.
        """
        if lookback_years < 1:
            raise WeatherEngineError(f"lookback_years must be >= 1, got {lookback_years}.")
        if not 0 <= day_window <= 182:
            raise WeatherEngineError(f"day_window must be between 0 and 182 days, got {day_window}.")
        if min_observations < 2:
            raise WeatherEngineError(
                f"min_observations must be >= 2 to estimate a sample standard deviation, "
                f"got {min_observations}."
            )
        as_of = _require_plain_date(as_of, "as_of")

        earliest_allowed = as_of - datetime.timedelta(
            days=round(lookback_years * _MEAN_DAYS_PER_YEAR)
        )
        target_day_index = _seasonal_day_index(as_of)

        sample: List[float] = []
        for entry_date, entry_value in history:
            _require_plain_date(entry_date, "history date")
            if entry_date >= as_of:                 # Strict look-ahead guard.
                continue
            if entry_date < earliest_allowed:
                continue
            if _circular_day_distance(_seasonal_day_index(entry_date), target_day_index) > day_window:
                continue
            sample.append(_require_finite(entry_value, f"history value for {entry_date}"))

        if len(sample) < min_observations:
            raise WeatherEngineError(
                f"Only {len(sample)} historical observations fall within +/-{day_window} days of "
                f"{as_of} across the prior {lookback_years} years; {min_observations} required. "
                f"Widen day_window or lookback_years, or suppress the signal for this date."
            )

        mean = statistics.fmean(sample)
        std = statistics.stdev(sample)
        if std <= 0:
            raise WeatherEngineError(
                f"Baseline standard deviation is zero for {as_of} (all {len(sample)} sampled "
                f"values identical); the anomaly Z-score is undefined."
            )

        logger.info(
            f"Climate baseline [{as_of}]: mean={mean:.2f}, std={std:.2f}, n={len(sample)} "
            f"(lookback={lookback_years}y, window=+/-{day_window}d)"
        )

        return ClimateBaseline(
            as_of=as_of,
            mean=mean,
            std=std,
            sample_size=len(sample),
            lookback_years=lookback_years,
            day_window=day_window,
        )

    def compute_anomaly_zscore(
        self, current_value: float, baseline_mean: float, baseline_std: float
    ) -> float:
        """
        Calculates the standard deviation Z-score relative to the historical baseline climate norm.

        Returns the full-precision value. It is deliberately NOT rounded: rounding before the
        threshold comparison promotes a near miss such as Z = 1.496 to exactly 1.50 and fires a
        directional signal at a threshold that was never actually met. Round for display only.
        """
        current_value = _require_finite(current_value, "current_value")
        baseline_mean = _require_finite(baseline_mean, "baseline_mean")
        baseline_std = _require_finite(baseline_std, "baseline_std")
        if baseline_std <= 0:
            raise WeatherEngineError("Baseline standard deviation must be positive.")

        return (current_value - baseline_mean) / baseline_std

    def generate_commodity_trade_signal(
        self,
        sector: CommoditySector,
        symbol: str,
        current_value: float,
        baseline_mean: float,
        baseline_std: float,
        signal_date: datetime.date,
    ) -> WeatherSignalResult:
        """
        Maps a weather anomaly Z-score to a commodity directional trading signal.

        IMPORTANT - metric orientation. ``current_value``, ``baseline_mean`` and ``baseline_std``
        must all describe the SAME price-bullish-oriented weather metric, that is, one where a
        higher value implies a tighter physical balance for that commodity:

        - ENERGY_NATGAS  -> population-weighted HDD (winter) or CDD (summer). Higher = more demand.
        - ENERGY_POWER   -> population-weighted CDD (summer peak) or HDD (winter peak).
        - AGRI_CORN / AGRI_SOYBEANS -> a crop-STRESS metric such as EDDI, a soil-moisture deficit,
          or accumulated heat-stress degree days above the crop's optimum. Higher = more stress.

        Do NOT pass raw Growing Degree Days for the agricultural sectors. A high GDD anomaly means
        accelerated crop development, which is not by itself bullish or bearish; feeding it in here
        would invert the intended signal. GDD belongs in phenology and maturity modelling, not in
        this directional mapping.

        Rules:
        - Energy: Z >= +threshold -> LONG (cold wave / heatwave demand spike);
                  Z <= -threshold -> SHORT (unusually mild, demand destruction).
        - Agri:   Z >= +threshold -> LONG (crop stress, yield reduction, supply deficit);
                  Z <= -threshold -> SHORT (benign conditions, surplus yield).

        ``confidence_score`` is a bounded heuristic, min(1.0, |Z| / (2 * threshold)), reaching 1.0
        at twice the trigger threshold. It is 0.0 whenever the direction is NEUTRAL, so a position
        sizer keyed on confidence cannot size an untriggered signal.
        """
        if not isinstance(sector, CommoditySector):
            raise WeatherEngineError(
                f"sector must be a CommoditySector, got {type(sector).__name__} ({sector!r})."
            )
        if not isinstance(signal_date, datetime.date):
            raise WeatherEngineError(
                f"signal_date must be a datetime.date, got {type(signal_date).__name__}."
            )

        z_score = self.compute_anomaly_zscore(current_value, baseline_mean, baseline_std)

        direction = SignalDirection.NEUTRAL

        if sector in (CommoditySector.ENERGY_NATGAS, CommoditySector.ENERGY_POWER):
            if z_score >= self.zscore_threshold:
                direction = SignalDirection.LONG
                rationale = f"Severe Cold/Heat Anomaly (Z = {z_score:+.2f} >= +{self.zscore_threshold}). High heating/cooling demand expected."
            elif z_score <= -self.zscore_threshold:
                direction = SignalDirection.SHORT
                rationale = f"Unusually Mild Weather Anomaly (Z = {z_score:+.2f} <= -{self.zscore_threshold}). Low demand expected."
            else:
                rationale = f"Weather within normal range (Z = {z_score:+.2f}). Neutral signal."

        elif sector in (CommoditySector.AGRI_CORN, CommoditySector.AGRI_SOYBEANS):
            if z_score >= self.zscore_threshold:
                direction = SignalDirection.LONG
                rationale = f"Extreme Drought/Heat Stress Anomaly (Z = {z_score:+.2f}). Crop yield reduction & supply deficit risk."
            elif z_score <= -self.zscore_threshold:
                direction = SignalDirection.SHORT
                rationale = f"Favorable Crop Weather Anomaly (Z = {z_score:+.2f}). Ideal growing conditions & surplus yield."
            else:
                rationale = f"Normal agricultural growing weather (Z = {z_score:+.2f}). Neutral signal."

        else:
            # Defensive: a newly added CommoditySector member must be given an explicit mapping
            # here rather than falling through to an unbound rationale or a silent NEUTRAL.
            raise WeatherEngineError(
                f"No directional signal mapping defined for sector {sector.value}."
            )

        confidence = (
            0.0
            if direction is SignalDirection.NEUTRAL
            else round(min(1.0, abs(z_score) / (self.zscore_threshold * 2.0)), 2)
        )

        logger.info(
            f"Weather Trade Signal [{symbol} - {sector.value}]: Z={z_score:+.2f} -> "
            f"Direction={direction.value}, Confidence={confidence:.2f}"
        )

        return WeatherSignalResult(
            symbol=symbol,
            sector=sector,
            signal_date=signal_date,
            anomaly_zscore=z_score,
            direction=direction,
            confidence_score=confidence,
            rationale=rationale,
        )

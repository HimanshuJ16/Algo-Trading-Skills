import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class CommoditySector(Enum):
    ENERGY_NATGAS = "ENERGY_NATGAS"             # Natural Gas (Henry Hub / TTF)
    ENERGY_POWER = "ENERGY_POWER"               # Electricity (ERCOT / PJM / CAISO)
    AGRI_CORN = "AGRI_CORN"                     # Corn Futures (CBOT)
    AGRI_SOYBEANS = "AGRI_SOYBEANS"             # Soybean Futures (CBOT)


class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class WeatherEngineError(Exception):
    """Base exception for Weather Signal Engine errors."""
    pass


@dataclass
class WeatherObservation:
    station_id: str
    region: str
    date: datetime.date
    t_min_f: float
    t_max_f: float
    precipitation_inches: float = 0.0
    population_weight: float = 1.0


@dataclass
class DegreeDayMetrics:
    date: datetime.date
    raw_hdd: float
    raw_cdd: float
    raw_gdd: float
    weighted_hdd: float
    weighted_cdd: float


@dataclass
class WeatherSignalResult:
    symbol: str
    sector: CommoditySector
    signal_date: datetime.date
    anomaly_zscore: float
    direction: SignalDirection
    confidence_score: float                     # 0.0 to 1.0 scale
    rationale: str


class WeatherCommoditySignalEngine:
    """
    Institutional Weather Data Signal Research Engine for Commodity Strategies.
    
    Ingests weather station observations and model forecasts (GFS / ECMWF), calculates Heating Degree Days (HDD),
    Cooling Degree Days (CDD), and Growing Degree Days (GDD), computes population-weighted regional aggregations,
    derives 10-year historical anomaly Z-scores, and outputs quantitative directional trading signals for commodities.
    """

    def __init__(self, zscore_threshold: float = 1.5):
        """
        :param zscore_threshold: Z-score standard deviation anomaly threshold to trigger LONG/SHORT signals.
        """
        self.zscore_threshold = zscore_threshold
        logger.info(f"Initialized Weather Commodity Signal Engine (Z-Score Threshold = ±{zscore_threshold})")

    def calculate_degree_days(
        self, observations: List[WeatherObservation], base_temp_f: float = 65.0, gdd_base_f: float = 50.0
    ) -> DegreeDayMetrics:
        """
        Calculates raw and population-weighted Heating Degree Days (HDD), Cooling Degree Days (CDD),
        and Growing Degree Days (GDD) across a cluster of weather stations.
        """
        if not observations:
            raise WeatherEngineError("Observations list cannot be empty.")

        total_weight = sum(o.population_weight for o in observations)
        if total_weight <= 0:
            raise WeatherEngineError("Total population weight must be positive.")

        sum_raw_hdd = 0.0
        sum_raw_cdd = 0.0
        sum_raw_gdd = 0.0
        sum_weighted_hdd = 0.0
        sum_weighted_cdd = 0.0

        target_date = observations[0].date

        for obs in observations:
            t_mean = (obs.t_max_f + obs.t_min_f) / 2.0

            # HDD = max(0, 65 - T_mean)
            hdd = max(0.0, base_temp_f - t_mean)
            # CDD = max(0, T_mean - 65)
            cdd = max(0.0, t_mean - base_temp_f)
            # GDD = max(0, T_mean - 50)
            gdd = max(0.0, t_mean - gdd_base_f)

            w = obs.population_weight / total_weight

            sum_raw_hdd += hdd
            sum_raw_cdd += cdd
            sum_raw_gdd += gdd
            sum_weighted_hdd += hdd * w
            sum_weighted_cdd += cdd * w

        n = len(observations)

        logger.info(
            f"Degree Days [{target_date}]: Weighted HDD={sum_weighted_hdd:.2f}, "
            f"Weighted CDD={sum_weighted_cdd:.2f}, Raw GDD={sum_raw_gdd / n:.2f}"
        )

        return DegreeDayMetrics(
            date=target_date,
            raw_hdd=round(sum_raw_hdd / n, 2),
            raw_cdd=round(sum_raw_cdd / n, 2),
            raw_gdd=round(sum_raw_gdd / n, 2),
            weighted_hdd=round(sum_weighted_hdd, 2),
            weighted_cdd=round(sum_weighted_cdd, 2),
        )

    def compute_anomaly_zscore(
        self, current_value: float, baseline_mean: float, baseline_std: float
    ) -> float:
        """
        Calculates standard deviation Z-score relative to 10-year historical baseline climate norm.
        """
        if baseline_std <= 0:
            raise WeatherEngineError("Baseline standard deviation must be positive.")

        z_score = (current_value - baseline_mean) / baseline_std
        return round(z_score, 2)

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
        Maps weather anomaly Z-score to commodity directional trading signals.
        
        Rules:
        - Energy (NatGas/Power): Cold wave (High HDD Z) or Heatwave (High CDD Z) -> LONG (High demand)
        - Agri (Corn/Soybeans): Severe drought / excessive heat -> LONG (Yield reduction / supply deficit)
        """
        z_score = self.compute_anomaly_zscore(current_value, baseline_mean, baseline_std)

        direction = SignalDirection.NEUTRAL
        confidence = round(min(1.0, abs(z_score) / (self.zscore_threshold * 2.0)), 2)

        if sector in (CommoditySector.ENERGY_NATGAS, CommoditySector.ENERGY_POWER):
            if z_score >= self.zscore_threshold:
                direction = SignalDirection.LONG
                rationale = f"Severe Cold/Heat Anomaly (Z = +{z_score:.2f} >= +{self.zscore_threshold}). High heating/cooling demand expected."
            elif z_score <= -self.zscore_threshold:
                direction = SignalDirection.SHORT
                rationale = f"Unusually Mild Weather Anomaly (Z = {z_score:.2f} <= -{self.zscore_threshold}). Low demand expected."
            else:
                rationale = f"Weather within normal range (Z = {z_score:.2f}). Neutral signal."

        elif sector in (CommoditySector.AGRI_CORN, CommoditySector.AGRI_SOYBEANS):
            if z_score >= self.zscore_threshold:
                direction = SignalDirection.LONG
                rationale = f"Extreme Drought/Heat Anomaly (Z = +{z_score:.2f}). Crop yield reduction & supply deficit risk."
            elif z_score <= -self.zscore_threshold:
                direction = SignalDirection.SHORT
                rationale = f"Favorable Crop Weather Anomaly (Z = {z_score:.2f}). Ideal growing conditions & surplus yield."
            else:
                rationale = f"Normal agricultural growing weather (Z = {z_score:.2f}). Neutral signal."

        logger.info(
            f"Weather Trade Signal [{symbol} - {sector.value}]: Z={z_score:+.2f} -> Direction={direction.value}, Confidence={confidence:.2f}"
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


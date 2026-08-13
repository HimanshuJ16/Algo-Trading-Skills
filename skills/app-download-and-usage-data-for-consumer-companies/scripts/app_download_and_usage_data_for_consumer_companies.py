"""
app-download-and-usage-data-for-consumer-companies:
Signal generation engine deriving stickiness (DAU/MAU) and churn warnings from
mobile app alternative data (Downloads, DAU, MAU).

The engine is a pure, deterministic transformation of a single
``AppUsageDataPoint`` (or a batch of them) into an ``AppUsageSignal``. It does
not perform network I/O, does not mutate its inputs, and does not depend on any
external state, which makes it safe to run inside backtest replays as well as
live signal pipelines.

Regulatory context: mobile app alternative data (vendor estimates of downloads
and active users) was the subject of the SEC's first enforcement action against
an alternative data provider for securities fraud (App Annie Inc., SEC Release
No. 34-92975, Sept. 14, 2021). Subscribers must perform vendor diligence and
confirm MNPI/MAR controls *before* consuming the resulting signals. See the
``insider-trading-controls-for-alternative-data-usage`` skill and the
``MNPI & Compliance`` section of ``references/standards.md``.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from typing import Iterable, List, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "AppUsageDataPoint",
    "AppUsageSignal",
    "EngineConfig",
    "AppUsageSignalEngine",
]


# --- Engine defaults ---------------------------------------------------------
# A stickiness ratio (DAU/MAU) at or above this value is treated as
# "world-class" habitual engagement (top-tier social / messaging apps).
DEFAULT_WORLD_CLASS_THRESHOLD: float = 0.50

# A stickiness ratio below this value indicates low daily engagement.
DEFAULT_LOW_STICKINESS_THRESHOLD: float = 0.20

# Daily downloads expressed as a fraction of MAU at or above which an app is
# considered to be in aggressive acquisition mode. Combined with low stickiness
# this defines the "Leaky Bucket" churn-risk condition.
DEFAULT_HIGH_ACQUISITION_FRACTION: float = 0.10


@dataclasses.dataclass(frozen=True)
class EngineConfig:
    """Thresholds governing signal classification.

    All thresholds are inclusive/exclusive as documented on each field so that
    the engine behaves deterministically at exact boundary values.
    """

    # Stickiness >= this value => world-class (inclusive).
    world_class_threshold: float = DEFAULT_WORLD_CLASS_THRESHOLD
    # Stickiness < this value => low engagement (exclusive upper bound).
    low_stickiness_threshold: float = DEFAULT_LOW_STICKINESS_THRESHOLD
    # downloads >= this fraction of MAU => high acquisition (inclusive).
    high_acquisition_fraction: float = DEFAULT_HIGH_ACQUISITION_FRACTION

    def __post_init__(self) -> None:
        if not 0.0 < self.world_class_threshold <= 1.0:
            raise ValueError("world_class_threshold must be in (0.0, 1.0].")
        if not 0.0 <= self.low_stickiness_threshold < self.world_class_threshold:
            raise ValueError(
                "low_stickiness_threshold must be in [0.0, world_class_threshold)."
            )
        if not 0.0 <= self.high_acquisition_fraction <= 1.0:
            raise ValueError("high_acquisition_fraction must be in [0.0, 1.0].")


@dataclasses.dataclass(frozen=True)
class AppUsageDataPoint:
    """A single panel observation for one ticker on one date.

    ``date`` is the event date (the day the metrics pertain to). Point-in-time
    alignment -- shifting this date forward by the vendor's publication lag so
    that the signal is only usable on the date the data actually became
    available -- is the caller's responsibility and is handled by the
    ``alternative-data-feature-integration`` skill before ingestion here.
    """

    ticker: str
    date: datetime
    downloads: int
    dau: int  # Daily Active Users
    mau: int  # Monthly Active Users


@dataclasses.dataclass(frozen=True)
class AppUsageSignal:
    """Output signal for one ``AppUsageDataPoint``."""

    ticker: str
    stickiness_ratio: float
    is_world_class: bool
    churn_risk_warning: bool
    signal_summary: str


class AppUsageSignalEngine:
    """Evaluates raw app metrics to detect fundamental engagement vs "vanity"
    download metrics.

    The engine is stateless: ``process`` may be called concurrently from
    multiple threads provided each call receives its own ``AppUsageDataPoint``.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()

    def process(self, data: AppUsageDataPoint) -> AppUsageSignal:
        """Transform a single data point into an ``AppUsageSignal``.

        Raises:
            ValueError: if any field is structurally invalid (empty ticker,
                negative counts, non-positive MAU, or DAU so large it cannot
                be clamped).
            TypeError: if ``data`` is not an ``AppUsageDataPoint``.
        """
        if not isinstance(data, AppUsageDataPoint):
            raise TypeError("data must be an AppUsageDataPoint instance.")
        self._validate(data)

        # DAU > MAU is mathematically impossible for a genuine panel; treat it
        # as a vendor/panel anomaly. Clamp locally WITHOUT mutating the input.
        effective_dau = data.dau
        if effective_dau > data.mau:
            logger.warning(
                "Data anomaly: DAU (%d) exceeds MAU (%d) for %s on %s; "
                "clamping DAU to MAU for stickiness calculation.",
                data.dau, data.mau, data.ticker, data.date.isoformat(),
            )
            effective_dau = data.mau

        stickiness = effective_dau / data.mau

        is_world_class = stickiness >= self.config.world_class_threshold
        is_high_acquisition = (
            data.downloads >= data.mau * self.config.high_acquisition_fraction
        )
        churn_risk_warning = (
            stickiness < self.config.low_stickiness_threshold and is_high_acquisition
        )

        if is_world_class:
            summary = (
                f"Strong structural engagement (Stickiness: {stickiness:.1%}). "
                f"Sustainable growth."
            )
        elif churn_risk_warning:
            summary = (
                f"LEAKY BUCKET: High downloads but poor retention "
                f"(Stickiness: {stickiness:.1%}). High churn risk."
            )
        else:
            summary = f"Average engagement (Stickiness: {stickiness:.1%})."

        logger.info("[%s] %s", data.ticker, summary)

        return AppUsageSignal(
            ticker=data.ticker,
            stickiness_ratio=stickiness,
            is_world_class=is_world_class,
            churn_risk_warning=churn_risk_warning,
            signal_summary=summary,
        )

    def process_many(
        self, data_points: Sequence[AppUsageDataPoint]
    ) -> List[AppUsageSignal]:
        """Process an ordered batch of data points.

        A single invalid point raises ``ValueError`` (fail-fast) rather than
        silently dropping it, because a partial batch in a signal pipeline
        typically indicates an upstream data-quality problem that should
        surface immediately.
        """
        if not isinstance(data_points, Iterable):
            raise TypeError("data_points must be an iterable of AppUsageDataPoint.")
        return [self.process(dp) for dp in data_points]

    @staticmethod
    def _validate(data: AppUsageDataPoint) -> None:
        if not data.ticker or not data.ticker.strip():
            raise ValueError("ticker must be a non-empty string.")
        if data.downloads < 0:
            raise ValueError(f"downloads must be >= 0 for {data.ticker}.")
        if data.dau < 0:
            raise ValueError(f"dau must be >= 0 for {data.ticker}.")
        if data.mau <= 0:
            raise ValueError(
                f"mau must be strictly positive to calculate stickiness for "
                f"{data.ticker}."
            )

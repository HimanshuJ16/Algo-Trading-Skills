"""Adaptive execution parameters for volatility shock protection."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import logging
import math
from numbers import Real
from typing import Any


logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Regime label for one evaluation.

    ``UNKNOWN`` is the state of an engine that has not yet evaluated anything,
    or whose most recent evaluation did not complete. It is never carried on an ``ExecutionParameters`` result --
    ``evaluate`` raises instead of returning one -- and exists so that a
    monitor reading ``engine.current_regime`` after a fault cannot observe a
    stale ``NORMAL`` left behind by an earlier successful call.
    """

    UNKNOWN = "UNKNOWN"
    NORMAL = "NORMAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    CRITICAL_SHOCK = "CRITICAL_SHOCK"


class MarketDataValidationError(ValueError):
    """Raised when the volatility input cannot support a safe decision."""


def _finite_real(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")

    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")
    return numeric_value


@dataclass
class AdaptiveVolatilityConfig:
    """Calibrated bounds for one instrument, session, and volatility estimator.

    ``enabled`` is a bypass, not a safe default. When it is ``False`` the
    overlay returns the base parameters unchanged, reports ``NORMAL``, and
    performs **no** volatility validation at all -- missing, stale, or
    non-numeric input is not rejected because it is never read. Disable it only
    when an independent control provides the volatility protection.

    ``limit_offset_bps_*`` are distances *away from the aggressive side* of the
    caller's reference price: a buy limit at ``ref * (1 - bps / 10_000)``, a
    sell limit at ``ref * (1 + bps / 10_000)``. A larger offset is therefore
    more passive, which is why the high-volatility offset exceeds the normal
    one. An EMS that reads the offset as aggressiveness instead will chase a
    dislocating book, inverting the protection this overlay exists to provide.

    Thresholds are illustrative defaults, not regulatory or venue settings; see
    ``references/standards.md`` for the calibration requirements.
    """

    enabled: bool = True
    base_participation_rate: float = 0.10
    base_child_order_size: int = 100
    volatility_threshold_high: float = 2.0
    volatility_threshold_critical: float = 5.0
    limit_offset_bps_normal: int = 5
    limit_offset_bps_high: int = 15

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")

        participation_rate = _finite_real(
            self.base_participation_rate, "base_participation_rate"
        )
        if not 0.0 <= participation_rate <= 1.0:
            raise ValueError("base_participation_rate must be between 0 and 1")

        if (
            isinstance(self.base_child_order_size, bool)
            or not isinstance(self.base_child_order_size, int)
            or self.base_child_order_size < 1
        ):
            raise ValueError("base_child_order_size must be a positive integer")

        high_threshold = _finite_real(
            self.volatility_threshold_high, "volatility_threshold_high"
        )
        critical_threshold = _finite_real(
            self.volatility_threshold_critical, "volatility_threshold_critical"
        )
        if high_threshold < 0.0:
            raise ValueError("volatility_threshold_high must be non-negative")
        if critical_threshold <= high_threshold:
            raise ValueError(
                "volatility_threshold_critical must be greater than "
                "volatility_threshold_high"
            )

        for field_name in ("limit_offset_bps_normal", "limit_offset_bps_high"):
            offset = getattr(self, field_name)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ExecutionParameters:
    """One child-order decision.

    On a ``CRITICAL_SHOCK`` every numeric field is zeroed. Those zeros are halt
    sentinels, not an order instruction: a zero ``limit_offset_bps`` is the
    most aggressive value under the convention documented on
    ``AdaptiveVolatilityConfig``, so a caller that ignored ``halt_trading`` and
    priced off these fields would do the opposite of halting. Branch on
    ``halt_trading`` first.
    """

    participation_rate: float
    child_order_size: int
    limit_offset_bps: int
    halt_trading: bool
    regime: MarketRegime = MarketRegime.NORMAL


class AdaptiveExecutionUnderVolatilitySpikesEngine:
    """Return bounded execution parameters for the current volatility regime.

    The engine does not submit or cancel orders. The caller must treat a halt
    decision, or a validation exception, as a fail-closed signal and enforce
    cancellation, risk, and venue controls in the execution management system.

    ``current_regime`` is single-writer instance state describing this
    engine's last evaluation only. Use one instance per instrument and parent
    order: a shared instance reports whichever symbol evaluated most recently,
    and concurrent ``evaluate`` calls on one instance race. The authoritative
    per-decision value is ``ExecutionParameters.regime`` on the returned
    object, which is safe to pass across threads.
    """

    def __init__(self, config: AdaptiveVolatilityConfig) -> None:
        if not isinstance(config, AdaptiveVolatilityConfig):
            raise TypeError("config must be an AdaptiveVolatilityConfig")
        self.config = config
        # A new engine has classified nothing; it must not report NORMAL until
        # an evaluation has actually succeeded.
        self.current_regime = MarketRegime.UNKNOWN

    def _determine_regime(self, current_volatility: float) -> MarketRegime:
        if current_volatility >= self.config.volatility_threshold_critical:
            return MarketRegime.CRITICAL_SHOCK
        if current_volatility >= self.config.volatility_threshold_high:
            return MarketRegime.HIGH_VOLATILITY
        return MarketRegime.NORMAL

    def _normal_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            participation_rate=self.config.base_participation_rate,
            child_order_size=self.config.base_child_order_size,
            limit_offset_bps=self.config.limit_offset_bps_normal,
            halt_trading=False,
            regime=MarketRegime.NORMAL,
        )

    def evaluate(self, market_data: Mapping[str, Any]) -> ExecutionParameters:
        """Evaluate one snapshot of market data.

        Enabled evaluations require a finite numeric ``current_volatility``.
        Missing or invalid data raises ``MarketDataValidationError`` instead of
        silently treating the market as normal, and leaves ``current_regime``
        at ``UNKNOWN``.

        A disabled config short-circuits before any of that: see
        ``AdaptiveVolatilityConfig``.
        """
        # Any exit from here on that is not a completed classification must
        # leave UNKNOWN behind, so a fault can never be read as the NORMAL
        # recorded by an earlier call.
        self.current_regime = MarketRegime.UNKNOWN

        if not isinstance(market_data, Mapping):
            raise TypeError("market_data must be a mapping")

        # Re-validated every call: the config is a mutable dataclass, so a
        # runtime edit or a partially applied reload must fail closed here
        # rather than route on out-of-range bounds.
        self.config.validate()
        if not self.config.enabled:
            self.current_regime = MarketRegime.NORMAL
            return self._normal_parameters()

        if "current_volatility" not in market_data:
            raise MarketDataValidationError(
                "market_data must contain current_volatility"
            )

        try:
            current_volatility = _finite_real(
                market_data["current_volatility"], "current_volatility"
            )
        except (TypeError, ValueError) as exc:
            raise MarketDataValidationError(str(exc)) from exc

        self.current_regime = self._determine_regime(current_volatility)
        logger.debug(
            "Current market regime: %s (volatility=%.2f)",
            self.current_regime.name,
            current_volatility,
        )

        if self.current_regime == MarketRegime.CRITICAL_SHOCK:
            logger.warning(
                "Critical volatility shock detected; execution must halt"
            )
            return ExecutionParameters(
                participation_rate=0.0,
                child_order_size=0,
                limit_offset_bps=0,
                halt_trading=True,
                regime=MarketRegime.CRITICAL_SHOCK,
            )

        if self.current_regime == MarketRegime.HIGH_VOLATILITY:
            return ExecutionParameters(
                participation_rate=self.config.base_participation_rate * 0.5,
                child_order_size=max(
                    1, int(self.config.base_child_order_size * 0.5)
                ),
                limit_offset_bps=self.config.limit_offset_bps_high,
                halt_trading=False,
                regime=MarketRegime.HIGH_VOLATILITY,
            )

        return self._normal_parameters()

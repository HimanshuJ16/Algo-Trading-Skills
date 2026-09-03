"""Liquidity-tier execution defaults with validation and calibration metadata."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import logging
import math
from numbers import Real
from types import MappingProxyType
from typing import Optional


logger = logging.getLogger(__name__)

_VALID_ALGO_TYPES = frozenset({"TWAP", "VWAP", "IS"})


def _finite_real(value: object, field_name: str) -> float:
    """Return ``value`` as a finite float, rejecting bools and non-real types."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")
    return numeric_value


def _positive_real(value: object, field_name: str) -> float:
    numeric_value = _finite_real(value, field_name)
    if numeric_value <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    return numeric_value


def _non_negative_real(value: object, field_name: str) -> float:
    numeric_value = _finite_real(value, field_name)
    if numeric_value < 0.0:
        raise ValueError(f"{field_name} cannot be negative")
    return numeric_value


def _validate_version(version: object, field_name: str = "calibration_version") -> str:
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return version.strip()


class LiquidityTier(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """An immutable set of execution starting constraints for one liquidity tier.

    Every invariant documented in ``references/standards.md`` is enforced here, at
    construction, so a profile built directly by an integrator carries the same
    guarantees as one produced by :class:`ExecutionParameterManager`. A profile is
    a *default and a ceiling* for a downstream execution system; it is never
    order-routing authorization.

    Fields:
        tier: The :class:`LiquidityTier` this profile is calibrated for. When the
            profile is placed in a manager's mapping, the mapping key must equal
            this value.
        max_participation_rate: Ceiling on the order's share of market volume, as
            a fraction in ``(0, 1]`` of **total** volume including the order's own
            prints. It is an upper bound for the execution engine to enforce,
            never a target to trade up to -- see
            ``participation-of-volume-pov-execution`` for the ``R/(1-R)`` identity
            that turns it into child quantities.
        default_algo_type: One of ``TWAP``, ``VWAP`` or ``IS``. The *starting*
            schedule shape only; urgency and completion policy live outside this
            module.
        cross_spread_allowed: Whether the profile's default posture lets the
            execution engine take liquidity. ``False`` means "rest passively by
            default". It is not by itself a hard prohibition, and it does not
            supply the urgency escalation policy an IS-style schedule needs.
        passive_buffer_bps: How far *behind* the same-side touch a passive child
            order may rest, in basis points (1 bps = 0.01%) of the current
            same-side touch price, as a non-negative magnitude. With a 5.0 bps
            buffer and a touch of 100.00, a buy may rest at or above 99.95 and a
            sell at or below 100.05. The sign comes from the side, never from this
            field. It is a passive *placement* tolerance, unrelated to price
            collars, limit prices or slippage budgets, which are independent risk
            controls.
        calibration_version: Identifier of the reviewed calibration this profile
            belongs to. Persist it with every parent-order decision so a fill can
            be traced back to the parameter set that produced it.
        requires_live_market_check: When ``True`` (the default, and the only value
            the shipped calibration uses), ``cross_spread_allowed`` is a
            capability flag and not authorization: the EMS must still decide from
            current protected quotes, depth, volatility, venue state and risk
            policy before any child order is sent.
    """

    tier: LiquidityTier
    max_participation_rate: float
    default_algo_type: str
    cross_spread_allowed: bool
    passive_buffer_bps: float
    calibration_version: str = "default-1.0"
    requires_live_market_check: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.tier, LiquidityTier):
            raise TypeError("tier must be a LiquidityTier")
        participation_rate = _finite_real(
            self.max_participation_rate, "max_participation_rate"
        )
        if not 0.0 < participation_rate <= 1.0:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if self.default_algo_type not in _VALID_ALGO_TYPES:
            raise ValueError(
                f"default_algo_type must be one of {sorted(_VALID_ALGO_TYPES)}"
            )
        if not isinstance(self.cross_spread_allowed, bool):
            raise TypeError("cross_spread_allowed must be a bool")
        _non_negative_real(self.passive_buffer_bps, "passive_buffer_bps")
        if not isinstance(self.requires_live_market_check, bool):
            raise TypeError("requires_live_market_check must be a bool")
        _validate_version(self.calibration_version)


class ExecutionParameterManager:
    """Classify instruments by ADV and return validated execution defaults.

    Profiles are starting constraints, not order-routing authorization. The
    caller must apply current spread, volatility, order-size, venue, and
    independent pre-trade risk checks before submitting any child order.

    The shipped default calibration deliberately widens ``max_participation_rate``
    as liquidity *falls* (5% / 10% / 20%). That is a fill-feasibility ceiling for
    orders that are large relative to a thin name's volume, not a claim that
    participating harder there is cheap: under the square-root law of market
    impact, cost scales with ``sigma * sqrt(Q / ADV)``, so a 20%-of-ADV footprint
    is more expensive in a low-ADV, high-volatility name, not less. Treat these
    values as illustrative and recalibrate against your own TCA before use -- see
    ``references/standards.md``.
    """

    def __init__(
        self,
        high_adv_threshold: float = 10_000_000.0,
        medium_adv_threshold: float = 1_000_000.0,
        *,
        profiles: Optional[Mapping[LiquidityTier, ExecutionProfile]] = None,
        calibration_version: str = "default-1.0",
        max_adv_age_days: float = 30.0,
        require_adv_age: bool = False,
    ):
        self.high_adv_threshold = _positive_real(
            high_adv_threshold, "high_adv_threshold"
        )
        self.medium_adv_threshold = _positive_real(
            medium_adv_threshold, "medium_adv_threshold"
        )
        if self.high_adv_threshold <= self.medium_adv_threshold:
            raise ValueError("high_adv_threshold must exceed medium_adv_threshold")

        self.calibration_version = _validate_version(calibration_version)
        self.max_adv_age_days = _non_negative_real(max_adv_age_days, "max_adv_age_days")
        if not isinstance(require_adv_age, bool):
            raise TypeError("require_adv_age must be a bool")
        self.require_adv_age = require_adv_age

        if profiles is None:
            profiles = self._default_profiles(self.calibration_version)
        self._profiles = self._validate_profiles(profiles)

    @property
    def profiles(self) -> Mapping[LiquidityTier, ExecutionProfile]:
        """Read-only view of the calibrated profile set.

        Returned as a mapping proxy so an approved calibration cannot be swapped
        out in place after construction. Build a new manager to change it.
        """
        return self._profiles

    def __getstate__(self) -> dict:
        # A mapping proxy is not picklable, and a manager must stay transferable
        # to worker processes (a backtest fan-out ships an approved calibration
        # rather than rebuilding it per worker). Unwrap for transport only.
        state = dict(self.__dict__)
        state["_profiles"] = dict(self._profiles)
        return state

    def __setstate__(self, state: Mapping[str, object]) -> None:
        restored = dict(state)
        profiles = restored.pop("_profiles")
        self.__dict__.update(restored)
        # Re-validate on the way in: a calibration that crossed a process or a
        # storage boundary has not been checked by any constructor.
        self._profiles = self._validate_profiles(profiles)

    @staticmethod
    def _default_profiles(
        calibration_version: str,
    ) -> dict[LiquidityTier, ExecutionProfile]:
        return {
            LiquidityTier.HIGH: ExecutionProfile(
                tier=LiquidityTier.HIGH,
                max_participation_rate=0.05,
                default_algo_type="TWAP",
                cross_spread_allowed=True,
                passive_buffer_bps=1.0,
                calibration_version=calibration_version,
            ),
            LiquidityTier.MEDIUM: ExecutionProfile(
                tier=LiquidityTier.MEDIUM,
                max_participation_rate=0.10,
                default_algo_type="VWAP",
                cross_spread_allowed=False,
                passive_buffer_bps=5.0,
                calibration_version=calibration_version,
            ),
            LiquidityTier.LOW: ExecutionProfile(
                tier=LiquidityTier.LOW,
                max_participation_rate=0.20,
                default_algo_type="IS",
                cross_spread_allowed=False,
                passive_buffer_bps=20.0,
                calibration_version=calibration_version,
            ),
        }

    @staticmethod
    def _validate_profiles(
        profiles: Mapping[LiquidityTier, ExecutionProfile],
    ) -> Mapping[LiquidityTier, ExecutionProfile]:
        if not isinstance(profiles, Mapping):
            raise TypeError("profiles must be a mapping")
        normalized_profiles = dict(profiles)
        expected_tiers = set(LiquidityTier)
        if set(normalized_profiles) != expected_tiers:
            raise ValueError("profiles must define HIGH, MEDIUM, and LOW tiers")
        for tier, profile in normalized_profiles.items():
            if not isinstance(profile, ExecutionProfile):
                raise TypeError("profiles must contain ExecutionProfile values")
            if profile.tier != tier:
                raise ValueError("profile mapping key must match profile.tier")
        return MappingProxyType(normalized_profiles)

    def _validate_adv(self, adv: object) -> float:
        normalized_adv = _non_negative_real(adv, "adv")
        if normalized_adv == 0.0:
            # A zero ADV is a data-quality signal (suspended, never traded, or a
            # broken feed), not evidence that the instrument belongs in the
            # lowest tier. Classification still proceeds so callers keep a single
            # rejection path, but the condition must not pass unnoticed.
            logger.warning(
                "ADV is zero; treat as a data-quality failure rather than a "
                "LOW-tier classification and confirm the instrument is tradable"
            )
        return normalized_adv

    def _validate_adv_age(self, adv_age_days: Optional[float]) -> None:
        if adv_age_days is None:
            if self.require_adv_age:
                raise ValueError("adv_age_days is required because require_adv_age=True")
            return
        age_days = _non_negative_real(adv_age_days, "adv_age_days")
        if age_days > self.max_adv_age_days:
            raise ValueError(
                f"adv_age_days exceeds configured maximum of {self.max_adv_age_days:g}"
            )

    def classify_tier(
        self, adv: float, *, adv_age_days: Optional[float] = None
    ) -> LiquidityTier:
        """Classify a validated ADV observation into a liquidity tier.

        Boundaries are inclusive from below: ``adv >= high_adv_threshold`` is
        ``HIGH``, and ``adv >= medium_adv_threshold`` is ``MEDIUM``.

        Raises:
            TypeError: ``adv`` or ``adv_age_days`` is not a real number.
            ValueError: ``adv`` is negative or non-finite, or the observation is
                older than ``max_adv_age_days`` -- or absent while
                ``require_adv_age`` is set.
        """
        normalized_adv = self._validate_adv(adv)
        self._validate_adv_age(adv_age_days)
        if normalized_adv >= self.high_adv_threshold:
            return LiquidityTier.HIGH
        if normalized_adv >= self.medium_adv_threshold:
            return LiquidityTier.MEDIUM
        return LiquidityTier.LOW

    def get_profile(
        self, adv: float, *, adv_age_days: Optional[float] = None
    ) -> ExecutionProfile:
        """Return a validated default profile for a fresh-enough ADV value.

        The result is a starting constraint. ``requires_live_market_check`` on the
        returned profile stays the caller's obligation: current spread, depth,
        volatility, order size, venue state and independent pre-trade risk
        controls all gate the actual child order.
        """
        tier = self.classify_tier(adv, adv_age_days=adv_age_days)
        profile = self._profiles[tier]
        logger.info(
            "Assigned %s liquidity profile for ADV %.0f (calibration=%s)",
            tier.value,
            float(adv),
            profile.calibration_version,
        )
        return profile

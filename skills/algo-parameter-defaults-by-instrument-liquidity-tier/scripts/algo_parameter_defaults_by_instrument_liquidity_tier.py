"""Liquidity-tier execution defaults with validation and calibration metadata."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import logging
import math
from numbers import Real
from typing import Optional


logger = logging.getLogger(__name__)


class LiquidityTier(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    tier: LiquidityTier
    max_participation_rate: float
    default_algo_type: str
    cross_spread_allowed: bool
    passive_buffer_bps: float
    calibration_version: str = "default-1.0"
    requires_live_market_check: bool = True


class ExecutionParameterManager:
    """Classify instruments by ADV and return validated execution defaults.

    Profiles are starting constraints, not order-routing authorization. The
    caller must apply current spread, volatility, order-size, venue, and
    independent pre-trade risk checks before submitting any child order.
    """

    _VALID_ALGOS = {"TWAP", "VWAP", "IS"}

    def __init__(
        self,
        high_adv_threshold: float = 10_000_000.0,
        medium_adv_threshold: float = 1_000_000.0,
        *,
        profiles: Optional[Mapping[LiquidityTier, ExecutionProfile]] = None,
        calibration_version: str = "default-1.0",
        max_adv_age_days: float = 30.0,
    ):
        self.high_adv_threshold = self._validate_threshold(
            high_adv_threshold, "high_adv_threshold"
        )
        self.medium_adv_threshold = self._validate_threshold(
            medium_adv_threshold, "medium_adv_threshold"
        )
        if self.high_adv_threshold <= self.medium_adv_threshold:
            raise ValueError("high_adv_threshold must exceed medium_adv_threshold")

        self.calibration_version = self._validate_version(calibration_version)
        self.max_adv_age_days = self._validate_non_negative(
            max_adv_age_days, "max_adv_age_days"
        )
        if profiles is None:
            profiles = self._default_profiles(self.calibration_version)
        self._profiles = self._validate_profiles(profiles)

    @staticmethod
    def _finite_real(value: object, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"{field_name} must be a real number")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError(f"{field_name} must be finite")
        return numeric_value

    @classmethod
    def _validate_threshold(cls, value: object, field_name: str) -> float:
        threshold = cls._finite_real(value, field_name)
        if threshold <= 0.0:
            raise ValueError(f"{field_name} must be positive")
        return threshold

    @classmethod
    def _validate_non_negative(cls, value: object, field_name: str) -> float:
        numeric_value = cls._finite_real(value, field_name)
        if numeric_value < 0.0:
            raise ValueError(f"{field_name} cannot be negative")
        return numeric_value

    @staticmethod
    def _validate_version(version: str) -> str:
        if not isinstance(version, str) or not version.strip():
            raise ValueError("calibration_version must be a non-empty string")
        return version.strip()

    @classmethod
    def _validate_profile(cls, profile: ExecutionProfile) -> ExecutionProfile:
        if not isinstance(profile, ExecutionProfile):
            raise TypeError("profiles must contain ExecutionProfile values")
        if not isinstance(profile.tier, LiquidityTier):
            raise TypeError("profile tier must be a LiquidityTier")
        participation_rate = cls._finite_real(
            profile.max_participation_rate, "max_participation_rate"
        )
        if not 0.0 < participation_rate <= 1.0:
            raise ValueError("max_participation_rate must be in (0, 1]")
        if profile.default_algo_type not in cls._VALID_ALGOS:
            raise ValueError(
                f"default_algo_type must be one of {sorted(cls._VALID_ALGOS)}"
            )
        if not isinstance(profile.cross_spread_allowed, bool):
            raise TypeError("cross_spread_allowed must be a bool")
        passive_buffer = cls._finite_real(
            profile.passive_buffer_bps, "passive_buffer_bps"
        )
        if passive_buffer < 0.0:
            raise ValueError("passive_buffer_bps cannot be negative")
        if not isinstance(profile.requires_live_market_check, bool):
            raise TypeError("requires_live_market_check must be a bool")
        cls._validate_version(profile.calibration_version)
        return profile

    @classmethod
    def _default_profiles(cls, calibration_version: str) -> dict[LiquidityTier, ExecutionProfile]:
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

    @classmethod
    def _validate_profiles(
        cls, profiles: Mapping[LiquidityTier, ExecutionProfile]
    ) -> dict[LiquidityTier, ExecutionProfile]:
        if not isinstance(profiles, Mapping):
            raise TypeError("profiles must be a mapping")
        normalized_profiles = dict(profiles)
        expected_tiers = set(LiquidityTier)
        if set(normalized_profiles) != expected_tiers:
            raise ValueError("profiles must define HIGH, MEDIUM, and LOW tiers")
        for tier, profile in normalized_profiles.items():
            validated_profile = cls._validate_profile(profile)
            if validated_profile.tier != tier:
                raise ValueError("profile mapping key must match profile.tier")
        return normalized_profiles

    def _validate_adv(self, adv: object) -> float:
        normalized_adv = self._finite_real(adv, "adv")
        if normalized_adv < 0.0:
            raise ValueError("adv cannot be negative")
        return normalized_adv

    def classify_tier(self, adv: float, *, adv_age_days: Optional[float] = None) -> LiquidityTier:
        normalized_adv = self._validate_adv(adv)
        if adv_age_days is not None:
            age_days = self._validate_non_negative(adv_age_days, "adv_age_days")
            if age_days > self.max_adv_age_days:
                raise ValueError(
                    f"adv_age_days exceeds configured maximum of {self.max_adv_age_days:g}"
                )
        if normalized_adv >= self.high_adv_threshold:
            return LiquidityTier.HIGH
        if normalized_adv >= self.medium_adv_threshold:
            return LiquidityTier.MEDIUM
        return LiquidityTier.LOW

    def get_profile(
        self, adv: float, *, adv_age_days: Optional[float] = None
    ) -> ExecutionProfile:
        """Return a validated default profile for a fresh-enough ADV value."""
        tier = self.classify_tier(adv, adv_age_days=adv_age_days)
        profile = self._profiles[tier]
        logger.info(
            "Assigned %s liquidity profile for ADV %.0f (calibration=%s)",
            tier.value,
            float(adv),
            profile.calibration_version,
        )
        return profile
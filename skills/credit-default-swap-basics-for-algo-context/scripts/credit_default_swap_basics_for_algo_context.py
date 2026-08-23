"""Textbook CDS metrics for algo context: credit triangle, survival
probabilities, risky PV01 (continuous-annuity approximation), upfront
payments, and CDS-equity cross-asset signals.

Conventions grounded in ISDA standardisation (April 2009 "Big Bang"):
North American corporate CDS (SNAC) trade with fixed 100 bps (IG) /
500 bps (HY) coupons plus upfront settlement; the ISDA CDS Standard
Model (https://www.cdsmodel.com/) is the reference implementation for
upfront conversion, using quarterly premiums on IMM dates and
Actual/360 accrual.

Limitations (documented, deliberate):
- The RPV01 here is the continuous-annuity approximation
  (1 - e^-(r+lambda)T)/(r+lambda), NOT the ISDA Standard Model's
  quarterly Act/360 annuity: upfront figures are indicative and will
  not exactly match ISDA cash settlement.
- The hazard rate uses the flat credit-triangle approximation
  lambda = s_par / (1 - R) with constant recovery, no curve stripping.
- Credit-tier boundaries (150 / 1000 bps) are informal desk
  conventions, not codified ISDA rules; 1000 bps (not 500) marks
  distressed because 500 bps is the standard high-yield coupon.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


@dataclass
class CdsValuationResult:
    par_spread_bps: float
    hazard_rate_pct: float
    survival_probability_pct: float
    cumulative_default_prob_pct: float
    rpv01: float
    isda_upfront_payment_usd: float   # indicative continuous-annuity approximation
    credit_tier: str


@dataclass
class CrossAssetSignal:
    latest_spread_bps: float
    z_score: float
    signal: str                        # 'SHORT_EQUITY_LONG_CDS', 'LONG_EQUITY_SHORT_CDS', 'NEUTRAL'


class CreditDefaultSwapEngine:
    """
    Quantitative engine for CDS Credit Triangle mathematics, indicative
    upfront payments, Risky PV01 (RPV01), implied hazard rates, and
    CDS-equity cross-asset spike signals.
    """
    def __init__(
        self,
        recovery_rate: float = 0.40,
        risk_free_rate: float = 0.04,
        ig_crossover_threshold_bps: float = 150.0,
        crossover_distressed_threshold_bps: float = 1000.0,
    ):
        _require_finite("recovery_rate", recovery_rate)
        if not 0.0 <= recovery_rate < 1.0:
            raise ValueError(
                f"recovery_rate must be within [0, 1) — 1.0 makes LGD zero, "
                f"got {recovery_rate}"
            )
        _require_finite("risk_free_rate", risk_free_rate)
        _require_finite("ig_crossover_threshold_bps", ig_crossover_threshold_bps)
        _require_finite("crossover_distressed_threshold_bps", crossover_distressed_threshold_bps)
        if not 0.0 < ig_crossover_threshold_bps < crossover_distressed_threshold_bps:
            raise ValueError(
                "tier thresholds must satisfy 0 < ig_crossover_threshold_bps "
                f"< crossover_distressed_threshold_bps, got "
                f"{ig_crossover_threshold_bps} / {crossover_distressed_threshold_bps}"
            )
        self.recovery_rate = recovery_rate       # ISDA CDS Standard Model convention: 40%
        self.risk_free_rate = risk_free_rate     # Annualized continuous rate
        self.ig_crossover_threshold_bps = ig_crossover_threshold_bps
        self.crossover_distressed_threshold_bps = crossover_distressed_threshold_bps

    def calculate_hazard_rate(self, par_spread_bps: float) -> float:
        """
        Flat credit-triangle hazard rate: lambda = s_par / (1 - R).
        Assumes constant hazard and recovery; s_par in bps.
        """
        _require_finite("par_spread_bps", par_spread_bps)
        if par_spread_bps < 0:
            raise ValueError(f"Par spread cannot be negative, got {par_spread_bps}")
        par_spread_decimal = par_spread_bps / 10000.0
        return par_spread_decimal / (1.0 - self.recovery_rate)

    def calculate_default_probabilities(self, hazard_rate: float, maturity_years: float) -> Tuple[float, float]:
        """
        Returns (Survival Probability, Cumulative Default Probability) over
        maturity T under a constant hazard rate:
        Survival(T) = exp(-lambda * T); PD(T) = 1 - Survival(T).
        """
        _require_finite("hazard_rate", hazard_rate)
        if hazard_rate < 0:
            raise ValueError(f"hazard_rate cannot be negative, got {hazard_rate}")
        _require_finite("maturity_years", maturity_years)
        if maturity_years <= 0:
            raise ValueError(f"maturity_years must be positive, got {maturity_years}")
        survival = math.exp(-hazard_rate * maturity_years)
        cum_default = 1.0 - survival
        return float(survival), float(cum_default)

    def calculate_rpv01(self, hazard_rate: float, maturity_years: float) -> float:
        """
        Risky PV01, continuous-annuity approximation:
        RPV01 = (1 - exp(-(r + lambda) * T)) / (r + lambda).
        As (r + lambda) -> 0 the expression converges to T, which is
        returned by the limit branch. This is NOT the ISDA Standard
        Model's quarterly Act/360 annuity.
        """
        _require_finite("hazard_rate", hazard_rate)
        if hazard_rate < 0:
            raise ValueError(f"hazard_rate cannot be negative, got {hazard_rate}")
        _require_finite("maturity_years", maturity_years)
        if maturity_years <= 0:
            raise ValueError(f"maturity_years must be positive, got {maturity_years}")
        total_rate = self.risk_free_rate + hazard_rate
        if total_rate <= 0:
            return float(maturity_years)
        return (1.0 - math.exp(-total_rate * maturity_years)) / total_rate

    def classify_credit_tier(self, par_spread_bps: float) -> str:
        """
        Heuristic spread bucketing on informal desk conventions:
        < 150 bps INVESTMENT_GRADE, 150-1000 CROSSOVER_HIGH_YIELD,
        >= 1000 DISTRESSED (500 bps is the standard HY coupon, so it
        cannot be the distressed boundary).
        """
        _require_finite("par_spread_bps", par_spread_bps)
        if par_spread_bps < 0:
            raise ValueError(f"Par spread cannot be negative, got {par_spread_bps}")
        if par_spread_bps < self.ig_crossover_threshold_bps:
            return "INVESTMENT_GRADE"
        if par_spread_bps < self.crossover_distressed_threshold_bps:
            return "CROSSOVER_HIGH_YIELD"
        return "DISTRESSED"

    def calculate_isda_upfront_payment(
        self,
        notional_usd: float,
        par_spread_bps: float,
        standard_coupon_bps: float,
        maturity_years: float
    ) -> CdsValuationResult:
        """
        Indicative upfront payment and CDS credit metrics:
        Upfront = Notional * RPV01 * (s_par - s_coupon). The protection
        buyer pays when the par spread exceeds the fixed coupon. Sign and
        magnitude follow the continuous-annuity approximation, so treat
        the figure as indicative rather than an ISDA cash-settlement match.
        """
        _require_finite("notional_usd", notional_usd)
        if notional_usd <= 0:
            raise ValueError(f"notional_usd must be positive, got {notional_usd}")
        _require_finite("maturity_years", maturity_years)
        if maturity_years <= 0:
            raise ValueError(f"maturity_years must be positive, got {maturity_years}")
        _require_finite("standard_coupon_bps", standard_coupon_bps)
        if standard_coupon_bps < 0:
            raise ValueError(
                f"standard_coupon_bps cannot be negative, got {standard_coupon_bps}"
            )

        hazard_rate = self.calculate_hazard_rate(par_spread_bps)
        survival_prob, default_prob = self.calculate_default_probabilities(hazard_rate, maturity_years)
        rpv01 = self.calculate_rpv01(hazard_rate, maturity_years)

        spread_diff_decimal = (par_spread_bps - standard_coupon_bps) / 10000.0
        upfront_payment = notional_usd * rpv01 * spread_diff_decimal

        credit_tier = self.classify_credit_tier(par_spread_bps)

        logger.info(
            f"CDS Valuation [ParSpread={par_spread_bps}bps, Coupon={standard_coupon_bps}bps]: "
            f"HazardRate={hazard_rate*100:.2f}%, RPV01={rpv01:.4f}, Upfront=${upfront_payment:,.2f}"
        )

        return CdsValuationResult(
            par_spread_bps=par_spread_bps,
            hazard_rate_pct=round(hazard_rate * 100, 4),
            survival_probability_pct=round(survival_prob * 100, 2),
            cumulative_default_prob_pct=round(default_prob * 100, 2),
            rpv01=round(rpv01, 4),
            isda_upfront_payment_usd=round(upfront_payment, 2),
            credit_tier=credit_tier
        )

    @staticmethod
    def generate_cross_asset_signal(
        spread_history_bps: Sequence[float], z_threshold: float = 2.0
    ) -> CrossAssetSignal:
        """
        Z-score spike detector for capital-structure signals: compares the
        latest CDS spread against the history's mean and (population)
        standard deviation.

        z > z_threshold  -> SHORT_EQUITY_LONG_CDS (credit distress spike)
        z < -z_threshold -> LONG_EQUITY_SHORT_CDS (spread compression)
        else             -> NEUTRAL

        A flat history (zero standard deviation) yields z = 0 -> NEUTRAL;
        fewer than two observations cannot produce a z-score and raise
        ValueError.
        """
        history: List[float] = list(spread_history_bps)
        if len(history) < 2:
            raise ValueError(
                f"spread_history_bps needs at least 2 observations, got {len(history)}"
            )
        for i, s in enumerate(history):
            _require_finite(f"spread_history_bps[{i}]", s)
            if s < 0:
                raise ValueError(f"CDS spreads cannot be negative, got {s} at index {i}")
        _require_finite("z_threshold", z_threshold)
        if z_threshold <= 0:
            raise ValueError(f"z_threshold must be positive, got {z_threshold}")

        latest = history[-1]
        mean = sum(history) / len(history)
        variance = sum((s - mean) ** 2 for s in history) / len(history)
        std = math.sqrt(variance)
        z_score = 0.0 if std == 0.0 else (latest - mean) / std

        if z_score > z_threshold:
            signal = "SHORT_EQUITY_LONG_CDS"
        elif z_score < -z_threshold:
            signal = "LONG_EQUITY_SHORT_CDS"
        else:
            signal = "NEUTRAL"

        logger.info(
            f"Cross-Asset Signal [Latest={latest}bps, Mean={mean:.2f}, "
            f"Z={z_score:+.2f}] -> {signal}"
        )
        return CrossAssetSignal(
            latest_spread_bps=latest,
            z_score=round(float(z_score), 4),
            signal=signal,
        )

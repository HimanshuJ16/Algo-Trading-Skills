import math
import logging
from dataclasses import dataclass
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CdsValuationResult:
    par_spread_bps: float
    hazard_rate_pct: float
    survival_probability_pct: float
    cumulative_default_prob_pct: float
    rpv01: float
    isda_upfront_payment_usd: float
    credit_tier: str

class CreditDefaultSwapEngine:
    """
    Quantitative engine for CDS Credit Triangle mathematics, ISDA upfront payment,
    Risky PV01 (RPV01), and implied hazard rate calculations.
    """
    def __init__(self, recovery_rate: float = 0.40, risk_free_rate: float = 0.04):
        self.recovery_rate = recovery_rate       # Standard 40% recovery rate
        self.risk_free_rate = risk_free_rate     # Annualized continuous rate (4%)

    def calculate_hazard_rate(self, par_spread_bps: float) -> float:
        """
        Calculates implied hazard rate lambda = par_spread / (1 - Recovery)
        """
        if par_spread_bps < 0:
            raise ValueError("Par spread cannot be negative.")
        par_spread_decimal = par_spread_bps / 10000.0
        return par_spread_decimal / (1.0 - self.recovery_rate)

    def calculate_default_probabilities(self, hazard_rate: float, maturity_years: float) -> Tuple[float, float]:
        """
        Returns (Survival Probability, Cumulative Default Probability) over maturity T.
        Survival(T) = exp(-lambda * T)
        PD(T) = 1 - Survival(T)
        """
        survival = math.exp(-hazard_rate * maturity_years)
        cum_default = 1.0 - survival
        return float(survival), float(cum_default)

    def calculate_rpv01(self, hazard_rate: float, maturity_years: float) -> float:
        """
        Calculates Risky PV01 (RPV01): RPV01 = (1 - exp(-(r + lambda)*T)) / (r + lambda)
        """
        total_rate = self.risk_free_rate + hazard_rate
        if total_rate <= 0:
            return maturity_years
        return (1.0 - math.exp(-total_rate * maturity_years)) / total_rate

    def calculate_isda_upfront_payment(
        self,
        notional_usd: float,
        par_spread_bps: float,
        standard_coupon_bps: float,
        maturity_years: float
    ) -> CdsValuationResult:
        """
        Calculates ISDA standard upfront payment and CDS credit metrics.
        """
        if notional_usd <= 0 or maturity_years <= 0:
            raise ValueError("Notional and maturity must be positive.")

        hazard_rate = self.calculate_hazard_rate(par_spread_bps)
        survival_prob, default_prob = self.calculate_default_probabilities(hazard_rate, maturity_years)
        rpv01 = self.calculate_rpv01(hazard_rate, maturity_years)

        # Upfront Payment = Notional * RPV01 * (Par_Spread - Fixed_Coupon)
        spread_diff_decimal = (par_spread_bps - standard_coupon_bps) / 10000.0
        upfront_payment = notional_usd * rpv01 * spread_diff_decimal

        # Credit Tier
        if par_spread_bps < 150.0:
            credit_tier = "INVESTMENT_GRADE"
        elif par_spread_bps < 500.0:
            credit_tier = "CROSSOVER_HIGH_YIELD"
        else:
            credit_tier = "DISTRESSED"

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

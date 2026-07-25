"""
options-backtesting-with-realistic-iv-surface: 3D IV surface interpolator (smile & skew),
Black-Scholes option pricer, and Greeks calculation engine.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OptionGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float


@dataclass
class OptionPricingResult:
    option_type: str  # CALL or PUT
    underlying_price: float
    strike_price: float
    tte_years: float
    atm_volatility: float
    strike_iv: float
    option_price: float
    greeks: OptionGreeks


class OptionsIVSurfaceEngine:
    """
    Interpolates dynamic 3D implied volatility (IV) surfaces across strike moneyness
    and time-to-expiration (TTE), evaluating Black-Scholes prices and Option Greeks.
    """

    def __init__(
        self,
        risk_free_rate: float = 0.05,
        skew_alpha: float = -0.30,  # Negative slope (put skew)
        smile_beta: float = 0.50,   # Convexity (smile)
    ):
        self.risk_free_rate = risk_free_rate
        self.skew_alpha = skew_alpha
        self.smile_beta = smile_beta

    def get_strike_iv(self, spot: float, strike: float, tte_years: float, atm_vol: float) -> float:
        """
        Evaluates IV for strike K/S and TTE using parametric smile model:
        IV = ATM + alpha*(m - 1.0) + beta*(m - 1.0)^2
        """
        if spot <= 0 or strike <= 0 or tte_years <= 0:
            return max(0.01, atm_vol)

        m = strike / float(spot)
        term_factor = 1.0 / math.sqrt(max(0.01, tte_years))

        # Skew/Smile offset
        skew_offset = (self.skew_alpha * (m - 1.0)) + (self.smile_beta * ((m - 1.0) ** 2))
        strike_iv = atm_vol + (skew_offset * 0.5)

        return max(0.05, min(3.0, round(strike_iv, 4)))

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def _norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def price_option(
        self,
        option_type: str,
        spot: float,
        strike: float,
        tte_years: float,
        atm_vol: float,
    ) -> OptionPricingResult:
        """
        Prices a European Call or Put option using strike-specific IV surface.
        """
        is_call = (option_type.upper() == "CALL")
        strike_iv = self.get_strike_iv(spot, strike, tte_years, atm_vol)

        r = self.risk_free_rate
        t = max(0.0001, tte_years)
        sigma = max(0.01, strike_iv)

        d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        d2 = d1 - (sigma * math.sqrt(t))

        norm_d1 = self._norm_cdf(d1)
        norm_d2 = self._norm_cdf(d2)
        pdf_d1 = self._norm_pdf(d1)

        discount = math.exp(-r * t)

        if is_call:
            price = (spot * norm_d1) - (strike * discount * norm_d2)
            delta = norm_d1
        else:
            price = (strike * discount * self._norm_cdf(-d2)) - (spot * self._norm_cdf(-d1))
            delta = norm_d1 - 1.0

        gamma = pdf_d1 / (spot * sigma * math.sqrt(t))
        vega = (spot * pdf_d1 * math.sqrt(t)) / 100.0  # per 1% vol change

        if is_call:
            theta = (-(spot * pdf_d1 * sigma) / (2.0 * math.sqrt(t)) - r * strike * discount * norm_d2) / 365.0
        else:
            theta = (-(spot * pdf_d1 * sigma) / (2.0 * math.sqrt(t)) + r * strike * discount * self._norm_cdf(-d2)) / 365.0

        greeks = OptionGreeks(
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 4),
            vega=round(vega, 4),
        )

        return OptionPricingResult(
            option_type="CALL" if is_call else "PUT",
            underlying_price=spot,
            strike_price=strike,
            tte_years=tte_years,
            atm_volatility=atm_vol,
            strike_iv=strike_iv,
            option_price=round(max(0.01, price), 2),
            greeks=greeks,
        )

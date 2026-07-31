import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

@dataclass
class InputData:
    value: float = 10.0                  # Retained for legacy backward compatibility
    spot_price: float = 100.0            # S: Foreign asset spot price
    strike_price: float = 100.0          # K: Strike price
    time_to_expiry_years: float = 1.0    # T: Time to expiry in years
    domestic_rate: float = 0.05          # r_d: Domestic risk-free rate (for discounting)
    foreign_rate: float = 0.02           # r_f: Foreign risk-free rate
    dividend_yield: float = 0.0          # q: Asset dividend yield
    asset_volatility: float = 0.20       # sigma_S: Foreign asset volatility
    fx_volatility: float = 0.15          # sigma_X: FX rate volatility
    correlation: float = 0.30            # rho: Correlation between asset return and FX return
    fixed_fx_rate: float = 1.0           # F_X: Fixed exchange rate multiplier (domestic per foreign)
    option_type: str = 'CALL'            # 'CALL', 'PUT'

@dataclass
class QuantoOptionPricingReport:
    spot_price: float
    strike_price: float
    option_type: str
    quanto_drift: float
    d1: float
    d2: float
    quanto_option_price_domestic: float
    quanto_delta: float
    quanto_gamma: float
    quanto_vega: float
    fx_correlation_sensitivity: float   # dV / d_rho
    status: str
    audit_notes: str

class QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine:
    """
    Quantitative quanto option pricing engine adjusting Black-Scholes drift for cross-currency
    asset-FX correlation risk, quanto Delta, and FX correlation sensitivity.
    """
    def __init__(self):
        pass

    def process(self, data: InputData) -> float:
        """
        Legacy process method retained for backward compatibility.
        Returns data.value * 2.0.
        """
        return data.value * 2.0

    def price_quanto_option(self, data: InputData) -> QuantoOptionPricingReport:
        """
        Calculates Black-Scholes Quanto Option Price and Greeks.
        """
        S = data.spot_price
        K = data.strike_price
        T = data.time_to_expiry_years
        r_d = data.domestic_rate
        r_f = data.foreign_rate
        q = data.dividend_yield
        sigma_S = data.asset_volatility
        sigma_X = data.fx_volatility
        rho = data.correlation
        F_X = data.fixed_fx_rate
        opt_type = data.option_type.upper()

        if T <= 0 or S <= 0 or K <= 0 or sigma_S <= 0:
            raise ValueError("Invalid parameters: T, S, K, and asset_volatility must be positive.")

        # Black-Scholes Quanto Drift Adjustment: r_quanto = r_f - q - rho * sigma_S * sigma_X
        r_quanto = r_f - q - (rho * sigma_S * sigma_X)

        d1 = (math.log(S / K) + (r_quanto + 0.5 * sigma_S**2) * T) / (sigma_S * math.sqrt(T))
        d2 = d1 - sigma_S * math.sqrt(T)

        disc_dom = math.exp(-r_d * T)
        drift_exp = math.exp(r_quanto * T)

        if opt_type == "CALL":
            price = F_X * disc_dom * (S * drift_exp * norm_cdf(d1) - K * norm_cdf(d2))
            delta = F_X * disc_dom * drift_exp * norm_cdf(d1)
        else: # PUT
            price = F_X * disc_dom * (K * norm_cdf(-d2) - S * drift_exp * norm_cdf(-d1))
            delta = -F_X * disc_dom * drift_exp * norm_cdf(-d1)

        gamma = (F_X * disc_dom * drift_exp * norm_pdf(d1)) / (S * sigma_S * math.sqrt(T))
        vega = F_X * disc_dom * S * drift_exp * norm_pdf(d1) * math.sqrt(T)

        # FX Correlation Sensitivity: dV / d_rho
        # dV/d_rho = - F_X * disc_dom * S * drift_exp * T * sigma_S * sigma_X * norm_cdf(d1)
        correlation_sens = -F_X * disc_dom * S * drift_exp * T * sigma_S * sigma_X * norm_cdf(d1 if opt_type == "CALL" else -d1)

        notes = (
            f"QUANTO OPTION PRICING [{opt_type} - {S}/{K} (T={T}y)]: "
            f"Price = ${price:.4f} (Domestic), Quanto Drift = {r_quanto:.4f} (Adj vs r_f = {r_f:.4f}), "
            f"d1 = {d1:.4f}, d2 = {d2:.4f}, Delta = {delta:.4f}, FX Correlation Sens = {correlation_sens:.4f}."
        )

        logger.info(notes)

        return QuantoOptionPricingReport(
            spot_price=S,
            strike_price=K,
            option_type=opt_type,
            quanto_drift=round(r_quanto, 6),
            d1=round(d1, 6),
            d2=round(d2, 6),
            quanto_option_price_domestic=round(price, 4),
            quanto_delta=round(delta, 4),
            quanto_gamma=round(gamma, 6),
            quanto_vega=round(vega, 4),
            fx_correlation_sensitivity=round(correlation_sens, 4),
            status="QUANTO_PRICING_SUCCESSFUL",
            audit_notes=notes
        )

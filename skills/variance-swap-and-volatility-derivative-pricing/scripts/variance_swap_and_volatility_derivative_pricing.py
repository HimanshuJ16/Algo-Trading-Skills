import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class SwapType(Enum):
    VARIANCE_SWAP = "VARIANCE_SWAP"
    VOLATILITY_SWAP = "VOLATILITY_SWAP"


class VariancePricingError(Exception):
    """Base exception for Variance Swap pricing errors."""
    pass


@dataclass
class OptionQuote:
    strike: float
    option_type: OptionType
    price: float
    implied_vol: float = 0.0


@dataclass
class VarianceSwapContract:
    contract_id: str
    symbol: str
    swap_type: SwapType
    strike_vol_pct: float          # Strike in volatility percentage points (e.g. 20.0 for 20%)
    vega_notional_usd: float       # Vega Notional e.g. $100,000 per vol point
    t_years: float                 # Total time to maturity in years
    spot_price: float
    risk_free_rate: float          # Annualized risk-free interest rate e.g. 0.05

    @property
    def strike_var(self) -> float:
        """Strike in variance terms (K_var = K_vol^2)."""
        return self.strike_vol_pct ** 2

    @property
    def variance_notional_usd(self) -> float:
        """Variance Notional N_var = N_vega / (2 * K_vol)."""
        if self.strike_vol_pct <= 0:
            raise VariancePricingError("Volatility strike must be positive.")
        return self.vega_notional_usd / (2.0 * self.strike_vol_pct)


@dataclass
class RealizedVarianceResult:
    annualized_realized_vol_pct: float
    annualized_realized_var: float
    num_observations: int
    daily_returns_variance: float


@dataclass
class FairStrikeResult:
    fair_variance_strike: float    # K_var (e.g. 400.0 for 20% vol)
    fair_volatility_strike: float  # K_vol (e.g. 20.0%)
    convexity_adjustment_pct: float # K_var - K_vol^2 adjustment
    forward_price: float
    num_options_used: int


@dataclass
class VarianceSwapMTMResult:
    contract_id: str
    current_mtm_usd: float
    realized_vol_so_far_pct: float
    realized_var_so_far: float
    fair_remaining_var_strike: float
    unrealized_pnl_usd: float


class VarianceSwapPricingEngine:
    """
    Institutional Variance Swap & Volatility Derivative Pricing Engine.
    
    Implements Demeterfi, Derman, Kamal, & Zou (1999) static replication of variance swaps
    using out-of-the-money (OTM) European option strips, calculates realized log-return variance,
    applies volatility swap convexity adjustments, and computes Mark-to-Market (MTM) valuations.
    """

    def __init__(self):
        logger.info("Initialized Quantitative Variance Swap Pricing Engine")

    @staticmethod
    def calculate_realized_variance(
        price_history: List[float], annualization_factor: int = 252
    ) -> RealizedVarianceResult:
        """
        Calculates annualized realized variance from daily price history using log-returns.
        
        Realized Variance = (252 / N) * SUM( ln(S_i / S_{i-1})^2 ) * 10,000
        """
        if len(price_history) < 2:
            raise VariancePricingError("At least 2 price observations required to calculate realized variance.")

        log_returns = []
        for i in range(1, len(price_history)):
            if price_history[i - 1] <= 0 or price_history[i] <= 0:
                raise VariancePricingError("Price observations must be positive.")
            r = math.log(price_history[i] / price_history[i - 1])
            log_returns.append(r)

        n = len(log_returns)
        sum_sq_returns = sum(r ** 2 for r in log_returns)

        # Annualized variance in percentage-squared units (10,000 factor converts 0.04 -> 400.0)
        daily_variance = sum_sq_returns / n
        annualized_var = (annualization_factor / n) * sum_sq_returns * 10_000.0
        annualized_vol = math.sqrt(max(0.0, annualized_var))

        return RealizedVarianceResult(
            annualized_realized_vol_pct=annualized_vol,
            annualized_realized_var=annualized_var,
            num_observations=n,
            daily_returns_variance=daily_variance,
        )

    @staticmethod
    def calculate_fair_strikes(
        spot: float, r: float, t_years: float, option_strip: List[OptionQuote]
    ) -> FairStrikeResult:
        """
        Calculates fair variance strike (K_var) via static log-contract replication of OTM options.
        
        K_var = (2/T) * e^(r*T) * SUM( (delta_K_i / K_i^2) * Q(K_i) ) - (1/T) * [ F_0/S_0 - 1 - ln(F_0/S_0) ]
        """
        if spot <= 0 or t_years <= 0:
            raise VariancePricingError("Spot price and time-to-maturity must be positive.")
        if not option_strip or len(option_strip) < 2:
            raise VariancePricingError("Option strip requires at least 2 quotes across strikes.")

        # Forward price F_0 = Spot * e^(r * T)
        forward = spot * math.exp(r * t_years)

        # Sort option strip by strike price ascending
        sorted_strip = sorted(option_strip, key=lambda x: x.strike)
        n = len(sorted_strip)

        integral_sum = 0.0
        for i in range(n):
            q = sorted_strip[i]
            k_i = q.strike

            # Compute strike grid width delta_K_i
            if i == 0:
                delta_k = sorted_strip[1].strike - sorted_strip[0].strike
            elif i == n - 1:
                delta_k = sorted_strip[n - 1].strike - sorted_strip[n - 2].strike
            else:
                delta_k = (sorted_strip[i + 1].strike - sorted_strip[i - 1].strike) / 2.0

            # Select OTM Option: Put for K < F_0, Call for K >= F_0
            if k_i < forward and q.option_type == OptionType.PUT:
                weight = delta_k / (k_i ** 2)
                integral_sum += weight * q.price
            elif k_i >= forward and q.option_type == OptionType.CALL:
                weight = delta_k / (k_i ** 2)
                integral_sum += weight * q.price

        # Static log-contract replication variance formula
        discount_factor = math.exp(r * t_years)
        option_part = (2.0 / t_years) * discount_factor * integral_sum
        adjustment_part = (1.0 / t_years) * ((forward / spot) - 1.0 - math.log(forward / spot))

        fair_var_raw = option_part - adjustment_part
        # Convert raw variance (e.g. 0.04) to percentage-squared units (e.g. 400.0)
        fair_var = max(0.0, fair_var_raw * 10_000.0)

        # Volatility Swap Fair Strike with convexity adjustment (K_vol approx sqrt(K_var))
        fair_vol = math.sqrt(fair_var)
        convexity_adj = fair_var - (fair_vol ** 2)

        return FairStrikeResult(
            fair_variance_strike=fair_var,
            fair_volatility_strike=fair_vol,
            convexity_adjustment_pct=convexity_adj,
            forward_price=forward,
            num_options_used=n,
        )

    def price_variance_swap_mtm(
        self,
        contract: VarianceSwapContract,
        elapsed_t_years: float,
        price_history: List[float],
        remaining_option_strip: List[OptionQuote],
    ) -> VarianceSwapMTMResult:
        """
        Computes Mark-to-Market (MTM) present value of an active Variance Swap.
        
        MTM = PV * N_var * [ (t_elapsed / T) * Realized_Var + (t_remaining / T) * Fair_Remaining_Var - Strike_Var ]
        """
        if elapsed_t_years < 0 or elapsed_t_years > contract.t_years:
            raise VariancePricingError("Elapsed time must be between 0 and contract maturity T.")

        t_total = contract.t_years
        t_remaining = t_total - elapsed_t_years
        discount_factor = math.exp(-contract.risk_free_rate * t_remaining)

        # 1. Realized Variance so far
        if elapsed_t_years > 0 and len(price_history) >= 2:
            realized_res = self.calculate_realized_variance(price_history)
            realized_var_so_far = realized_res.annualized_realized_var
            realized_vol_so_far = realized_res.annualized_realized_vol_pct
        else:
            realized_var_so_far = contract.strike_var
            realized_vol_so_far = contract.strike_vol_pct

        # 2. Fair remaining variance strike from current option strip
        if t_remaining > 1e-4 and remaining_option_strip:
            fair_res = self.calculate_fair_strikes(
                contract.spot_price, contract.risk_free_rate, t_remaining, remaining_option_strip
            )
            fair_rem_var = fair_res.fair_variance_strike
        else:
            fair_rem_var = realized_var_so_far

        # 3. Expected Total Variance at Maturity
        weight_elapsed = elapsed_t_years / t_total
        weight_remaining = t_remaining / t_total
        expected_total_var = (weight_elapsed * realized_var_so_far) + (weight_remaining * fair_rem_var)

        # 4. Present Value MTM
        var_diff = expected_total_var - contract.strike_var
        mtm_usd = discount_factor * contract.variance_notional_usd * var_diff

        logger.info(
            f"Variance Swap MTM [{contract.contract_id}]: RealizedVol={realized_vol_so_far:.2f}%, "
            f"FairRemVar={fair_rem_var:.2f}, ExpTotalVar={expected_total_var:.2f}, MTM=${mtm_usd:,.2f}"
        )

        return VarianceSwapMTMResult(
            contract_id=contract.contract_id,
            current_mtm_usd=mtm_usd,
            realized_vol_so_far_pct=realized_vol_so_far,
            realized_var_so_far=realized_var_so_far,
            fair_remaining_var_strike=fair_rem_var,
            unrealized_pnl_usd=mtm_usd,
        )


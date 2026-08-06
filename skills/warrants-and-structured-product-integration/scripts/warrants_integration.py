import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class WarrantType(Enum):
    COVERED_CALL = "COVERED_CALL"               # European/American Covered Call Warrant
    COVERED_PUT = "COVERED_PUT"                 # European/American Covered Put Warrant
    TURBO_BULL_CBBC = "TURBO_BULL_CBBC"         # Callable Bull Contract (Knock-out Call)
    TURBO_BEAR_CBBC = "TURBO_BEAR_CBBC"         # Callable Bear Contract (Knock-out Put)
    AUTOCALLABLE_NOTE = "AUTOCALLABLE_NOTE"     # Yield-enhancement structured product


class SettlementType(Enum):
    CASH_SETTLED = "CASH_SETTLED"
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"


class KnockOutStatus(Enum):
    ACTIVE = "ACTIVE"
    KNOCKED_OUT = "KNOCKED_OUT"
    EXPIRED = "EXPIRED"


class WarrantEngineError(Exception):
    """Base exception for Warrants Integration Engine errors."""
    pass


def standard_normal_cdf(x: float) -> float:
    """Cumulative distribution function for standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def standard_normal_pdf(x: float) -> float:
    """Probability density function for standard normal distribution."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


@dataclass
class WarrantContract:
    warrant_id: str
    symbol: str
    underlying_symbol: str
    warrant_type: WarrantType
    strike_price: float
    barrier_price: float = 0.0                  # Knock-out price for Turbos/CBBCs
    entitlement_ratio: float = 0.1              # E.g., 0.1 = 10 warrants for 1 share
    days_to_expiry: int = 90
    risk_free_rate: float = 0.03
    implied_volatility: float = 0.30
    settlement_type: SettlementType = SettlementType.CASH_SETTLED
    status: KnockOutStatus = KnockOutStatus.ACTIVE


@dataclass
class WarrantValuation:
    warrant_id: str
    spot_price: float
    fair_price_usd: float
    intrinsic_value_usd: float
    time_value_usd: float
    delta: float
    gamma: float
    theta: float
    vega: float
    simple_gearing: float
    effective_gearing: float
    status: KnockOutStatus


@dataclass
class DeltaHedgeSignal:
    warrant_id: str
    underlying_symbol: str
    position_warrants: int
    required_underlying_delta_shares: float
    net_rebalance_shares: float
    action: str                                # "BUY", "SELL", "HOLD"


class WarrantsIntegrationEngine:
    """
    Institutional Warrants & Structured Product Integration Engine.
    
    Prices Covered Warrants, Turbo Warrants / CBBCs, and Structured Products using Black-Scholes pricing,
    models Entitlement Ratios ($R_{\text{ent}}$), evaluates Mandatory Call Event (MCE / Knock-out) barriers,
    computes Effective Gearing (Leverage Factor) and Black-Scholes Greeks ($\Delta, \Gamma, \Theta, \text{Vega}$),
    and generates real-time Delta Hedge rebalancing signals.
    """

    def __init__(self):
        logger.info("Initialized Warrants & Structured Product Integration Engine")

    def price_warrant(self, spot_price: float, contract: WarrantContract) -> WarrantValuation:
        """
        Prices a warrant contract using Black-Scholes model scaled by entitlement ratio.
        """
        if spot_price <= 0 or contract.strike_price <= 0:
            raise WarrantEngineError("Spot and strike prices must be positive.")

        r = contract.risk_free_rate
        sigma = contract.implied_volatility
        T = max(0.0001, contract.days_to_expiry / 365.0)
        K = contract.strike_price
        R = contract.entitlement_ratio

        # Check Knock-out for Turbo Warrants / CBBCs
        if contract.warrant_type == WarrantType.TURBO_BULL_CBBC and spot_price <= contract.barrier_price:
            logger.warning(f"CBBC KNOCK-OUT EVENT [{contract.symbol}]: Spot ${spot_price:.2f} <= Barrier ${contract.barrier_price:.2f}")
            return WarrantValuation(
                warrant_id=contract.warrant_id,
                spot_price=spot_price,
                fair_price_usd=0.0,
                intrinsic_value_usd=0.0,
                time_value_usd=0.0,
                delta=0.0,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                simple_gearing=0.0,
                effective_gearing=0.0,
                status=KnockOutStatus.KNOCKED_OUT,
            )

        if contract.warrant_type == WarrantType.TURBO_BEAR_CBBC and spot_price >= contract.barrier_price:
            logger.warning(f"CBBC KNOCK-OUT EVENT [{contract.symbol}]: Spot ${spot_price:.2f} >= Barrier ${contract.barrier_price:.2f}")
            return WarrantValuation(
                warrant_id=contract.warrant_id,
                spot_price=spot_price,
                fair_price_usd=0.0,
                intrinsic_value_usd=0.0,
                time_value_usd=0.0,
                delta=0.0,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                simple_gearing=0.0,
                effective_gearing=0.0,
                status=KnockOutStatus.KNOCKED_OUT,
            )

        # Black-Scholes d1, d2
        d1 = (math.log(spot_price / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        is_call = contract.warrant_type in (WarrantType.COVERED_CALL, WarrantType.TURBO_BULL_CBBC)

        if is_call:
            bs_price = spot_price * standard_normal_cdf(d1) - K * math.exp(-r * T) * standard_normal_cdf(d2)
            raw_delta = standard_normal_cdf(d1)
            intrinsic = max(0.0, spot_price - K) * R
        else:
            bs_price = K * math.exp(-r * T) * standard_normal_cdf(-d2) - spot_price * standard_normal_cdf(-d1)
            raw_delta = standard_normal_cdf(d1) - 1.0
            intrinsic = max(0.0, K - spot_price) * R

        # Scale by entitlement ratio R
        fair_price = max(0.0001, bs_price * R)
        time_val = max(0.0, fair_price - intrinsic)

        # Greeks (scaled by entitlement ratio R)
        warrant_delta = raw_delta * R
        gamma = (standard_normal_pdf(d1) / (spot_price * sigma * math.sqrt(T))) * R
        theta = (-(spot_price * standard_normal_pdf(d1) * sigma) / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * standard_normal_cdf(d1 if is_call else -d2)) * (R / 365.0)
        vega = (spot_price * math.sqrt(T) * standard_normal_pdf(d1)) * (R / 100.0)

        # Gearing Calculations
        simple_gearing = (spot_price * R) / fair_price
        effective_gearing = simple_gearing * abs(raw_delta)

        logger.info(
            f"Warrant Valuation [{contract.symbol}]: FairPrice=${fair_price:.4f}, Delta={warrant_delta:.4f}, "
            f"EffectiveGearing={effective_gearing:.2f}x, Intrinsic=${intrinsic:.4f}"
        )

        return WarrantValuation(
            warrant_id=contract.warrant_id,
            spot_price=spot_price,
            fair_price_usd=round(fair_price, 4),
            intrinsic_value_usd=round(intrinsic, 4),
            time_value_usd=round(time_val, 4),
            delta=round(warrant_delta, 6),
            gamma=round(gamma, 6),
            theta=round(theta, 6),
            vega=round(vega, 6),
            simple_gearing=round(simple_gearing, 2),
            effective_gearing=round(effective_gearing, 2),
            status=KnockOutStatus.ACTIVE,
        )

    def calculate_delta_hedge_signal(
        self, valuation: WarrantValuation, position_warrants: int, current_underlying_hedged_shares: float
    ) -> DeltaHedgeSignal:
        """
        Calculates the required underlying equity shares to maintain a delta-neutral hedge.
        """
        if valuation.status == KnockOutStatus.KNOCKED_OUT:
            required_shares = 0.0
        else:
            # Total Underlying Shares = Position Warrants * Warrant Delta
            required_shares = position_warrants * valuation.delta

        net_rebalance = required_shares - current_underlying_hedged_shares

        if abs(net_rebalance) < 1.0:
            action = "HOLD"
        elif net_rebalance > 0:
            action = "BUY"
        else:
            action = "SELL"

        logger.info(
            f"Delta Hedge Signal [{valuation.warrant_id}]: RequiredShares={required_shares:.1f}, "
            f"Hedged={current_underlying_hedged_shares:.1f}, NetRebalance={net_rebalance:+.1f} -> Action={action}"
        )

        return DeltaHedgeSignal(
            warrant_id=valuation.warrant_id,
            underlying_symbol="UNDERLYING",
            position_warrants=position_warrants,
            required_underlying_delta_shares=round(required_shares, 2),
            net_rebalance_shares=round(net_rebalance, 2),
            action=action,
        )


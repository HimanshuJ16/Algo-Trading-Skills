import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TermStructureState(Enum):
    CONTANGO = "CONTANGO"               # F1 < F2 < F3 (Normal calm market, negative roll yield for longs)
    BACKWARDATION = "BACKWARDATION"     # F1 > F2 > F3 (Market stress/crisis, positive roll yield for longs)
    FLAT = "FLAT"                       # F1 approx equal to F2


class VIXStrategyType(Enum):
    ROLL_YIELD_HARVEST = "ROLL_YIELD_HARVEST"  # Short VIX futures / Long Inverse VIX ETPs in steep contango
    TAIL_RISK_HEDGE = "TAIL_RISK_HEDGE"        # Long VIX OTM Call Spreads in backwardation or market stress
    TERM_STRUCTURE_SPREAD = "TERM_STRUCTURE_SPREAD" # Long F2 / Short F1 calendar spread
    NEUTRAL = "NEUTRAL"                        # Cash / No position


class VIXEngineError(Exception):
    """Base exception for VIX Strategy Engine errors."""
    pass


@dataclass
class VIXFuturesContract:
    symbol: str
    expiry_date: datetime.date
    days_to_expiry: int
    price: float


@dataclass
class VIXTermStructure:
    spot_vix: float
    f1_contract: VIXFuturesContract
    f2_contract: VIXFuturesContract
    slope_f2_minus_f1: float
    state: TermStructureState
    annualized_roll_yield_pct: float


@dataclass
class VIXStrategySignal:
    strategy_type: VIXStrategyType
    recommended_position: str                  # E.g., "SHORT_F1_FUTURE", "LONG_VIX_CALL_SPREAD"
    target_contracts: int
    daily_roll_decay_usd: float
    tail_hedge_protection_usd: float
    rationale: str


class VIXStrategyEngine:
    """
    Institutional VIX & Volatility Index Derivative Strategy Engine.
    
    Analyzes VIX Futures Term Structure (Contango vs Backwardation), calculates annualized roll yield decay,
    executes VIX Roll Yield Harvesting (Short VIX Futures) and VIX Tail Risk Hedging (VIX Call Spreads),
    sizes derivatives contracts, and models forward volatility term structure dynamics.
    """

    def __init__(self, contango_threshold_pct: float = 2.0, backwardation_threshold_pct: float = -2.0):
        """
        :param contango_threshold_pct: Slope % threshold (F2 - F1)/F1 to qualify as Contango.
        :param backwardation_threshold_pct: Slope % threshold to qualify as Backwardation.
        """
        self.contango_threshold_pct = contango_threshold_pct
        self.backwardation_threshold_pct = backwardation_threshold_pct
        logger.info("Initialized VIX Strategy Engine")

    def analyze_term_structure(
        self, spot_vix: float, f1: VIXFuturesContract, f2: VIXFuturesContract
    ) -> VIXTermStructure:
        """
        Analyzes spot VIX and front two futures contracts to determine term structure state and roll yield.
        """
        if spot_vix <= 0 or f1.price <= 0 or f2.price <= 0:
            raise VATEngineError if 'VATEngineError' in globals() else VIXEngineError("VIX prices must be positive.")

        slope = f2.price - f1.price
        slope_pct = (slope / f1.price) * 100.0

        # Determine Term Structure State
        if slope_pct >= self.contango_threshold_pct:
            state = TermStructureState.CONTANGO
        elif slope_pct <= self.backwardation_threshold_pct:
            state = TermStructureState.BACKWARDATION
        else:
            state = TermStructureState.FLAT

        # Calculate Annualized Roll Yield: ((F1 - Spot) / Spot) * (365 / days_to_expiry)
        days = max(1, f1.days_to_expiry)
        roll_yield_pct = ((f1.price - spot_vix) / spot_vix) * (365.0 / days) * 100.0

        logger.info(
            f"VIX Term Structure: Spot={spot_vix:.2f}, F1={f1.price:.2f}, F2={f2.price:.2f}, "
            f"Slope={slope:.2f} ({slope_pct:.2f}%), State={state.value}, AnnualizedRollYield={roll_yield_pct:.2f}%"
        )

        return VIXTermStructure(
            spot_vix=spot_vix,
            f1_contract=f1,
            f2_contract=f2,
            slope_f2_minus_f1=round(slope, 4),
            state=state,
            annualized_roll_yield_pct=round(roll_yield_pct, 2),
        )

    def generate_strategy_signal(
        self, term_struct: VIXTermStructure, portfolio_equity_usd: float, risk_tolerance: str = "MODERATE"
    ) -> VIXStrategySignal:
        """
        Generates tactical VIX strategy signals (Roll Yield Harvest vs Tail Protection) based on term structure.
        """
        if portfolio_equity_usd <= 0:
            raise VIXEngineError("Portfolio equity must be positive.")

        f1_price = term_struct.f1_contract.price
        contract_multiplier = 1000.0  # Cboe VIX Futures contract multiplier ($1,000 per index point)

        # 1. Contango Market: Roll Yield Harvest Strategy (Short VIX Futures)
        if term_struct.state == TermStructureState.CONTANGO:
            # Allocate 5% of portfolio equity to short VIX futures
            target_notional = portfolio_equity_usd * 0.05
            contracts = max(1, int(target_notional / (f1_price * contract_multiplier)))

            # Daily Roll Decay (USD earned per day from contango decay)
            daily_decay = (term_struct.f1_contract.price - term_struct.spot_vix) / max(1, term_struct.f1_contract.days_to_expiry)
            daily_roll_decay_usd = round(daily_decay * contract_multiplier * contracts, 2)

            return VIXStrategySignal(
                strategy_type=VIXStrategyType.ROLL_YIELD_HARVEST,
                recommended_position="SHORT_F1_VIX_FUTURE",
                target_contracts=contracts,
                daily_roll_decay_usd=daily_roll_decay_usd,
                tail_hedge_protection_usd=0.0,
                rationale=f"Steep Contango ({term_struct.slope_f2_minus_f1:.2f} pts). Harvesting negative roll yield via short F1 futures.",
            )

        # 2. Backwardation Market: Tail Risk Protection Strategy (Long VIX Calls / Spreads)
        elif term_struct.state == TermStructureState.BACKWARDATION:
            # Allocate 2% of portfolio equity to long VIX Call Spreads for crash protection
            target_notional = portfolio_equity_usd * 0.02
            contracts = max(1, int(target_notional / (f1_price * contract_multiplier)))
            protection_usd = round(contracts * contract_multiplier * 15.0, 2)  # 15 index point upside spread payoff

            return VIXStrategySignal(
                strategy_type=VIXStrategyType.TAIL_RISK_HEDGE,
                recommended_position="LONG_VIX_CALL_SPREAD",
                target_contracts=contracts,
                daily_roll_decay_usd=0.0,
                tail_hedge_protection_usd=protection_usd,
                rationale=f"VIX Backwardation ({term_struct.slope_f2_minus_f1:.2f} pts). Severe volatility spike/market crash signal active.",
            )

        # 3. Flat Term Structure: Neutral Position
        return VIXStrategySignal(
            strategy_type=VIXStrategyType.NEUTRAL,
            recommended_position="CASH",
            target_contracts=0,
            daily_roll_decay_usd=0.0,
            tail_hedge_protection_usd=0.0,
            rationale="Flat VIX term structure. Insufficient roll yield or tail risk signal.",
        )

    def price_vix_call_spread(
        self, f1_futures_price: float, strike_lower: float, strike_upper: float, days_to_expiry: int
    ) -> Tuple[float, float]:
        """
        Approximates intrinsic payoff and max loss for a VIX Call Spread (priced off F1 Futures price).
        
        Returns (max_profit_per_contract_usd, max_loss_per_contract_usd).
        """
        if strike_upper <= strike_lower:
            raise VIXEngineError("Upper strike must be greater than lower strike.")

        spread_width = strike_upper - strike_lower
        contract_multiplier = 1000.0

        max_profit_usd = spread_width * contract_multiplier
        # Assume net debit is approximately 25% of spread width
        max_loss_usd = (spread_width * 0.25) * contract_multiplier

        logger.info(f"VIX Call Spread [{strike_lower:.0f}/{strike_upper:.0f}]: MaxProfit=${max_profit_usd:,.2f}, MaxLoss=${max_loss_usd:,.2f}")
        return max_profit_usd, max_loss_usd


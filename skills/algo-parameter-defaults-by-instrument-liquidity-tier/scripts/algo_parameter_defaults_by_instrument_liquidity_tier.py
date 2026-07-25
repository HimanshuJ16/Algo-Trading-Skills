"""
algo-parameter-defaults-by-instrument-liquidity-tier: 
Assigns adaptive execution profiles based on Average Daily Volume (ADV).
"""
import dataclasses
import logging
from enum import Enum, auto

logger = logging.getLogger(__name__)

class LiquidityTier(Enum):
    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()


@dataclasses.dataclass
class ExecutionProfile:
    tier: LiquidityTier
    max_participation_rate: float
    default_algo_type: str  # TWAP, VWAP, or IS (Implementation Shortfall)
    cross_spread_allowed: bool
    passive_buffer_bps: float  # How far away from mid to peg passive orders


class ExecutionParameterManager:
    """
    Manages and assigns execution parameters based on ADV thresholds.
    """
    def __init__(self, high_adv_threshold: float = 10_000_000.0, medium_adv_threshold: float = 1_000_000.0):
        self.high_adv_threshold = high_adv_threshold
        self.medium_adv_threshold = medium_adv_threshold

    def classify_tier(self, adv: float) -> LiquidityTier:
        if adv >= self.high_adv_threshold:
            return LiquidityTier.HIGH
        elif adv >= self.medium_adv_threshold:
            return LiquidityTier.MEDIUM
        else:
            return LiquidityTier.LOW

    def get_profile(self, adv: float) -> ExecutionProfile:
        """
        Returns the optimal execution parameters for the given liquidity profile.
        """
        tier = self.classify_tier(adv)
        
        if tier == LiquidityTier.HIGH:
            # Liquid names: can be passive, low participation prevents signaling
            profile = ExecutionProfile(
                tier=tier,
                max_participation_rate=0.05,  # 5% of volume
                default_algo_type="TWAP",
                cross_spread_allowed=True,    # Spread is tight enough to cross if needed
                passive_buffer_bps=1.0        # Peg very close to the touch
            )
        elif tier == LiquidityTier.MEDIUM:
            profile = ExecutionProfile(
                tier=tier,
                max_participation_rate=0.10,  # 10% of volume
                default_algo_type="VWAP",
                cross_spread_allowed=False,   # Prefer passive fills
                passive_buffer_bps=5.0
            )
        else:
            # Illiquid names: Must capture available liquidity, but strictly limit price impact
            profile = ExecutionProfile(
                tier=tier,
                max_participation_rate=0.20,  # 20% of volume (when it actually trades)
                default_algo_type="IS",       # Implementation Shortfall (front-loaded)
                cross_spread_allowed=False,   # Never cross wide spreads
                passive_buffer_bps=20.0       # Sit deeper in the book
            )
            
        logger.info(f"Assigned {tier.name} liquidity profile for ADV {adv:,.0f}.")
        return profile

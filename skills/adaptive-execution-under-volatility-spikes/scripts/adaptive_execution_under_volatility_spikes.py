import dataclasses
import logging
from typing import Dict, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

class MarketRegime(Enum):
    NORMAL = "NORMAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    CRITICAL_SHOCK = "CRITICAL_SHOCK"

@dataclasses.dataclass
class AdaptiveVolatilityConfig:
    enabled: bool = True
    base_participation_rate: float = 0.10  # e.g., 10% of volume
    base_child_order_size: int = 100
    volatility_threshold_high: float = 2.0  # e.g., 2 standard deviations
    volatility_threshold_critical: float = 5.0
    limit_offset_bps_normal: int = 5
    limit_offset_bps_high: int = 15

@dataclasses.dataclass
class ExecutionParameters:
    participation_rate: float
    child_order_size: int
    limit_offset_bps: int
    halt_trading: bool

class AdaptiveExecutionUnderVolatilitySpikesEngine:
    """
    Dynamically adjusts execution parameters (child order size, participation rate) 
    based on real-time volatility to protect against flash crashes and toxic liquidity.
    """
    def __init__(self, config: AdaptiveVolatilityConfig):
        self.config = config
        self.current_regime = MarketRegime.NORMAL

    def _determine_regime(self, current_volatility: float) -> MarketRegime:
        if current_volatility >= self.config.volatility_threshold_critical:
            return MarketRegime.CRITICAL_SHOCK
        elif current_volatility >= self.config.volatility_threshold_high:
            return MarketRegime.HIGH_VOLATILITY
        return MarketRegime.NORMAL

    def evaluate(self, market_data: Dict[str, Any]) -> ExecutionParameters:
        """
        Evaluates current market data and returns adaptive execution parameters.
        market_data must contain 'current_volatility' (e.g., z-score of short-term variance).
        """
        if not self.config.enabled:
            return ExecutionParameters(
                self.config.base_participation_rate,
                self.config.base_child_order_size,
                self.config.limit_offset_bps_normal,
                halt_trading=False
            )
            
        current_vol = market_data.get("current_volatility", 0.0)
        self.current_regime = self._determine_regime(current_vol)
        
        logger.debug(f"Current Market Regime: {self.current_regime.name} (Vol: {current_vol:.2f})")

        if self.current_regime == MarketRegime.CRITICAL_SHOCK:
            logger.warning("CRITICAL SHOCK DETECTED. Halting execution.")
            return ExecutionParameters(0.0, 0, 0, halt_trading=True)
            
        elif self.current_regime == MarketRegime.HIGH_VOLATILITY:
            # Defensive execution: Halve participation rate, widen limit offsets
            return ExecutionParameters(
                participation_rate=self.config.base_participation_rate * 0.5,
                child_order_size=max(1, int(self.config.base_child_order_size * 0.5)),
                limit_offset_bps=self.config.limit_offset_bps_high,
                halt_trading=False
            )
            
        # Normal execution
        return ExecutionParameters(
            participation_rate=self.config.base_participation_rate,
            child_order_size=self.config.base_child_order_size,
            limit_offset_bps=self.config.limit_offset_bps_normal,
            halt_trading=False
        )

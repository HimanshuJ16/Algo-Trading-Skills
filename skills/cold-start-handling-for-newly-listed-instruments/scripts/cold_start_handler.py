import logging
from dataclasses import dataclass
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

@dataclass
class InstrumentStatus:
    symbol: str
    n_obs: int
    is_probationary: bool
    confidence_weight: float    # 0.0 to 1.0
    estimated_volatility: float
    max_position_cap_pct: float

class ColdStartHandler:
    """
    Manages cold-start onboarding for newly listed instruments (IPOs/SPACs).
    Applies Bayesian shrinkage toward peer priors and scales risk allocations.
    """
    def __init__(self, warmup_period_days: int = 30, base_max_position_pct: float = 1.0):
        self.warmup_period_days = warmup_period_days
        self.base_max_position_pct = base_max_position_pct

    def calculate_shrinkage_weight(self, n_obs: int) -> float:
        """
        Calculates linear shrinkage weight w in [0.0, 1.0].
        """
        if n_obs < 0:
            raise ValueError("Observation count cannot be negative.")
        return float(min(1.0, max(0.0, n_obs / self.warmup_period_days)))

    def process_instrument(
        self,
        symbol: str,
        n_obs: int,
        observed_volatility: float,
        peer_prior_volatility: float
    ) -> InstrumentStatus:
        """
        Evaluates instrument history, applies volatility shrinkage, and returns status.
        """
        w = self.calculate_shrinkage_weight(n_obs)
        is_probationary = w < 1.0
        
        # Bayesian Shrinkage: w * observed + (1 - w) * prior
        shrunken_vol = (w * observed_volatility) + ((1.0 - w) * peer_prior_volatility)
        
        # Position sizing cap scales with confidence weight
        max_cap = self.base_max_position_pct * w if is_probationary else self.base_max_position_pct
        
        if is_probationary:
            logger.info(
                f"Instrument {symbol} is PROBATIONARY ({n_obs}/{self.warmup_period_days} days). "
                f"Shrunken Vol: {shrunken_vol:.4f}, Position Cap: {max_cap:.2%}"
            )
        else:
            logger.info(f"Instrument {symbol} is GRADUATED. Vol: {shrunken_vol:.4f}")
            
        return InstrumentStatus(
            symbol=symbol,
            n_obs=n_obs,
            is_probationary=is_probationary,
            confidence_weight=round(w, 4),
            estimated_volatility=round(shrunken_vol, 4),
            max_position_cap_pct=round(max_cap, 4)
        )

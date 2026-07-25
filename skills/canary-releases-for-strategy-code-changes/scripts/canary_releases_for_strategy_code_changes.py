import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

class DeploymentPhase(Enum):
    SHADOW = 1
    CANARY = 2
    PRODUCTION = 3

@dataclass
class StrategyRegistration:
    strategy_id: str
    phase: DeploymentPhase
    canary_scale_factor: float = 0.05  # Default 5%
    min_lot_size: int = 1

@dataclass
class OrderSignal:
    strategy_id: str
    symbol: str
    quantity: int
    price: float
    side: str

class StrategyCanaryRouter:
    """
    Routes quantitative strategy signals based on deployment lifecycle phases.
    Intercepts and scales orders safely before they hit the execution gateway.
    """
    def __init__(self):
        self.strategies = {}

    def register_strategy(self, registration: StrategyRegistration):
        if registration.canary_scale_factor <= 0 or registration.canary_scale_factor >= 1.0:
            raise ValueError("Canary scale factor must be strictly between 0 and 1.0")
        self.strategies[registration.strategy_id] = registration
        logger.info(f"Registered Strategy {registration.strategy_id} in {registration.phase.name} mode.")

    def route_order(self, signal: OrderSignal) -> Optional[OrderSignal]:
        if signal.strategy_id not in self.strategies:
            logger.error(f"Unregistered strategy: {signal.strategy_id}. Rejecting signal.")
            return None

        reg = self.strategies[signal.strategy_id]

        if reg.phase == DeploymentPhase.SHADOW:
            logger.info(f"SHADOW: Intercepted signal for {signal.quantity} {signal.symbol}. Logged. Dropping execution.")
            return None
            
        elif reg.phase == DeploymentPhase.CANARY:
            scaled_qty = int(signal.quantity * reg.canary_scale_factor)
            
            # Apply lot size rounding (floor to nearest lot)
            remainder = scaled_qty % reg.min_lot_size
            final_qty = scaled_qty - remainder
            
            if final_qty <= 0:
                logger.warning(f"CANARY: Scaled quantity {scaled_qty} is below min lot size {reg.min_lot_size}. Dropping.")
                return None
                
            logger.info(f"CANARY: Scaling {signal.quantity} down to {final_qty} {signal.symbol}.")
            signal.quantity = final_qty
            return signal
            
        elif reg.phase == DeploymentPhase.PRODUCTION:
            logger.info(f"PRODUCTION: Routing full {signal.quantity} {signal.symbol}.")
            return signal

        return None

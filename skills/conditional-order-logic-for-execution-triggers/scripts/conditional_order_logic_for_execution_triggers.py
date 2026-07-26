import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class BaseCondition(ABC):
    """Abstract base class for all conditional order trigger nodes."""
    @abstractmethod
    def evaluate(self, market_state: Dict[str, Dict[str, Any]]) -> bool:
        pass

class PriceCondition(BaseCondition):
    """Evaluates a single symbol's price field against a threshold."""
    def __init__(self, symbol: str, field: str, operator: str, target_value: float):
        self.symbol = symbol
        self.field = field              # 'last', 'bid', 'ask'
        self.operator = operator        # '>=', '<=', '==', '>'
        self.target_value = target_value

    def evaluate(self, market_state: Dict[str, Dict[str, Any]]) -> bool:
        if self.symbol not in market_state or self.field not in market_state[self.symbol]:
            return False
            
        val = market_state[self.symbol][self.field]
        if self.operator == '>=':
            return val >= self.target_value
        elif self.operator == '<=':
            return val <= self.target_value
        elif self.operator == '>':
            return val > self.target_value
        elif self.operator == '<':
            return val < self.target_value
        elif self.operator == '==':
            return val == self.target_value
        return False

class VolumeCondition(BaseCondition):
    """Evaluates cumulative volume or size threshold."""
    def __init__(self, symbol: str, field: str, min_volume: float):
        self.symbol = symbol
        self.field = field              # 'volume', 'bid_size', 'ask_size'
        self.min_volume = min_volume

    def evaluate(self, market_state: Dict[str, Dict[str, Any]]) -> bool:
        if self.symbol not in market_state or self.field not in market_state[self.symbol]:
            return False
        return market_state[self.symbol][self.field] >= self.min_volume

class AndCondition(BaseCondition):
    """Composite Boolean AND gate."""
    def __init__(self, conditions: List[BaseCondition]):
        self.conditions = conditions

    def evaluate(self, market_state: Dict[str, Dict[str, Any]]) -> bool:
        return all(cond.evaluate(market_state) for cond in self.conditions)

class OrCondition(BaseCondition):
    """Composite Boolean OR gate."""
    def __init__(self, conditions: List[BaseCondition]):
        self.conditions = conditions

    def evaluate(self, market_state: Dict[str, Dict[str, Any]]) -> bool:
        return any(cond.evaluate(market_state) for cond in self.conditions)

@dataclass
class ChildOrderPayload:
    symbol: str
    side: str                          # 'BUY' or 'SELL'
    quantity: int
    order_type: str                    # 'LIMIT' or 'MARKET'
    price: Optional[float] = None

class ConditionalOrderTrigger:
    """
    Holds a conditional order payload coupled with a Boolean condition tree.
    Enforces atomic single-fire transition from DORMANT -> TRIGGERED.
    """
    def __init__(self, trigger_id: str, condition_tree: BaseCondition, child_order: ChildOrderPayload):
        self.trigger_id = trigger_id
        self.condition_tree = condition_tree
        self.child_order = child_order
        self.status = "DORMANT"        # 'DORMANT', 'TRIGGERED', 'CANCELLED'

    def process_tick(self, market_state: Dict[str, Dict[str, Any]]) -> Optional[ChildOrderPayload]:
        if self.status != "DORMANT":
            return None

        if self.condition_tree.evaluate(market_state):
            self.status = "TRIGGERED"
            logger.info(f"Conditional Order Trigger {self.trigger_id} FIRED! Status set to TRIGGERED.")
            return self.child_order
            
        return None

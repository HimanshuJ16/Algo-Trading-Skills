import dataclasses
from typing import List, Dict, Any

@dataclasses.dataclass
class CloseAuctionParticipationStrategyConfig:
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100

class CloseAuctionParticipationStrategyEngine:
    def __init__(self, config: CloseAuctionParticipationStrategyConfig):
        self.config = config
        self.state = "INITIALIZED"
        self.orders = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.config.enabled:
            return []
            
        if market_data.get("price", 0) > self.config.threshold:
            order = {"symbol": market_data.get("symbol", "UNKNOWN"), "qty": self.config.size, "type": "LIMIT"}
            self.orders.append(order)
            return [order]
        return []

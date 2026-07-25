"""
algo-wheel-broker-execution-quality-comparison: 
Evaluates broker performance via Implementation Shortfall and adjusts routing weights.
"""
import dataclasses
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BrokerExecution:
    broker_id: str
    side: str  # 'BUY' or 'SELL'
    decision_price: float  # The price when the algo decided to trade (Arrival Price)
    fill_price: float      # The actual average execution price
    quantity: float
    fees_usd: float        # Explicit commissions and taxes


class AlgoWheelEvaluator:
    """
    Computes Implementation Shortfall (IS) to rank broker performance 
    and output target order flow allocations (the 'Wheel').
    """
    def __init__(self, min_allocation: float = 0.10):
        self.min_allocation = min_allocation  # Prevent starving bad brokers (keep testing them)

    def calculate_implementation_shortfall_bps(self, exec_data: BrokerExecution) -> float:
        """
        Implementation Shortfall (IS) = Slippage + Fees.
        Lower IS is better (0 bps is perfect execution at decision price).
        """
        notional = exec_data.decision_price * exec_data.quantity
        if notional <= 0:
            return 0.0

        fees_bps = (exec_data.fees_usd / notional) * 10000.0

        if exec_data.side.upper() == 'BUY':
            slippage_bps = ((exec_data.fill_price / exec_data.decision_price) - 1.0) * 10000.0
        elif exec_data.side.upper() == 'SELL':
            slippage_bps = ((exec_data.decision_price / exec_data.fill_price) - 1.0) * 10000.0
        else:
            raise ValueError(f"Invalid side: {exec_data.side}")

        total_is_bps = slippage_bps + fees_bps
        return round(total_is_bps, 2)

    def evaluate_brokers(self, executions: List[BrokerExecution]) -> Dict[str, float]:
        """
        Takes a list of historical executions, ranks the brokers by their average IS, 
        and outputs the new target allocation weights for the Algo Wheel.
        """
        if not executions:
            return {}

        broker_stats = {}
        for ex in executions:
            b_id = ex.broker_id
            is_bps = self.calculate_implementation_shortfall_bps(ex)
            
            if b_id not in broker_stats:
                broker_stats[b_id] = []
            broker_stats[b_id].append(is_bps)

        # Calculate average IS per broker
        avg_is_per_broker = {b_id: sum(scores)/len(scores) for b_id, scores in broker_stats.items()}
        
        # Sort brokers from best (lowest IS) to worst (highest IS)
        ranked_brokers = sorted(avg_is_per_broker.items(), key=lambda item: item[1])
        
        logger.info(f"Broker Rankings (Average IS bps): {ranked_brokers}")

        return self._assign_wheel_weights(ranked_brokers)

    def _assign_wheel_weights(self, ranked_brokers: List[tuple]) -> Dict[str, float]:
        """
        Assigns percentage of flow based on rank.
        Example for 3 brokers: 1st gets 60%, 2nd gets 30%, 3rd gets 10% (min_allocation).
        """
        num_brokers = len(ranked_brokers)
        allocations = {}
        
        if num_brokers == 0:
            return allocations
        if num_brokers == 1:
            allocations[ranked_brokers[0][0]] = 1.0
            return allocations

        # Allocate minimum flow to the worst broker(s) to keep benchmarking them
        remaining_flow = 1.0
        
        for i in range(num_brokers - 1, -1, -1):
            broker_id = ranked_brokers[i][0]
            if i == 0:
                # Top broker gets everything remaining
                allocations[broker_id] = round(remaining_flow, 2)
            elif i == num_brokers - 1:
                # Worst broker gets minimum allocation
                allocations[broker_id] = self.min_allocation
                remaining_flow -= self.min_allocation
            else:
                # Middle brokers get a proportional split of remaining (simplified logic)
                mid_alloc = round(remaining_flow / 2, 2)
                allocations[broker_id] = mid_alloc
                remaining_flow -= mid_alloc

        return allocations

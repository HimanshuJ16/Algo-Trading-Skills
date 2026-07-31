import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    param1: float = 1.0
    param2: str = "default"
    cancellation_share_alpha: float = 0.5 # Proportional cancellation weight

@dataclass
class PassiveOrderTracker:
    order_id: str
    side: str                            # 'BUY', 'SELL'
    price: float
    our_quantity: float
    initial_queue_ahead: float
    total_level_volume: float

@dataclass
class QueuePositionReport:
    order_id: str
    side: str
    price: float
    our_quantity: float
    current_queue_ahead: float
    estimated_queue_rank: int
    fill_probability: float
    is_front_of_queue: bool
    status: str                          # 'FRONT_OF_QUEUE', 'QUEUE_PRIORITY_TRACKING'
    audit_notes: str

class MainEngine:
    """
    Legacy MainEngine retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return True

    def process_data(self, data: float) -> float:
        return data * self.config.param1

class QueuePositionModelEngine:
    """
    FIFO limit order book queue position tracking engine estimating volume ahead,
    fill probabilities, and defensive order cancellations for passive limit orders.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def update_queue_position(
        self,
        tracker: PassiveOrderTracker,
        accumulated_fills: float,
        accumulated_cancellations: float,
        time_horizon_sec: float = 5.0,
        historical_fill_rate_per_sec: float = 50.0
    ) -> QueuePositionReport:
        """
        Updates volume ahead and estimates queue rank and fill probability.
        """
        q_ahead = tracker.initial_queue_ahead

        # 1. Fills directly consume volume ahead
        q_ahead -= accumulated_fills

        # 2. Cancellations ahead: proportional allocation
        total_vol = max(1.0, tracker.total_level_volume)
        prop_alpha = min(1.0, max(0.0, q_ahead / total_vol))
        cancellations_ahead = accumulated_cancellations * prop_alpha * self.config.cancellation_share_alpha
        q_ahead -= cancellations_ahead

        # Clamp queue ahead to zero minimum
        current_q_ahead = max(0.0, q_ahead)
        is_front = current_q_ahead == 0.0

        # Estimated rank (assuming average order size of 100)
        estimated_rank = int(current_q_ahead / 100.0) + 1

        # Fill Probability in next time_horizon_sec
        expected_fills = historical_fill_rate_per_sec * time_horizon_sec
        total_qty_needed = current_q_ahead + tracker.our_quantity
        fill_prob = min(1.0, expected_fills / max(1.0, total_qty_needed))

        status = "FRONT_OF_QUEUE" if is_front else "QUEUE_PRIORITY_TRACKING"
        notes = (
            f"QUEUE POSITION REPORT [{tracker.order_id} ({tracker.side} @ ${tracker.price:.2f}) - {status}]: "
            f"Init Ahead = {tracker.initial_queue_ahead:,.0f}, Current Ahead = {current_q_ahead:,.0f}, "
            f"Est Rank = #{estimated_rank}, Fill Prob ({time_horizon_sec}s) = {fill_prob * 100.0:.1f}%."
        )

        logger.info(notes)

        return QueuePositionReport(
            order_id=tracker.order_id,
            side=tracker.side.upper(),
            price=tracker.price,
            our_quantity=tracker.our_quantity,
            current_queue_ahead=round(current_q_ahead, 1),
            estimated_queue_rank=estimated_rank,
            fill_probability=round(fill_prob, 4),
            is_front_of_queue=is_front,
            status=status,
            audit_notes=notes
        )

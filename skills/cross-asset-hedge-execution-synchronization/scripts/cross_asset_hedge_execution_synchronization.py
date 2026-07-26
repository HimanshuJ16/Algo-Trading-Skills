import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class PrimaryFillEvent:
    strategy_id: str
    fill_id: str
    primary_symbol: str
    fill_qty: float                    # Positive for Buy, Negative for Sell
    fill_price: float
    timestamp_ms: float

@dataclass
class HedgeOrder:
    hedge_order_id: str
    primary_fill_id: str
    hedge_symbol: str
    target_hedge_qty: float            # Offset quantity calculated via hedge ratio
    side: str                          # 'BUY' or 'SELL'
    created_timestamp_ms: float

@dataclass
class HedgeSynchronizationStatus:
    strategy_id: str
    primary_fill_id: str
    hedge_order_id: str
    sync_delay_ms: float
    is_sync_sla_met: bool
    hedge_status: str                   # 'SYNCHRONIZED_OK', 'SYNC_DELAY_BREACH', 'UNHEDGED_TIMEOUT_UNWIND'
    unhedged_exposure_qty: float

class CrossAssetHedgeSynchronizer:
    """
    Execution synchronization engine for multi-leg strategies to eliminate legging risk
    and enforce sub-second hedge latency SLAs.
    """
    def __init__(
        self,
        strategy_id: str,
        primary_symbol: str,
        hedge_symbol: str,
        hedge_ratio: float = 1.0,      # e.g. -50 shares per +1 call option contract
        max_sync_delay_ms: float = 100.0,
        unhedged_timeout_ms: float = 500.0
    ):
        self.strategy_id = strategy_id
        self.primary_symbol = primary_symbol
        self.hedge_symbol = hedge_symbol
        self.hedge_ratio = hedge_ratio
        self.max_sync_delay_ms = max_sync_delay_ms
        self.unhedged_timeout_ms = unhedged_timeout_ms
        self.pending_hedges: Dict[str, HedgeOrder] = {}

    def generate_hedge_order(self, primary_fill: PrimaryFillEvent) -> HedgeOrder:
        """
        Calculates required hedge quantity and creates a HedgeOrder.
        Hedge Qty = -1.0 * Primary Fill Qty * Hedge Ratio
        """
        if primary_fill.primary_symbol != self.primary_symbol:
            raise ValueError(f"Primary symbol mismatch: {primary_fill.primary_symbol} vs {self.primary_symbol}")

        target_qty = -1.0 * primary_fill.fill_qty * self.hedge_ratio
        side = "BUY" if target_qty > 0 else "SELL"
        hedge_order_id = f"HEDGE_{primary_fill.fill_id}"

        hedge_order = HedgeOrder(
            hedge_order_id=hedge_order_id,
            primary_fill_id=primary_fill.fill_id,
            hedge_symbol=self.hedge_symbol,
            target_hedge_qty=round(target_qty, 4),
            side=side,
            created_timestamp_ms=primary_fill.timestamp_ms
        )

        self.pending_hedges[hedge_order_id] = hedge_order
        logger.info(
            f"Generated Hedge Order [{hedge_order_id}]: {side} {abs(target_qty)} {self.hedge_symbol} "
            f"for Primary Fill {primary_fill.fill_id} (+{primary_fill.fill_qty} {primary_fill.primary_symbol})"
        )
        return hedge_order

    def process_hedge_fill(
        self,
        hedge_order_id: str,
        filled_hedge_qty: float,
        hedge_fill_timestamp_ms: float
    ) -> HedgeSynchronizationStatus:
        """
        Processes hedge fill callback, calculates synchronization latency, and checks SLAs.
        """
        if hedge_order_id not in self.pending_hedges:
            raise ValueError(f"Unknown hedge order ID {hedge_order_id}")

        h_order = self.pending_hedges[hedge_order_id]
        sync_delay = float(hedge_fill_timestamp_ms - h_order.created_timestamp_ms)
        unhedged_qty = abs(h_order.target_hedge_qty) - abs(filled_hedge_qty)

        if sync_delay <= self.max_sync_delay_ms and abs(unhedged_qty) < 1e-4:
            status = "SYNCHRONIZED_OK"
            is_sla_met = True
        elif sync_delay <= self.unhedged_timeout_ms:
            status = "SYNC_DELAY_BREACH"
            is_sla_met = False
            logger.warning(
                f"Hedge Sync SLA Breached [{hedge_order_id}]: Delay = {sync_delay:.1f}ms > {self.max_sync_delay_ms}ms"
            )
        else:
            status = "UNHEDGED_TIMEOUT_UNWIND"
            is_sla_met = False
            logger.critical(
                f"UNHEDGED TIMEOUT ({sync_delay:.1f}ms): Triggering emergency primary leg unwind!"
            )

        del self.pending_hedges[hedge_order_id]

        return HedgeSynchronizationStatus(
            strategy_id=self.strategy_id,
            primary_fill_id=h_order.primary_fill_id,
            hedge_order_id=hedge_order_id,
            sync_delay_ms=round(sync_delay, 2),
            is_sync_sla_met=is_sla_met,
            hedge_status=status,
            unhedged_exposure_qty=round(unhedged_qty, 4)
        )

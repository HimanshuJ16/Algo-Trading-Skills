"""
automated-tax-lot-reporting-pipeline:
Engine for matching buy/sell trades to specific tax lots to calculate
realized capital gains, supporting FIFO and HIFO accounting methods.
"""
import dataclasses
import logging
from enum import Enum
from typing import List, Dict
import math

logger = logging.getLogger(__name__)


class LotMatchingStrategy(Enum):
    FIFO = "FIFO"  # First-In, First-Out (Oldest lots first)
    HIFO = "HIFO"  # Highest-In, First-Out (Highest cost basis first)


class TradeAction(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclasses.dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    action: TradeAction
    quantity: float
    price: float
    timestamp_ms: int


@dataclasses.dataclass
class TaxLot:
    lot_id: str
    symbol: str
    original_quantity: float
    remaining_quantity: float
    cost_basis_price: float
    timestamp_ms: int


@dataclasses.dataclass
class RealizedGainRecord:
    sell_trade_id: str
    symbol: str
    quantity_sold: float
    sell_price: float
    buy_lot_id: str
    cost_basis_price: float
    realized_pnl: float


def _validate_trade_record(trade: TradeRecord) -> None:
    """Validate TradeRecord parameters for correctness."""
    if not isinstance(trade.trade_id, str) or not trade.trade_id:
        raise ValueError("trade_id must be a non-empty string")

    if not isinstance(trade.symbol, str) or not trade.symbol:
        raise ValueError("symbol must be a non-empty string")

    if not isinstance(trade.action, TradeAction):
        raise ValueError("action must be a valid TradeAction")

    if not isinstance(trade.quantity, (int, float)) or math.isnan(trade.quantity):
        raise ValueError("quantity must be a valid number")

    if trade.quantity <= 0:
        raise ValueError("quantity must be positive")

    if not isinstance(trade.price, (int, float)) or math.isnan(trade.price):
        raise ValueError("price must be a valid number")

    if trade.price < 0:
        raise ValueError("price must be non-negative")

    if not isinstance(trade.timestamp_ms, int) or trade.timestamp_ms < 0:
        raise ValueError("timestamp_ms must be a non-negative integer")


def _sort_lots_for_strategy(lots: List[TaxLot], strategy: LotMatchingStrategy) -> List[TaxLot]:
    """
    Sort lots according to the specified matching strategy.

    Args:
        lots: List of TaxLot objects to sort
        strategy: The matching strategy to use

    Returns:
        New list of lots sorted according to the strategy
    """
    if strategy == LotMatchingStrategy.FIFO:
        # Sort by age (oldest timestamp first)
        return sorted(lots, key=lambda x: x.timestamp_ms)
    elif strategy == LotMatchingStrategy.HIFO:
        # Sort by highest cost basis first, then by age (oldest first for same cost basis)
        return sorted(lots, key=lambda x: (-x.cost_basis_price, x.timestamp_ms))
    else:
        raise ValueError(f"Unknown lot matching strategy: {strategy}")


def _calculate_pnl(sell_price: float, cost_basis_price: float, quantity: float) -> float:
    """
    Calculate profit and loss for a tax lot match.

    Args:
        sell_price: Price at which the asset was sold
        cost_basis_price: Original cost basis of the asset
        quantity: Quantity of assets matched

    Returns:
        Profit or loss (positive for gain, negative for loss)
    """
    if math.isnan(sell_price) or math.isnan(cost_basis_price) or math.isnan(quantity):
        raise ValueError("Price, cost basis, and quantity must be valid numbers")

    if quantity <= 0:
        raise ValueError("Quantity must be positive")

    if sell_price < 0 or cost_basis_price < 0:
        raise ValueError("Price and cost basis must be non-negative")

    return (sell_price - cost_basis_price) * quantity


class AutomatedTaxLotReportingPipelineEngine:
    def __init__(self, strategy: LotMatchingStrategy = LotMatchingStrategy.FIFO):
        self.strategy = strategy
        # Ledger mapping symbol -> list of open TaxLots
        self.open_lots: Dict[str, List[TaxLot]] = {}
        self.realized_gains: List[RealizedGainRecord] = []

    def process_trade(self, trade: TradeRecord) -> List[RealizedGainRecord]:
        # Validate input trade record
        _validate_trade_record(trade)

        if trade.action == TradeAction.BUY:
            self._handle_buy(trade)
            return []
        elif trade.action == TradeAction.SELL:
            return self._handle_sell(trade)
        return []

    def _handle_buy(self, trade: TradeRecord):
        lot = TaxLot(
            lot_id=trade.trade_id,
            symbol=trade.symbol,
            original_quantity=trade.quantity,
            remaining_quantity=trade.quantity,
            cost_basis_price=trade.price,
            timestamp_ms=trade.timestamp_ms
        )
        if trade.symbol not in self.open_lots:
            self.open_lots[trade.symbol] = []
        self.open_lots[trade.symbol].append(lot)
        logger.info(f"Created Tax Lot {lot.lot_id} for {trade.symbol}: {lot.remaining_quantity} @ {lot.cost_basis_price}")

    def _handle_sell(self, trade: TradeRecord) -> List[RealizedGainRecord]:
        gains_for_this_trade = []
        qty_to_sell = trade.quantity
        symbol = trade.symbol

        if symbol not in self.open_lots or len(self.open_lots[symbol]) == 0:
            error_msg = f"Cannot sell {qty_to_sell} of {symbol}. No open lots found (Naked Shorting not supported in this ledger)."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Sort the open lots based on the selected strategy
        lots = self.open_lots[symbol]
        sorted_lots = _sort_lots_for_strategy(lots, self.strategy)
        # Update the original list to match the sorted order
        self.open_lots[symbol][:] = sorted_lots

        # Consume lots until the sell order is fulfilled
        while qty_to_sell > 0 and len(lots) > 0:
            current_lot = lots[0]

            # Determine how much we can consume from this lot
            qty_consumed = min(qty_to_sell, current_lot.remaining_quantity)

            # Calculate PnL using the utility function
            realized_pnl = _calculate_pnl(trade.price, current_lot.cost_basis_price, qty_consumed)

            record = RealizedGainRecord(
                sell_trade_id=trade.trade_id,
                symbol=symbol,
                quantity_sold=qty_consumed,
                sell_price=trade.price,
                buy_lot_id=current_lot.lot_id,
                cost_basis_price=current_lot.cost_basis_price,
                realized_pnl=realized_pnl
            )
            gains_for_this_trade.append(record)
            self.realized_gains.append(record)

            logger.info(f"Matched SELL {trade.trade_id} against LOT {current_lot.lot_id}. Qty: {qty_consumed}, PnL: {realized_pnl:.2f}")

            # Deduct quantities
            qty_to_sell -= qty_consumed
            current_lot.remaining_quantity -= qty_consumed

            # If the lot is completely consumed, remove it from the ledger to prevent memory leaks
            if current_lot.remaining_quantity == 0:
                removed_lot = lots.pop(0)
                logger.debug(f"Removed fully settled lot {removed_lot.lot_id} for {symbol} from ledger")

        if qty_to_sell > 0:
            error_msg = f"Oversold {symbol}! Remaining {qty_to_sell} units had no matching buy lots."
            logger.warning(error_msg)
            raise ValueError(error_msg)

        return gains_for_this_trade

    def get_open_lot_count(self) -> Dict[str, int]:
        """Get the number of open lots per symbol for monitoring purposes."""
        return {symbol: len(lots) for symbol, lots in self.open_lots.items()}

    def get_total_open_lot_count(self) -> int:
        """Get the total number of open lots across all symbols for monitoring purposes."""
        return sum(len(lots) for lots in self.open_lots.values())
"""
automated-tax-lot-reporting-pipeline:
Engine for matching buy/sell trades to specific tax lots to calculate
realized capital gains, supporting FIFO and HIFO accounting methods.
"""
import dataclasses
import logging
from enum import Enum
from typing import List, Dict, Tuple
import math

logger = logging.getLogger(__name__)

# Quantity tolerance for float dust when depleting lots. Binary floats cannot
# represent most decimal share fractions exactly, so consuming a lot in pieces
# leaves a residue: buying 0.3 and selling 0.1 three times leaves 2.78e-17 on the
# lot. Testing exhaustion with ``== 0`` strands that residue, the lot is never
# removed, and a fully-owned position becomes unsellable. 1e-9 is far below the
# smallest fraction of a unit any broker or exchange supports, so no real holding
# is discarded by treating a sub-epsilon remainder as closed.
_QUANTITY_EPSILON = 1e-9


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
    # Form 8949 requires column (b) "Date acquired" and column (c) "Date sold or
    # disposed of" per transaction, and splits Part I (short-term) from Part II
    # (long-term) on the holding period. Both timestamps are carried here so the
    # holding period is derivable from the record alone. The short/long-term
    # determination itself is left to the consumer: the count runs in whole days
    # from the day *after* acquisition through the disposal date, which requires
    # a calendar timezone this engine deliberately does not assume.
    acquired_timestamp_ms: int
    disposed_timestamp_ms: int


def _validate_trade_record(trade: TradeRecord) -> None:
    """Validate TradeRecord parameters for correctness."""
    if not isinstance(trade.trade_id, str) or not trade.trade_id:
        raise ValueError("trade_id must be a non-empty string")

    if not isinstance(trade.symbol, str) or not trade.symbol:
        raise ValueError("symbol must be a non-empty string")

    if not isinstance(trade.action, TradeAction):
        raise ValueError("action must be a valid TradeAction")

    if (not isinstance(trade.quantity, (int, float))
            or isinstance(trade.quantity, bool)
            or not math.isfinite(trade.quantity)):
        raise ValueError("quantity must be a valid number (finite and not NaN)")

    if trade.quantity <= 0:
        raise ValueError("quantity must be positive")

    if (not isinstance(trade.price, (int, float))
            or isinstance(trade.price, bool)
            or not math.isfinite(trade.price)):
        raise ValueError("price must be a valid number (finite and not NaN)")

    if trade.price < 0:
        raise ValueError("price must be non-negative")

    if (not isinstance(trade.timestamp_ms, int)
            or isinstance(trade.timestamp_ms, bool)
            or trade.timestamp_ms < 0):
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
    if not (math.isfinite(sell_price) and math.isfinite(cost_basis_price)
            and math.isfinite(quantity)):
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
        """Match a SELL against open lots, atomically.

        The match is planned in full before any lot is mutated. A sell that
        cannot be covered raises without consuming a single lot, so a caught
        exception always leaves the ledger exactly as it was. An earlier version
        consumed lots as it went and only raised at the end, which committed a
        partial disposal plus its RealizedGainRecords before reporting failure --
        the ledger and the resulting Form 8949 then disagreed with the trade
        history in a way no later trade could repair.
        """
        qty_to_sell = trade.quantity
        symbol = trade.symbol

        lots = self.open_lots.get(symbol)
        if not lots:
            error_msg = f"Cannot sell {qty_to_sell} of {symbol}. No open lots found (Naked Shorting not supported in this ledger)."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Sort the open lots based on the selected strategy
        sorted_lots = _sort_lots_for_strategy(lots, self.strategy)
        # Update the original list to match the sorted order
        self.open_lots[symbol][:] = sorted_lots

        # Phase 1: plan the match without mutating anything.
        plan: List[Tuple[TaxLot, float]] = []
        remaining = qty_to_sell
        for lot in lots:
            if remaining <= _QUANTITY_EPSILON:
                break
            qty_consumed = min(remaining, lot.remaining_quantity)
            if qty_consumed <= _QUANTITY_EPSILON:
                # Float dust left on an already-depleted lot; nothing to realize.
                continue
            plan.append((lot, qty_consumed))
            remaining -= qty_consumed

        if remaining > _QUANTITY_EPSILON:
            error_msg = (
                f"Oversold {symbol}! Remaining {remaining} units had no matching buy lots. "
                "No lots were consumed and no gains were recorded. A shortfall means the "
                "trade history is incomplete (a missing buy, a lot booked under the wrong "
                "symbol or account, or a duplicated sell) -- it must not be resolved by "
                "inventing cost basis."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Phase 2: commit. Nothing below can fail on a validated, planned match.
        gains_for_this_trade = []
        for lot, qty_consumed in plan:
            realized_pnl = _calculate_pnl(trade.price, lot.cost_basis_price, qty_consumed)

            record = RealizedGainRecord(
                sell_trade_id=trade.trade_id,
                symbol=symbol,
                quantity_sold=qty_consumed,
                sell_price=trade.price,
                buy_lot_id=lot.lot_id,
                cost_basis_price=lot.cost_basis_price,
                realized_pnl=realized_pnl,
                acquired_timestamp_ms=lot.timestamp_ms,
                disposed_timestamp_ms=trade.timestamp_ms
            )
            gains_for_this_trade.append(record)
            self.realized_gains.append(record)

            logger.info(f"Matched SELL {trade.trade_id} against LOT {lot.lot_id}. Qty: {qty_consumed}, PnL: {realized_pnl:.2f}")

            lot.remaining_quantity -= qty_consumed

        # Drop settled lots to bound ledger growth in long-running processes. The
        # test is epsilon-based, not ``== 0``: a lot depleted in fractional pieces
        # can be left holding float dust that would otherwise pin it open forever.
        settled = [lot for lot in lots if lot.remaining_quantity <= _QUANTITY_EPSILON]
        if settled:
            self.open_lots[symbol][:] = [
                lot for lot in lots if lot.remaining_quantity > _QUANTITY_EPSILON
            ]
            for lot in settled:
                logger.debug(f"Removed fully settled lot {lot.lot_id} for {symbol} from ledger")

        return gains_for_this_trade

    def get_open_lot_count(self) -> Dict[str, int]:
        """Get the number of open lots per symbol for monitoring purposes."""
        return {symbol: len(lots) for symbol, lots in self.open_lots.items()}

    def get_total_open_lot_count(self) -> int:
        """Get the total number of open lots across all symbols for monitoring purposes."""
        return sum(len(lots) for lots in self.open_lots.values())
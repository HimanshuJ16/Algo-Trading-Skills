"""
automated-tax-lot-reporting-pipeline:
Engine for matching buy/sell trades to specific tax lots to calculate
realized capital gains, supporting FIFO and HIFO accounting methods.
"""
import dataclasses
import logging
from enum import Enum
from typing import List, Dict

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


class AutomatedTaxLotReportingPipelineEngine:
    def __init__(self, strategy: LotMatchingStrategy = LotMatchingStrategy.FIFO):
        self.strategy = strategy
        # Ledger mapping symbol -> list of open TaxLots
        self.open_lots: Dict[str, List[TaxLot]] = {}
        self.realized_gains: List[RealizedGainRecord] = []

    def process_trade(self, trade: TradeRecord) -> List[RealizedGainRecord]:
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
            logger.error(f"Cannot sell {qty_to_sell} of {symbol}. No open lots found (Naked Shorting not supported in this ledger).")
            return gains_for_this_trade

        # Sort the open lots based on the selected strategy
        lots = self.open_lots[symbol]
        if self.strategy == LotMatchingStrategy.FIFO:
            # Sort by age (oldest timestamp first)
            lots.sort(key=lambda x: x.timestamp_ms)
        elif self.strategy == LotMatchingStrategy.HIFO:
            # Sort by highest cost basis first, then by age
            lots.sort(key=lambda x: (-x.cost_basis_price, x.timestamp_ms))

        # Consume lots until the sell order is fulfilled
        while qty_to_sell > 0 and len(lots) > 0:
            current_lot = lots[0]

            # Determine how much we can consume from this lot
            qty_consumed = min(qty_to_sell, current_lot.remaining_quantity)
            
            # Calculate PnL
            realized_pnl = (trade.price - current_lot.cost_basis_price) * qty_consumed
            
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

            # If the lot is completely consumed, remove it from the ledger
            if current_lot.remaining_quantity == 0:
                lots.pop(0)

        if qty_to_sell > 0:
            logger.warning(f"Oversold {symbol}! Remaining {qty_to_sell} units had no matching buy lots.")

        return gains_for_this_trade

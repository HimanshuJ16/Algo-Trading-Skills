import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class WashSaleError(Exception):
    """Base exception for US Wash Sale Tracking Engine errors."""
    pass


@dataclass
class TradeExecution:
    trade_id: str
    symbol: str
    trade_date: datetime.date
    side: TradeSide
    price: float
    quantity: float


@dataclass
class WashSaleMatch:
    loss_trade_id: str
    replacement_trade_id: str
    symbol: str
    loss_date: datetime.date
    replacement_date: datetime.date
    matched_quantity: float
    disallowed_loss_usd: float
    adjusted_replacement_basis_per_share: float


@dataclass
class WashSaleSummary:
    symbol: str
    total_realized_gross_pnl_usd: float
    total_disallowed_wash_loss_usd: float
    net_allowed_taxable_pnl_usd: float
    wash_matches: List[WashSaleMatch] = field(default_factory=list)


class USWashSaleTrackingEngine:
    """
    Institutional US IRS Wash Sale Rule Tracking Engine (26 U.S. Code § 1091).
    
    Identifies capital loss realizations, scans the 61-day window (30 days prior, trade date, 30 days post),
    matches replacement purchase tax lots, disallows losses, adjusts replacement share cost basis,
    and formats Form 1099-B Box 1g wash sale disclosures.
    """

    def __init__(self, window_days: int = 30):
        """
        :param window_days: IRS § 1091 window (30 days before and 30 days after loss sale).
        """
        self.window_days = window_days
        self.trades: List[TradeExecution] = []
        logger.info(f"Initialized US Wash Sale Engine (IRC § 1091 Window = ±{window_days} days)")

    def add_trade(self, trade: TradeExecution) -> None:
        """Adds a trade execution to the tax tracking ledger."""
        if trade.price <= 0 or trade.quantity <= 0:
            raise WashSaleError("Trade price and quantity must be positive.")

        self.trades.append(trade)
        # Keep trades chronologically sorted
        self.trades.sort(key=lambda t: t.trade_date)
        logger.debug(f"Added trade [{trade.trade_id}] {trade.side.value} {trade.quantity} {trade.symbol} @ ${trade.price:.2f}")

    def evaluate_wash_sales_for_symbol(self, symbol: str) -> WashSaleSummary:
        """
        Evaluates wash sale loss disallowances and cost basis adjustments for a specific symbol.
        """
        sym_trades = [t for t in self.trades if t.symbol == symbol]
        if not sym_trades:
            return WashSaleSummary(symbol=symbol, total_realized_gross_pnl_usd=0.0, total_disallowed_wash_loss_usd=0.0, net_allowed_taxable_pnl_usd=0.0)

        # Track replacement capacity for each buy trade
        buy_avail_qty = {t.trade_id: t.quantity for t in sym_trades if t.side == TradeSide.BUY}

        # FIFO Tax Lot Matching Engine
        open_lots: List[Dict] = []  # [{trade_id, date, remaining_qty, cost_basis}]
        realized_losses: List[Dict] = []  # [{buy_trade_id, sell_trade_id, date, qty, loss_per_share, total_loss}]
        gross_pnl = 0.0

        for tr in sym_trades:
            if tr.side == TradeSide.BUY:
                open_lots.append({
                    "trade_id": tr.trade_id,
                    "date": tr.trade_date,
                    "remaining_qty": tr.quantity,
                    "cost_basis": tr.price,
                })
            elif tr.side == TradeSide.SELL:
                sell_qty_left = tr.quantity
                while sell_qty_left > 0 and open_lots:
                    lot = open_lots[0]
                    matched_qty = min(sell_qty_left, lot["remaining_qty"])
                    pnl = (tr.price - lot["cost_basis"]) * matched_qty
                    gross_pnl += pnl

                    if pnl < 0:
                        realized_losses.append({
                            "buy_trade_id": lot["trade_id"],
                            "sell_trade_id": tr.trade_id,
                            "date": tr.trade_date,
                            "qty": matched_qty,
                            "loss_per_share": abs(tr.price - lot["cost_basis"]),
                            "total_loss": abs(pnl),
                            "unmatched_loss_qty": matched_qty,
                        })

                    lot["remaining_qty"] -= matched_qty
                    sell_qty_left -= matched_qty

                    if lot["remaining_qty"] <= 0:
                        open_lots.pop(0)

        # Scans 61-day window (30 days before, loss date, 30 days after) for replacement buys
        wash_matches: List[WashSaleMatch] = []
        total_disallowed = 0.0

        for loss in realized_losses:
            loss_date = loss["date"]
            loss_qty_needed = loss["unmatched_loss_qty"]
            orig_buy_id = loss["buy_trade_id"]

            # Find replacement buys within 61-day window [loss_date - 30, loss_date + 30]
            # Excludes the original buy lot itself
            replacement_candidates = [
                t for t in sym_trades
                if t.side == TradeSide.BUY
                and t.trade_id != orig_buy_id
                and abs((t.trade_date - loss_date).days) <= self.window_days
                and buy_avail_qty.get(t.trade_id, 0.0) > 0.0
            ]

            for rep in replacement_candidates:
                if loss_qty_needed <= 0:
                    break

                avail = buy_avail_qty[rep.trade_id]
                matched_qty = min(loss_qty_needed, avail)
                disallowed_amt = matched_qty * loss["loss_per_share"]
                adjusted_basis = rep.price + (disallowed_amt / matched_qty)

                wash_matches.append(
                    WashSaleMatch(
                        loss_trade_id=loss["sell_trade_id"],
                        replacement_trade_id=rep.trade_id,
                        symbol=symbol,
                        loss_date=loss_date,
                        replacement_date=rep.trade_date,
                        matched_quantity=matched_qty,
                        disallowed_loss_usd=round(disallowed_amt, 2),
                        adjusted_replacement_basis_per_share=round(adjusted_basis, 4),
                    )
                )

                total_disallowed += disallowed_amt
                loss_qty_needed -= matched_qty
                buy_avail_qty[rep.trade_id] -= matched_qty

        net_allowed = gross_pnl + total_disallowed

        logger.info(
            f"Wash Sale Evaluation [{symbol}]: GrossPnL=${gross_pnl:,.2f}, DisallowedLoss=${total_disallowed:,.2f}, "
            f"NetAllowedPnL=${net_allowed:,.2f}, WashMatches={len(wash_matches)}"
        )

        return WashSaleSummary(
            symbol=symbol,
            total_realized_gross_pnl_usd=round(gross_pnl, 2),
            total_disallowed_wash_loss_usd=round(total_disallowed, 2),
            net_allowed_taxable_pnl_usd=round(net_allowed, 2),
            wash_matches=wash_matches,
        )


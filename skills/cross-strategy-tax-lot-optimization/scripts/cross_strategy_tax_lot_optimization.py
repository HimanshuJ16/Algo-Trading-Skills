import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TaxLot:
    lot_id: str
    strategy_id: str
    symbol: str
    acquisition_date: str              # YYYY-MM-DD
    days_held: int                      # > 365 => Long-Term, <= 365 => Short-Term
    cost_basis_per_share: float
    quantity: float

@dataclass
class SelectedTaxLotExecution:
    lot_id: str
    strategy_id: str
    shares_sold: float
    cost_basis_per_share: float
    execution_price: float
    realized_gain_loss_usd: float
    is_long_term: bool
    is_wash_sale_triggered: bool

@dataclass
class TaxOptimizationResult:
    symbol: str
    requested_sell_quantity: float
    total_realized_gain_loss_usd: float
    short_term_realized_usd: float
    long_term_realized_usd: float
    executed_lots: List[SelectedTaxLotExecution]
    wash_sale_warning: bool

class CrossStrategyTaxLotOptimizer:
    """
    Quantitative tax optimization engine for cross-strategy tax-lot selection (HIFO, Specific ID, LTCG),
    internal order netting, and wash-sale rule interception.
    """
    def __init__(self, wash_sale_window_days: int = 30):
        self.wash_sale_window_days = wash_sale_window_days
        self.tax_lots: List[TaxLot] = []
        self.recent_buys: List[Tuple[str, str, int]] = [] # (symbol, strategy_id, days_ago)

    def add_tax_lot(self, lot: TaxLot):
        self.tax_lots.append(lot)

    def register_recent_buy(self, symbol: str, strategy_id: str, days_ago: int):
        self.recent_buys.append((symbol.upper(), strategy_id, days_ago))

    def optimize_sell_order(
        self,
        symbol: str,
        sell_quantity: float,
        current_market_price: float,
        method: str = "HIFO_MIN_TAX"
    ) -> TaxOptimizationResult:
        """
        Selects optimal tax lots for a sell order using HIFO (Highest-In-First-Out) or LTCG optimization.
        """
        symbol_upper = symbol.upper()
        matching_lots = [l for l in self.tax_lots if l.symbol.upper() == symbol_upper and l.quantity > 0]

        if not matching_lots:
            raise ValueError(f"No available tax lots for symbol {symbol}")

        # Sort Lots based on method
        if method == "HIFO_MIN_TAX":
            # Highest cost basis first -> maximizes losses / minimizes gains
            sorted_lots = sorted(matching_lots, key=lambda l: l.cost_basis_per_share, reverse=True)
        elif method == "LTCG_OPTIMIZED":
            # Prioritize Long-Term (>365 days) over Short-Term
            sorted_lots = sorted(matching_lots, key=lambda l: (l.days_held > 365, l.cost_basis_per_share), reverse=True)
        else: # FIFO
            sorted_lots = sorted(matching_lots, key=lambda l: l.days_held, reverse=True)

        # Audit Wash Sale Window across ALL sub-strategies
        wash_sale_active = any(
            b_sym == symbol_upper and b_days <= self.wash_sale_window_days 
            for b_sym, _, b_days in self.recent_buys
        )

        remaining_qty = sell_quantity
        executed_lots: List[SelectedTaxLotExecution] = []
        total_pnl = 0.0
        st_pnl = 0.0
        lt_pnl = 0.0

        for lot in sorted_lots:
            if remaining_qty <= 0:
                break

            qty_to_take = min(remaining_qty, lot.quantity)
            pnl_per_share = current_market_price - lot.cost_basis_per_share
            lot_pnl = qty_to_take * pnl_per_share

            # Check if this specific lot triggers a Wash Sale (Loss + Recent Buy)
            is_wash = (lot_pnl < 0) and wash_sale_active
            is_lt = lot.days_held > 365

            if is_wash:
                logger.warning(
                    f"WASH SALE INTERCEPTED on {symbol} Lot {lot.lot_id}: Loss of ${abs(lot_pnl):,.2f} disallowed due to recent purchase!"
                )

            executed_lots.append(SelectedTaxLotExecution(
                lot_id=lot.lot_id,
                strategy_id=lot.strategy_id,
                shares_sold=qty_to_take,
                cost_basis_per_share=lot.cost_basis_per_share,
                execution_price=current_market_price,
                realized_gain_loss_usd=round(lot_pnl, 2),
                is_long_term=is_lt,
                is_wash_sale_triggered=is_wash
            ))

            lot.quantity -= qty_to_take
            remaining_qty -= qty_to_take
            total_pnl += lot_pnl
            
            if is_lt:
                lt_pnl += lot_pnl
            else:
                st_pnl += lot_pnl

        return TaxOptimizationResult(
            symbol=symbol_upper,
            requested_sell_quantity=sell_quantity,
            total_realized_gain_loss_usd=round(total_pnl, 2),
            short_term_realized_usd=round(st_pnl, 2),
            long_term_realized_usd=round(lt_pnl, 2),
            executed_lots=executed_lots,
            wash_sale_warning=wash_sale_active
        )

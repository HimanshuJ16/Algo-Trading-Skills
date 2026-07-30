import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class OpenTaxLot:
    lot_id: str
    symbol: str
    acquisition_date_iso: str
    quantity: int
    cost_basis_per_share: float
    holding_period_days: int

@dataclass
class RealizedLotMatch:
    lot_id: str
    symbol: str
    shares_matched: int
    cost_basis_per_share: float
    sale_price_per_share: float
    realized_gain_loss_usd: float
    capital_gain_type: str              # 'STCG' (<=365 days) or 'LTCG' (>365 days)

@dataclass
class TaxLotAccountingReport:
    symbol: str
    matching_strategy_used: str         # 'FIFO', 'LIFO', 'HIFO', 'SPECIFIC_LOT'
    total_shares_sold: int
    total_sale_proceeds_usd: float
    total_cost_basis_usd: float
    total_realized_gain_loss_usd: float
    total_stcg_gain_loss_usd: float
    total_ltcg_gain_loss_usd: float
    matched_lots: List[RealizedLotMatch]
    remaining_open_lots: List[OpenTaxLot]
    audit_notes: str

class TaxLotAccountingEngine:
    """
    Quantitative tax accounting engine for matching open tax lots across FIFO, LIFO, HIFO, and Specific Identification methods,
    calculating realized short-term vs long-term capital gains, and optimizing tax liabilities.
    """
    def __init__(self):
        pass

    def process_sell_order(
        self,
        open_lots: List[OpenTaxLot],
        sale_qty: int,
        sale_price: float,
        strategy: str = "FIFO",
        target_lot_ids: Optional[List[str]] = None
    ) -> TaxLotAccountingReport:
        """
        Matches sell order against open tax lots using specified accounting strategy.
        """
        if not open_lots or sale_qty <= 0 or sale_price <= 0:
            raise ValueError("Open lots list cannot be empty and sale qty / price must be > 0.")

        total_avail = sum(l.quantity for l in open_lots)
        if sale_qty > total_avail:
            raise ValueError(f"Insufficient open tax lot quantity ({total_avail}) for sell order ({sale_qty}).")

        strat_clean = strategy.upper()

        # Sort lots based on strategy
        available_lots = [OpenTaxLot(**vars(l)) for l in open_lots] # Deep copy

        if strat_clean == "FIFO":
            available_lots.sort(key=lambda l: l.acquisition_date_iso)
        elif strat_clean == "LIFO":
            available_lots.sort(key=lambda l: l.acquisition_date_iso, reverse=True)
        elif strat_clean == "HIFO":
            available_lots.sort(key=lambda l: l.cost_basis_per_share, reverse=True)
        elif strat_clean == "SPECIFIC_LOT":
            if not target_lot_ids:
                raise ValueError("target_lot_ids must be provided for SPECIFIC_LOT matching.")
            target_set = set(target_lot_ids)
            available_lots.sort(key=lambda l: 0 if l.lot_id in target_set else 1)
        else:
            raise ValueError(f"Unsupported matching strategy: '{strategy}'. Use FIFO, LIFO, HIFO, or SPECIFIC_LOT.")

        remaining_to_sell = sale_qty
        matched_records: List[RealizedLotMatch] = []
        remaining_open: List[OpenTaxLot] = []

        for lot in available_lots:
            if remaining_to_sell <= 0:
                remaining_open.append(lot)
                continue

            take_qty = min(lot.quantity, remaining_to_sell)
            remaining_to_sell -= take_qty

            pnl_per_share = sale_price - lot.cost_basis_per_share
            total_lot_pnl = round(pnl_per_share * take_qty, 2)

            gain_type = "LTCG" if lot.holding_period_days > 365 else "STCG"

            matched_records.append(RealizedLotMatch(
                lot_id=lot.lot_id,
                symbol=lot.symbol,
                shares_matched=take_qty,
                cost_basis_per_share=lot.cost_basis_per_share,
                sale_price_per_share=sale_price,
                realized_gain_loss_usd=total_lot_pnl,
                capital_gain_type=gain_type
            ))

            lot.quantity -= take_qty
            if lot.quantity > 0:
                remaining_open.append(lot)

        total_proceeds = round(sale_qty * sale_price, 2)
        total_cost = round(sum(m.cost_basis_per_share * m.shares_matched for m in matched_records), 2)
        total_pnl = round(sum(m.realized_gain_loss_usd for m in matched_records), 2)
        total_stcg = round(sum(m.realized_gain_loss_usd for m in matched_records if m.capital_gain_type == "STCG"), 2)
        total_ltcg = round(sum(m.realized_gain_loss_usd for m in matched_records if m.capital_gain_type == "LTCG"), 2)

        notes = (
            f"TAX LOT ACCOUNTING MATCH COMPLETE [{strat_clean}]: Sold {sale_qty} sh @ ${sale_price:.2f}. "
            f"Total Realized PnL = ${total_pnl:,.2f} (STCG = ${total_stcg:,.2f}, LTCG = ${total_ltcg:,.2f}). "
            f"Matched across {len(matched_records)} tax lots."
        )
        logger.info(notes)

        symbol = open_lots[0].symbol
        return TaxLotAccountingReport(
            symbol=symbol,
            matching_strategy_used=strat_clean,
            total_shares_sold=sale_qty,
            total_sale_proceeds_usd=total_proceeds,
            total_cost_basis_usd=total_cost,
            total_realized_gain_loss_usd=total_pnl,
            total_stcg_gain_loss_usd=total_stcg,
            total_ltcg_gain_loss_usd=total_ltcg,
            matched_lots=matched_records,
            remaining_open_lots=remaining_open,
            audit_notes=notes
        )

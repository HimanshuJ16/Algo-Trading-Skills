import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class InputData:
    value: float

@dataclass
class ContractSettlementSpec:
    symbol: str
    underlying_asset: str
    settlement_type: str                 # 'CASH', 'PHYSICAL'
    multiplier: float = 100.0
    days_to_notice_date: int = 30

@dataclass
class PositionState:
    position_qty: float                  # + for Long, - for Short
    entry_price: float
    account_cash_balance: float
    has_delivery_facility: bool = False

@dataclass
class SettlementReport:
    symbol: str
    settlement_type: str
    position_qty: float
    notional_delivery_value: float
    cash_settlement_pnl: float
    is_fnd_risk_active: bool
    status: str                          # 'CASH_SETTLED_OK', 'PHYSICAL_DELIVERY_OK', 'PHYSICAL_DELIVERY_RISK_BREACH'
    audit_notes: str

class PhysicalVsCashSettlementHandlingEngine:
    """
    Derivatives settlement handling engine distinguishing cash-settled contracts from physical delivery obligations,
    calculating expiration cashflows, and enforcing First Notice Date (FND) liquidations.
    """
    def process(self, data: InputData) -> float:
        """
        Legacy processing method for backward compatibility.
        """
        if data is None:
            return 0.0
        return data.value * 2.0

    def evaluate_settlement_risk(
        self, spec: ContractSettlementSpec, pos: PositionState, settlement_price: float
    ) -> SettlementReport:
        """
        Evaluates settlement risk and calculates cash/physical expiration mechanics.
        """
        stype = spec.settlement_type.upper()
        notional_delivery_val = abs(pos.position_qty) * spec.multiplier * settlement_price

        if stype == "CASH":
            pnl = pos.position_qty * spec.multiplier * (settlement_price - pos.entry_price)
            notes = (
                f"CASH SETTLEMENT COMPLETED [{spec.symbol}]: Qty = {pos.position_qty}, "
                f"Settle Price = ${settlement_price:,.2f}, Net Cash PnL = ${pnl:+,.2f}."
            )
            logger.info(notes)
            return SettlementReport(
                symbol=spec.symbol,
                settlement_type="CASH",
                position_qty=pos.position_qty,
                notional_delivery_value=0.0,
                cash_settlement_pnl=round(pnl, 2),
                is_fnd_risk_active=False,
                status="CASH_SETTLED_OK",
                audit_notes=notes
            )

        # PHYSICAL SETTLEMENT
        pnl = pos.position_qty * spec.multiplier * (settlement_price - pos.entry_price)
        is_fnd_near = spec.days_to_notice_date <= 3
        has_sufficient_cash = pos.account_cash_balance >= notional_delivery_val

        # Delivery breach condition
        is_breach = False
        if is_fnd_near:
            if not pos.has_delivery_facility or not has_sufficient_cash:
                is_breach = True

        status = "PHYSICAL_DELIVERY_RISK_BREACH" if is_breach else "PHYSICAL_DELIVERY_OK"
        notes = (
            f"PHYSICAL SETTLEMENT AUDIT [{spec.symbol} - {status}]: Qty = {pos.position_qty}, "
            f"Delivery Value = ${notional_delivery_val:,.2f}, Days to FND = {spec.days_to_notice_date}, "
            f"Delivery Facility = {pos.has_delivery_facility}, Cash Balance = ${pos.account_cash_balance:,.2f}."
        )

        if is_breach:
            logger.error(f"DELIVERY RISK ALERT: {notes}")
        else:
            logger.info(notes)

        return SettlementReport(
            symbol=spec.symbol,
            settlement_type="PHYSICAL",
            position_qty=pos.position_qty,
            notional_delivery_value=round(notional_delivery_val, 2),
            cash_settlement_pnl=round(pnl, 2),
            is_fnd_risk_active=is_fnd_near,
            status=status,
            audit_notes=notes
        )

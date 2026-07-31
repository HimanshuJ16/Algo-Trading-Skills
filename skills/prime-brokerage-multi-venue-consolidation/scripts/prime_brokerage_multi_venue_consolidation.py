import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    api_key: str = "default_key"

@dataclass
class VenueExecution:
    execution_id: str
    executing_broker: str
    venue_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL'
    quantity: float
    price: float
    trade_date: str                      # e.g. '2026-07-31'

@dataclass
class PrimeBrokerSpec:
    prime_broker_name: str               # e.g. 'GOLDMAN_SACHS', 'HIDDEN_ROAD'
    pb_account_id: str
    clearing_fee_per_share: float = 0.0005 # $0.0005 per share clearing fee

@dataclass
class PBConsolidationReport:
    prime_broker_name: str
    pb_account_id: str
    total_executions_consolidated: int
    total_gross_notional_usd: float
    net_positions_by_symbol: Dict[str, float]
    total_clearing_fees_usd: float
    consolidated_margin_savings_pct: float
    giveup_payload: List[Dict[str, Any]]
    status: str                          # 'CONSOLIDATION_SUCCESSFUL', 'NO_EXECUTIONS_PROVIDED'
    audit_notes: str

class IntegrationEngine:
    """
    Legacy IntegrationEngine retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def connect(self) -> bool:
        return True

    def process(self) -> str:
        return "success"

class PrimeBrokerageMultiVenueConsolidationEngine:
    """
    Prime brokerage multi-venue trade consolidation engine generating central give-up clearing payloads,
    netting position exposures, and optimizing cross-margining across venues.
    """
    def __init__(
        self,
        config: Optional[Config] = None,
        pb_spec: Optional[PrimeBrokerSpec] = None
    ):
        self.config = config or Config()
        self.pb_spec = pb_spec or PrimeBrokerSpec("GOLDMAN_SACHS", "PB_ACCT_99182")

    def consolidate_venue_executions(
        self, executions: List[VenueExecution]
    ) -> PBConsolidationReport:
        """
        Aggregates multi-venue trade fills, nets positions by symbol, and formats Prime Broker give-up payloads.
        """
        if not executions:
            return PBConsolidationReport(
                prime_broker_name=self.pb_spec.prime_broker_name,
                pb_account_id=self.pb_spec.pb_account_id,
                total_executions_consolidated=0,
                total_gross_notional_usd=0.0,
                net_positions_by_symbol={},
                total_clearing_fees_usd=0.0,
                consolidated_margin_savings_pct=0.0,
                giveup_payload=[],
                status="NO_EXECUTIONS_PROVIDED",
                audit_notes="No venue executions submitted for PB consolidation."
            )

        net_positions: Dict[str, float] = {}
        total_gross_notional = 0.0
        total_clearing_fees = 0.0
        giveup_payload: List[Dict[str, Any]] = []

        for ex in executions:
            side_upper = ex.side.upper()
            sym_upper = ex.symbol.upper()

            notional = ex.quantity * ex.price
            total_gross_notional += notional
            total_clearing_fees += ex.quantity * self.pb_spec.clearing_fee_per_share

            # Netting logic
            signed_qty = ex.quantity if side_upper == "BUY" else -ex.quantity
            net_positions[sym_upper] = net_positions.get(sym_upper, 0.0) + signed_qty

            # Give-Up Clearing Payload Entry
            giveup_payload.append({
                "give_up_id": f"GU_{ex.execution_id}",
                "pb_account": self.pb_spec.pb_account_id,
                "executing_broker": ex.executing_broker,
                "venue": ex.venue_id,
                "symbol": sym_upper,
                "side": side_upper,
                "quantity": ex.quantity,
                "price": ex.price,
                "notional_usd": round(notional, 2),
                "trade_date": ex.trade_date
            })

        # Calculate Margin Savings % (Netting longs & shorts reduces gross margin requirement)
        gross_abs_qty = sum(abs(ex.quantity) for ex in executions)
        net_abs_qty = sum(abs(qty) for qty in net_positions.values())
        margin_savings_pct = (1.0 - (net_abs_qty / max(1.0, gross_abs_qty))) * 100.0

        notes = (
            f"PB CONSOLIDATION SUCCESSFUL [{self.pb_spec.prime_broker_name} ({self.pb_spec.pb_account_id})]: "
            f"Executions = {len(executions)}, Gross Notional = ${total_gross_notional:,.2f}, "
            f"Net Symbols = {len(net_positions)}, Margin Savings = {margin_savings_pct:.1f}%, "
            f"Total PB Clearing Fees = ${total_clearing_fees:,.2f}."
        )

        logger.info(notes)

        return PBConsolidationReport(
            prime_broker_name=self.pb_spec.prime_broker_name,
            pb_account_id=self.pb_spec.pb_account_id,
            total_executions_consolidated=len(executions),
            total_gross_notional_usd=round(total_gross_notional, 2),
            net_positions_by_symbol=net_positions,
            total_clearing_fees_usd=round(total_clearing_fees, 2),
            consolidated_margin_savings_pct=round(margin_savings_pct, 2),
            giveup_payload=giveup_payload,
            status="CONSOLIDATION_SUCCESSFUL",
            audit_notes=notes
        )

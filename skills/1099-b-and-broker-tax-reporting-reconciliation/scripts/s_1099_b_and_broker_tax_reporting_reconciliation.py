import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import date

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TaxReconciliation")

@dataclass
class TaxLot:
    lot_id: str
    symbol: str
    quantity: float
    acquired_date: date
    sold_date: date
    proceeds: float
    cost_basis: float
    is_wash_sale: bool = False

@dataclass
class Discrepancy:
    internal_lot_id: Optional[str]
    broker_lot_id: Optional[str]
    reason: str
    details: str

class S1099BAndBrokerTaxReportingReconciliationEngine:
    """
    Institutional engine for reconciling internal tax lots against broker 1099-B reports.
    """
    def __init__(self, tolerance_usd: float = 0.05):
        self.internal_lots: Dict[str, TaxLot] = {}
        self.broker_lots: Dict[str, TaxLot] = {}
        self.tolerance_usd = tolerance_usd
        self.matched_pairs: List[tuple] = []
        self.discrepancies: List[Discrepancy] = []

    def load_internal_lot(self, lot: TaxLot):
        self.internal_lots[lot.lot_id] = lot

    def load_broker_lot(self, lot: TaxLot):
        self.broker_lots[lot.lot_id] = lot

    def _is_match(self, internal: TaxLot, broker: TaxLot) -> bool:
        if internal.symbol != broker.symbol or internal.quantity != broker.quantity:
            return False
        if internal.acquired_date != broker.acquired_date or internal.sold_date != broker.sold_date:
            return False
        
        proceeds_diff = abs(internal.proceeds - broker.proceeds)
        cost_diff = abs(internal.cost_basis - broker.cost_basis)
        
        if proceeds_diff > self.tolerance_usd or cost_diff > self.tolerance_usd:
            return False
            
        return True

    def process_reconciliation(self):
        """Runs the reconciliation algorithm and categorizes results."""
        logger.info(f"Starting reconciliation. Internal Lots: {len(self.internal_lots)}, Broker Lots: {len(self.broker_lots)}")
        
        unmatched_internal = set(self.internal_lots.keys())
        unmatched_broker = set(self.broker_lots.keys())

        # O(N^2) naive matching for demonstration; in production, use a hash map grouped by symbol/date.
        for int_id, int_lot in self.internal_lots.items():
            for brk_id, brk_lot in self.broker_lots.items():
                if brk_id in unmatched_broker and self._is_match(int_lot, brk_lot):
                    # Check Wash Sale Flag
                    if int_lot.is_wash_sale != brk_lot.is_wash_sale:
                        self.discrepancies.append(
                            Discrepancy(int_id, brk_id, "Wash Sale Mismatch", "Flags do not align.")
                        )
                    else:
                        self.matched_pairs.append((int_id, brk_id))
                    
                    unmatched_internal.remove(int_id)
                    unmatched_broker.remove(brk_id)
                    break # Move to next internal lot

        # Log missing records
        for int_id in unmatched_internal:
            self.discrepancies.append(Discrepancy(int_id, None, "Missing in 1099-B", "Internal lot not found in broker report."))
            
        for brk_id in unmatched_broker:
            self.discrepancies.append(Discrepancy(None, brk_id, "Missing Internally", "Broker lot not found in internal ledger."))

        logger.info(f"Reconciliation complete. Matched: {len(self.matched_pairs)}, Discrepancies: {len(self.discrepancies)}")
        return {
            "matched": len(self.matched_pairs),
            "discrepancies": self.discrepancies
        }

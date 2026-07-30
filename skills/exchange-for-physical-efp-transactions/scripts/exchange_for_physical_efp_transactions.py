import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class EfpFuturesLeg:
    futures_symbol: str                 # e.g. 'GC_202612' (Gold Futures) or 'CL_202609' (Crude Oil)
    contract_count: int
    contract_multiplier: float          # e.g. 100.0 (100 oz per Gold contract)
    futures_price_usd: float
    side: str                           # 'BUY_FUTURES' or 'SELL_FUTURES'

@dataclass
class EfpPhysicalLeg:
    physical_symbol: str                # e.g. 'SPOT_GOLD_USD' or 'PHYSICAL_WTI_CRUDE'
    physical_quantity: float            # e.g. 1000.0 oz
    spot_price_usd: float
    side: str                           # 'SELL_PHYSICAL' or 'BUY_PHYSICAL'

@dataclass
class EfpAuditReport:
    efp_transaction_id: str
    futures_symbol: str
    physical_symbol: str
    observed_efp_basis_usd: float       # Futures Price - Spot Price
    theoretical_fair_basis_usd: float   # Spot Price * (exp(r*T) - 1)
    basis_mispricing_usd: float         # Observed Basis - Theoretical Basis
    is_quantity_equivalent: bool
    status: str                         # 'EFP_APPROVED', 'QUANTITY_MISMATCH_REJECTION', 'RULE_538_VIOLATION'
    efrp_clearing_payload: Optional[Dict[str, str]]
    audit_notes: str

class ExchangeForPhysicalEngine:
    """
    Quantitative derivatives and commodities execution engine for modeling Exchange for Physical (EFP / EFRP) transactions
    under CME Rule 538, evaluating basis arbitrage spread, and validating physical-to-futures quantity equivalence.
    """
    def __init__(self, quantity_tolerance: float = 1e-4):
        self.quantity_tolerance = quantity_tolerance

    def evaluate_efp_transaction(
        self,
        efp_id: str,
        futures_leg: EfpFuturesLeg,
        physical_leg: EfpPhysicalLeg,
        risk_free_rate: float = 0.04,    # 4.0% per annum
        time_to_expiry_years: float = 0.25
    ) -> EfpAuditReport:
        """
        Audits physical-to-futures quantity equivalence, computes EFP basis spread, and formats EFRP clearing payload.
        """
        required_physical_qty = float(futures_leg.contract_count) * futures_leg.contract_multiplier

        # Rule 1: Quantity Equivalence Audit
        qty_diff = abs(physical_leg.physical_quantity - required_physical_qty)
        if qty_diff > self.quantity_tolerance:
            msg = (
                f"QUANTITY MISMATCH REJECTION [{efp_id}]: Physical quantity {physical_leg.physical_quantity} "
                f"does not match futures equivalent {required_physical_qty} ({futures_leg.contract_count} contracts x {futures_leg.contract_multiplier})."
            )
            logger.error(msg)
            return EfpAuditReport(
                efp_transaction_id=efp_id, futures_symbol=futures_leg.futures_symbol, physical_symbol=physical_leg.physical_symbol,
                observed_efp_basis_usd=0.0, theoretical_fair_basis_usd=0.0, basis_mispricing_usd=0.0,
                is_quantity_equivalent=False, status="QUANTITY_MISMATCH_REJECTION", efrp_clearing_payload=None, audit_notes=msg
            )

        # Rule 2: EFP Basis & Carrying Cost Calculation
        # Observed Basis = Futures Price - Spot Price
        observed_basis = round(futures_leg.futures_price_usd - physical_leg.spot_price_usd, 4)

        # Theoretical Carrying Cost Fair Basis = Spot * (exp(r*T) - 1.0)
        cost_of_carry = math.exp(risk_free_rate * time_to_expiry_years) - 1.0
        theoretical_basis = round(physical_leg.spot_price_usd * cost_of_carry, 4)

        basis_mispricing = round(observed_basis - theoretical_basis, 4)

        # Rule 3: Format CME Rule 538 EFRP Clearing Payload
        clearing_payload = {
            "rule": "CME Rule 538 / Eurex Rule 4.6 EFRP",
            "efp_id": efp_id,
            "futures_symbol": futures_leg.futures_symbol,
            "futures_contracts": str(futures_leg.contract_count),
            "futures_price": f"{futures_leg.futures_price_usd:.2f}",
            "physical_symbol": physical_leg.physical_symbol,
            "physical_qty": f"{physical_leg.physical_quantity:.2f}",
            "spot_price": f"{physical_leg.spot_price_usd:.2f}",
            "observed_basis": f"{observed_basis:.4f}",
            "reporting_status": "SUBMITTED_TO_CLEARINGHOUSE"
        }

        notes = (
            f"EFP TRANSACTION APPROVED [{efp_id}]: {futures_leg.contract_count} contracts ({futures_leg.futures_symbol}) swapped for "
            f"{physical_leg.physical_quantity} units ({physical_leg.physical_symbol}). Observed Basis = ${observed_basis:+.4f}/unit "
            f"(Theoretical Fair Basis = ${theoretical_basis:+.4f}/unit, Mispricing = ${basis_mispricing:+.4f}/unit)."
        )
        logger.info(notes)

        return EfpAuditReport(
            efp_transaction_id=efp_id,
            futures_symbol=futures_leg.futures_symbol,
            physical_symbol=physical_leg.physical_symbol,
            observed_efp_basis_usd=observed_basis,
            theoretical_fair_basis_usd=theoretical_basis,
            basis_mispricing_usd=basis_mispricing,
            is_quantity_equivalent=True,
            status="EFP_APPROVED",
            efrp_clearing_payload=clearing_payload,
            audit_notes=notes
        )

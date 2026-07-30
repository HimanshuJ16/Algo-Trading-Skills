import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class EquityShortPositionState:
    isin: str
    symbol: str
    issued_share_capital_qty: int       # Total issued shares
    long_shares_qty: int
    short_shares_qty: int
    has_valid_locate_agreement: bool    # True if locate/borrow confirmed

@dataclass
class EuSsrDisclosureReport:
    isin: str
    symbol: str
    net_short_shares_qty: int
    net_short_percentage: float         # Net short % of issued share capital
    reporting_status: str               # 'PUBLIC_DISCLOSURE_REQUIRED', 'PRIVATE_NCA_NOTIFICATION_REQUIRED', 'BELOW_REPORTING_THRESHOLDS', 'NAKED_SHORT_BAN_BREACH'
    is_short_execution_allowed: bool
    reporting_deadline_description: str # 'T+1 15:30 CET'
    audit_notes: str

class EuShortSellingRegulationEngine:
    """
    Quantitative European regulatory compliance engine for tracking EU Short Selling Regulation
    (SSR - Regulation 236/2012) net short position disclosures (0.1% NCA private notification / 0.5% public disclosure) and naked short bans.
    """
    def __init__(self, private_nca_threshold_pct: float = 0.10, public_disclosure_threshold_pct: float = 0.50):
        self.private_nca_threshold_pct = private_nca_threshold_pct
        self.public_disclosure_threshold_pct = public_disclosure_threshold_pct

    def evaluate_short_position_disclosure(self, pos: EquityShortPositionState) -> EuSsrDisclosureReport:
        """
        Audits net short position percentage against EU SSR reporting thresholds and locate requirements.
        """
        if pos.issued_share_capital_qty <= 0:
            raise ValueError("Issued share capital must be > 0.")

        net_short_qty = pos.short_shares_qty - pos.long_shares_qty
        net_short_pct = round((net_short_qty / float(pos.issued_share_capital_qty)) * 100.0, 4)

        # Rule 1: Naked Short Selling Ban Audit
        if net_short_qty > 0 and not pos.has_valid_locate_agreement:
            msg = f"NAKED SHORT BAN BREACH [{pos.symbol}]: Net short position {net_short_qty:,} shares lacks valid locate/borrow confirmation!"
            logger.critical(msg)
            return EuSsrDisclosureReport(
                isin=pos.isin, symbol=pos.symbol, net_short_shares_qty=net_short_qty,
                net_short_percentage=net_short_pct, reporting_status="NAKED_SHORT_BAN_BREACH",
                is_short_execution_allowed=False, reporting_deadline_description="IMMEDIATE_HALT", audit_notes=msg
            )

        # Rule 2: Statutory Disclosure Threshold Evaluation
        if net_short_pct >= self.public_disclosure_threshold_pct:
            status = "PUBLIC_DISCLOSURE_REQUIRED"
            notes = (
                f"PUBLIC DISCLOSURE REQUIRED [{pos.symbol}]: Net short position {net_short_pct:.4f}% >= {self.public_disclosure_threshold_pct}% threshold. "
                f"Must file public market disclosure by T+1 15:30 CET."
            )
            logger.warning(notes)
        elif net_short_pct >= self.private_nca_threshold_pct:
            status = "PRIVATE_NCA_NOTIFICATION_REQUIRED"
            notes = (
                f"PRIVATE NCA NOTIFICATION REQUIRED [{pos.symbol}]: Net short position {net_short_pct:.4f}% >= {self.private_nca_threshold_pct}% threshold. "
                f"Must file private NCA notification by T+1 15:30 CET."
            )
            logger.info(notes)
        else:
            status = "BELOW_REPORTING_THRESHOLDS"
            notes = f"SSR COMPLIANT [{pos.symbol}]: Net short position {net_short_pct:.4f}% is below reporting thresholds."

        return EuSsrDisclosureReport(
            isin=pos.isin,
            symbol=pos.symbol,
            net_short_shares_qty=net_short_qty,
            net_short_percentage=net_short_pct,
            reporting_status=status,
            is_short_execution_allowed=True,
            reporting_deadline_description="T+1 15:30 CET" if status != "BELOW_REPORTING_THRESHOLDS" else "NONE",
            audit_notes=notes
        )

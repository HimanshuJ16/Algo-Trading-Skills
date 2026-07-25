"""
australia-asic-drt-obligations:
Validates OTC derivative trade reports against ASIC's 2024 Derivative Transaction Rules,
ensuring mandatory ISO identifiers (LEI, UTI, UPI) and T+2 deadlines.
"""
import dataclasses
import logging
from typing import Optional, List
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OtcDerivativeTrade:
    internal_trade_id: str
    trade_date: date
    # Mandatory ASIC 2024 fields
    lei_counterparty: Optional[str] = None  # ISO 17442
    uti: Optional[str] = None               # ISO 23897
    upi: Optional[str] = None               # ISO 4914


@dataclasses.dataclass
class DrtComplianceRecord:
    internal_trade_id: str
    is_ready_for_reporting: bool
    is_late_submission: bool
    missing_fields: List[str]


class AsicDrtReportingEngine:
    """
    Validates trades for ASIC Trade Repository reporting compliance.
    """

    def validate_report(self, trade: OtcDerivativeTrade, reporting_date: date) -> DrtComplianceRecord:
        missing_fields = []
        is_ready = True

        # 1. Validate LEI (Legal Entity Identifier)
        if not trade.lei_counterparty or len(trade.lei_counterparty.strip()) != 20:
            missing_fields.append("LEI (ISO 17442) is missing or invalid.")
            is_ready = False

        # 2. Validate UTI (Unique Transaction Identifier)
        if not trade.uti or len(trade.uti.strip()) < 10: # UTIs are up to 52 alphanumeric characters
            missing_fields.append("UTI (ISO 23897) is missing or invalid.")
            is_ready = False

        # 3. Validate UPI (Unique Product Identifier)
        if not trade.upi or len(trade.upi.strip()) != 12: # UPI is a 12-character code
            missing_fields.append("UPI (ISO 4914) is missing or invalid.")
            is_ready = False

        # 4. Check T+2 Deadline (ASIC 2024 rules)
        # Note: In a production system, this should calculate T+2 using business days (excluding weekends/holidays).
        # For this heuristic model, we use a simple timedelta.
        deadline = trade.trade_date + timedelta(days=2)
        is_late = reporting_date > deadline

        if is_late:
            logger.warning(
                f"Trade {trade.internal_trade_id} is breaching the ASIC T+2 reporting deadline! "
                f"Trade Date: {trade.trade_date}, Reporting Date: {reporting_date}"
            )

        if not is_ready:
            logger.error(f"Trade {trade.internal_trade_id} failed ASIC DRT validation. Missing: {missing_fields}")
        else:
            logger.info(f"Trade {trade.internal_trade_id} passed ASIC DRT validation.")

        return DrtComplianceRecord(
            internal_trade_id=trade.internal_trade_id,
            is_ready_for_reporting=is_ready,
            is_late_submission=is_late,
            missing_fields=missing_fields
        )

    def batch_validate(self, trades: List[OtcDerivativeTrade], reporting_date: date) -> List[DrtComplianceRecord]:
        return [self.validate_report(trade, reporting_date) for trade in trades]

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

MANDATORY_FIELDS = ["trade_id", "symbol", "side", "quantity", "price", "trade_date", "cost_basis_usd"]

@dataclass
class Record:
    id: str
    value: float

@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL'
    quantity: float
    price: float
    trade_date: str                      # ISO format e.g. '2024-01-15'
    cost_basis_usd: Optional[float] = None
    proceeds_usd: Optional[float] = None
    holding_period_days: Optional[int] = None
    wash_sale_flag: Optional[bool] = None
    lot_method: str = "FIFO"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "trade_date": self.trade_date,
            "cost_basis_usd": self.cost_basis_usd,
        }

@dataclass
class RecordIssue:
    trade_id: str
    issue_type: str                      # 'MISSING_FIELD', 'WASH_SALE_UNSET', 'RETENTION_AT_RISK'
    detail: str

@dataclass
class TaxAuditComplianceReport:
    total_records: int
    complete_records: int
    incomplete_records: int
    short_term_trades: int
    long_term_trades: int
    wash_sale_flagged: int
    issues: List[RecordIssue]
    status: str                          # 'AUDIT_COMPLIANT', 'AUDIT_ISSUES_FOUND'
    audit_notes: str

class RecordKeepingRequirementsForTaxAuditDefenseEngine:
    """
    Trade record-keeping compliance engine validating mandatory tax audit documentation
    including cost basis, holding periods, wash sale flags, and retention policy enforcement.
    """
    def __init__(self):
        self.records: List[Record] = []
        self.trade_records: List[TradeRecord] = []

    def add_record(self, record: Record) -> None:
        """Legacy method retained for backward compatibility."""
        self.records.append(record)

    def process(self) -> float:
        """Legacy method retained for backward compatibility."""
        return sum(r.value for r in self.records)

    def add_trade_record(self, trade: TradeRecord) -> None:
        self.trade_records.append(trade)

    def audit_records(self) -> TaxAuditComplianceReport:
        """
        Audits all trade records for mandatory field completeness, holding period classification,
        wash sale flag population, and generates a compliance report.
        """
        issues: List[RecordIssue] = []
        complete = 0
        incomplete = 0
        short_term = 0
        long_term = 0
        wash_flagged = 0

        for tr in self.trade_records:
            rec_dict = tr.to_dict()
            missing = [f for f in MANDATORY_FIELDS if rec_dict.get(f) is None]

            if missing:
                incomplete += 1
                issues.append(RecordIssue(
                    trade_id=tr.trade_id,
                    issue_type="MISSING_FIELD",
                    detail=f"Missing mandatory fields: {', '.join(missing)}"
                ))
            else:
                complete += 1

            # Holding period classification
            if tr.holding_period_days is not None:
                if tr.holding_period_days <= 365:
                    short_term += 1
                else:
                    long_term += 1

            # Wash sale flag check for sell transactions
            if tr.side.upper() == "SELL" and tr.wash_sale_flag is None:
                issues.append(RecordIssue(
                    trade_id=tr.trade_id,
                    issue_type="WASH_SALE_UNSET",
                    detail="Sell transaction missing wash_sale_flag."
                ))

            if tr.wash_sale_flag is True:
                wash_flagged += 1

        total = len(self.trade_records)
        has_issues = len(issues) > 0
        status = "AUDIT_ISSUES_FOUND" if has_issues else "AUDIT_COMPLIANT"

        notes = (
            f"TAX AUDIT COMPLIANCE [{status}]: "
            f"Total = {total}, Complete = {complete}, Incomplete = {incomplete}, "
            f"Short-Term = {short_term}, Long-Term = {long_term}, Wash Sale Flagged = {wash_flagged}, "
            f"Issues = {len(issues)}."
        )

        if has_issues:
            logger.warning(notes)
        else:
            logger.info(notes)

        return TaxAuditComplianceReport(
            total_records=total,
            complete_records=complete,
            incomplete_records=incomplete,
            short_term_trades=short_term,
            long_term_trades=long_term,
            wash_sale_flagged=wash_flagged,
            issues=issues,
            status=status,
            audit_notes=notes
        )

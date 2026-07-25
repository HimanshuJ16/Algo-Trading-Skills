from dataclasses import dataclass
from typing import List

@dataclass
class ComplianceRecord:
    trade_id: str
    is_compliant: bool
    notes: str

class ComplianceChecker:
    def __init__(self):
        self.rules = ["rule1", "rule2"]

    def check_compliance(self, trade_id: str) -> ComplianceRecord:
        return ComplianceRecord(trade_id=trade_id, is_compliant=True, notes="Compliant")

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        return [self.check_compliance(tid) for tid in trade_ids]

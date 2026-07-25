from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime

@dataclass
class UniverseSelectionRule:
    rule_name: str
    lookback_days: int
    data_source: str
    requires_point_in_time: bool = True

@dataclass
class AuditResult:
    rule_name: str
    has_lookahead_bias: bool
    description: str

class UniverseLookaheadAuditor:
    def __init__(self, rules: List[UniverseSelectionRule]):
        self.rules = rules

    def audit(self, snapshot_date: datetime, data_available_up_to: datetime) -> List[AuditResult]:
        results = []
        for rule in self.rules:
            # If the data used to select the universe is newer than the snapshot date, it's a lookahead bias
            bias = False
            desc = f"Rule '{rule.rule_name}' is safe."
            if snapshot_date < data_available_up_to and rule.requires_point_in_time:
                bias = True
                desc = f"Lookahead Bias Detected: Data up to {data_available_up_to} used for snapshot at {snapshot_date}."
            
            results.append(AuditResult(rule.rule_name, bias, desc))
        return results

from dataclasses import dataclass
from enum import Enum

class ResponseAction(Enum):
    WARN = "WARN"
    REDUCE = "REDUCE"
    HALT = "HALT"
    FLATTEN = "FLATTEN"
    NONE = "NONE"

@dataclass
class EscalationResult:
    action: ResponseAction
    level: float

class RiskEscalationMatrix:
    def __init__(self, warn_lvl=1.0, reduce_lvl=1.2, halt_lvl=1.5, flatten_lvl=2.0):
        self.levels = {
            flatten_lvl: ResponseAction.FLATTEN,
            halt_lvl: ResponseAction.HALT,
            reduce_lvl: ResponseAction.REDUCE,
            warn_lvl: ResponseAction.WARN
        }

    def evaluate(self, risk_metric: float, limit: float) -> EscalationResult:
        if limit <= 0:
            return EscalationResult(ResponseAction.NONE, 0.0)
        ratio = risk_metric / limit
        
        for lvl in sorted(self.levels.keys(), reverse=True):
            if ratio >= lvl:
                return EscalationResult(self.levels[lvl], lvl)
        return EscalationResult(ResponseAction.NONE, 0.0)

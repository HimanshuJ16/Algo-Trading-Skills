from dataclasses import dataclass
from typing import Dict

@dataclass
class CheckResult:
    is_breached: bool
    max_allowed: float
    current_exposure: float

class SingleNameConcentrationLimiter:
    def __init__(self, limit_pct: float = 0.1):
        self.limit_pct = limit_pct

    def check_exposure(self, portfolio_value: float, instrument_exposure: float) -> CheckResult:
        if portfolio_value <= 0:
            return CheckResult(False, 0.0, instrument_exposure)
        max_allowed = portfolio_value * self.limit_pct
        return CheckResult(instrument_exposure > max_allowed, max_allowed, instrument_exposure)

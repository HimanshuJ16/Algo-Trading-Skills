from dataclasses import dataclass

@dataclass
class ComplianceResult:
    is_compliant: bool
    reason: str

class MasSingaporeAlgoTradingGuidelinesEngine:
    def __init__(self):
        pass
        
    def run_checks(self, trade_data: dict) -> ComplianceResult:
        if not trade_data:
            return ComplianceResult(False, "Empty trade data")
        if trade_data.get('size', 0) < 0:
            return ComplianceResult(False, "Negative size")
        return ComplianceResult(True, "OK")

from dataclasses import dataclass

@dataclass
class ComplianceResult:
    is_compliant: bool
    reason: str

class RegulatorySandboxProgramsForFintechTestingEngine:
    def check(self, data: dict) -> ComplianceResult:
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

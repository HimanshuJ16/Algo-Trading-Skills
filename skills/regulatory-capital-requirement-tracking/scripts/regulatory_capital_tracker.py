import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class Result:
    """Legacy Result class for backward compatibility."""
    success: bool
    message: str

@dataclass
class CapitalComponents:
    liquid_assets: float
    illiquid_deductions: float
    total_liabilities: float
    subordinated_debt: float = 0.0

@dataclass
class CapitalRequirementSpec:
    jurisdiction: str                    # e.g. 'SEC_15C3_1', 'BASEL_III', 'FCA_IFPR'
    base_minimum_capital: float
    variable_risk_weighted_req: float = 0.0
    warning_buffer_pct: float = 1.20      # Alert if Net Capital < 120% of minimum

@dataclass
class CapitalStatusReport:
    jurisdiction: str
    net_capital_available: float
    total_capital_required: float
    capital_headroom: float
    capital_ratio: float
    is_compliant: bool
    is_warning: bool
    status: str                          # 'COMPLIANT', 'WARNING_BUFFER_BREACHED', 'CAPITAL_DEFICIT'
    audit_notes: str

class RegulatoryCapitalTrackerEngine:
    """
    Regulatory capital requirement tracking engine evaluating liquid assets, deductions,
    and liabilities against regulatory minimum capital requirements (SEC 15c3-1, Basel III, FCA IFPR).
    """
    def __init__(self, spec: Optional[CapitalRequirementSpec] = None):
        self.spec = spec or CapitalRequirementSpec(
            jurisdiction="SEC_15C3_1",
            base_minimum_capital=250000.0,
            variable_risk_weighted_req=0.0,
            warning_buffer_pct=1.20
        )

    def execute(self, param: bool) -> Result:
        """Legacy execute method retained for backward compatibility."""
        if param:
            return Result(True, "Success")
        return Result(False, "Failure")

    def calculate_net_capital(self, components: CapitalComponents) -> float:
        """
        Calculates Net Capital: (Liquid Assets + Subordinated Debt) - (Total Liabilities + Illiquid Deductions)
        """
        net_cap = (components.liquid_assets + components.subordinated_debt) - (components.total_liabilities + components.illiquid_deductions)
        return round(net_cap, 2)

    def evaluate_capital_adequacy(self, components: CapitalComponents) -> CapitalStatusReport:
        """
        Evaluates capital adequacy against regulatory minimums and warning buffers.
        """
        net_capital = self.calculate_net_capital(components)
        total_required = self.spec.base_minimum_capital + self.spec.variable_risk_weighted_req

        if total_required <= 0:
            capital_ratio = 999.0
        else:
            capital_ratio = round(net_capital / total_required, 4)

        headroom = round(net_capital - total_required, 2)
        warning_threshold = total_required * self.spec.warning_buffer_pct

        is_compliant = net_capital >= total_required
        is_warning = is_compliant and (net_capital < warning_threshold)

        if not is_compliant:
            status = "CAPITAL_DEFICIT"
        elif is_warning:
            status = "WARNING_BUFFER_BREACHED"
        else:
            status = "COMPLIANT"

        notes = (
            f"REGULATORY CAPITAL [{status}] ({self.spec.jurisdiction}): "
            f"Net Capital = ${net_capital:,.2f}, Required = ${total_required:,.2f}, "
            f"Headroom = ${headroom:,.2f}, Ratio = {capital_ratio:.2%}."
        )

        if status == "CAPITAL_DEFICIT":
            logger.critical(notes)
        elif status == "WARNING_BUFFER_BREACHED":
            logger.warning(notes)
        else:
            logger.info(notes)

        return CapitalStatusReport(
            jurisdiction=self.spec.jurisdiction,
            net_capital_available=net_capital,
            total_capital_required=total_required,
            capital_headroom=headroom,
            capital_ratio=capital_ratio,
            is_compliant=is_compliant,
            is_warning=is_warning,
            status=status,
            audit_notes=notes
        )

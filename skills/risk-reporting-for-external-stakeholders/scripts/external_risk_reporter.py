import logging
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class InputData:
    """Legacy InputData container for backward compatibility."""
    data: str
    value: float

class ExternalRiskReporter:
    """Legacy class retained for backward compatibility."""
    def __init__(self):
        self.history: List[InputData] = []

    def process(self, data: InputData) -> bool:
        self.history.append(data)
        if data.value < 0:
            return False
        return True

class StakeholderType(str, Enum):
    LIMITED_PARTNER = "LIMITED_PARTNER"  # LP / Institutional Investor
    REGULATOR = "REGULATOR"              # SEC Form PF, FCA Annex IV, AIFMD
    PRIME_BROKER = "PRIME_BROKER"        # Daily leverage & concentration feed
    AUDITOR = "AUDITOR"                  # Independent SOC 1 / Annual audit

@dataclass
class PortfolioRiskState:
    fund_name: str
    report_date_iso: str
    total_aum_usd: float
    net_asset_value_usd: float
    gross_exposure_usd: float
    net_exposure_usd: float
    daily_var_99_pct: float
    annualized_sharpe_ratio: float
    max_drawdown_pct: float
    top_sector_concentrations: Dict[str, float]  # e.g., {'TECH': 25.0, 'FINANCE': 15.0}
    liquidity_days_to_liquidate_pct: Dict[str, float] # e.g. {'1_DAY': 85.0, '7_DAYS': 100.0}
    proprietary_positions: Optional[List[Dict[str, Any]]] = None # Secret position details

@dataclass
class ExternalRiskReport:
    report_id: str
    stakeholder_type: StakeholderType
    fund_name: str
    report_date_iso: str
    total_aum_usd: float
    gross_leverage: float
    net_leverage: float
    daily_var_99_pct: float
    annualized_sharpe: float
    max_drawdown_pct: float
    disclosed_concentrations: Dict[str, float]
    disclosed_liquidity: Dict[str, float]
    are_proprietary_positions_redacted: bool
    report_signature: str
    audit_notes: str

class RiskReportingForExternalStakeholdersEngine:
    """
    Production-grade risk reporting engine generating customized, compliance-cleared risk reports
    for external stakeholders (LPs, Regulators, Prime Brokers) with automated proprietary redaction.
    """
    def __init__(self, firm_name: str = "Quantitative Asset Management"):
        self.firm_name = firm_name

    def generate_external_report(
        self,
        state: PortfolioRiskState,
        stakeholder_type: StakeholderType
    ) -> ExternalRiskReport:
        """
        Generates customized external risk report for target stakeholder, ensuring proprietary
        alpha positions are redacted while exposing required risk metrics.
        """
        gross_leverage = round(state.gross_exposure_usd / max(state.net_asset_value_usd, 1.0), 2)
        net_leverage = round(state.net_exposure_usd / max(state.net_asset_value_usd, 1.0), 2)

        # Enforce Information Barrier & Position Redaction
        redacted = True  # Always redact exact proprietary positions for external reports

        # Tailor disclosure level per stakeholder
        if stakeholder_type == StakeholderType.REGULATOR:
            # Form PF / Annex IV requires full sector concentrations
            disclosed_conc = dict(state.top_sector_concentrations)
            disclosed_liq = dict(state.liquidity_days_to_liquidate_pct)
        elif stakeholder_type == StakeholderType.LIMITED_PARTNER:
            # LP receives top 5 sector breakdown
            disclosed_conc = {k: v for k, v in list(state.top_sector_concentrations.items())[:5]}
            disclosed_liq = dict(state.liquidity_days_to_liquidate_pct)
        elif stakeholder_type == StakeholderType.PRIME_BROKER:
            # Prime broker receives gross/net leverage and liquidity metrics
            disclosed_conc = {k: v for k, v in list(state.top_sector_concentrations.items())[:3]}
            disclosed_liq = dict(state.liquidity_days_to_liquidate_pct)
        else:
            disclosed_conc = dict(state.top_sector_concentrations)
            disclosed_liq = dict(state.liquidity_days_to_liquidate_pct)

        # Generate cryptographic report signature (SHA-256)
        sig_raw = f"{state.fund_name}:{stakeholder_type.value}:{state.report_date_iso}:{state.net_asset_value_usd}:{gross_leverage}".encode('utf-8')
        report_sig = hashlib.sha256(sig_raw).hexdigest()

        notes = (
            f"EXTERNAL RISK REPORT [{stakeholder_type.value}] ({state.fund_name}): "
            f"NAV = ${state.net_asset_value_usd:,.2f}, Gross Lev = {gross_leverage}x, "
            f"Net Lev = {net_leverage}x, 99% VaR = {state.daily_var_99_pct}%, "
            f"Positions Redacted = {redacted}."
        )

        logger.info(notes)

        return ExternalRiskReport(
            report_id=f"RPT_{stakeholder_type.value}_{state.report_date_iso}",
            stakeholder_type=stakeholder_type,
            fund_name=state.fund_name,
            report_date_iso=state.report_date_iso,
            total_aum_usd=state.total_aum_usd,
            gross_leverage=gross_leverage,
            net_leverage=net_leverage,
            daily_var_99_pct=state.daily_var_99_pct,
            annualized_sharpe=state.annualized_sharpe_ratio,
            max_drawdown_pct=state.max_drawdown_pct,
            disclosed_concentrations=disclosed_conc,
            disclosed_liquidity=disclosed_liq,
            are_proprietary_positions_redacted=redacted,
            report_signature=report_sig,
            audit_notes=notes
        )

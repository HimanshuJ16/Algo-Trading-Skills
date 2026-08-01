import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str

@dataclass
class SandboxParameters:
    program_name: str                    # e.g. 'FCA_UK_SANDBOX', 'MAS_SG_SANDBOX', 'SEBI_IN_SANDBOX'
    jurisdiction: str
    max_allowed_clients: int
    max_transaction_volume_usd: float
    max_aum_usd: float
    max_duration_months: int
    exit_strategy_documented: bool = True

DEFAULT_SANDBOX_PROGRAMS: Dict[str, SandboxParameters] = {
    "FCA_UK": SandboxParameters("FCA_UK_SANDBOX", "UK", max_allowed_clients=500, max_transaction_volume_usd=5000000.0, max_aum_usd=10000000.0, max_duration_months=12),
    "MAS_SG": SandboxParameters("MAS_SG_SANDBOX", "SG", max_allowed_clients=1000, max_transaction_volume_usd=10000000.0, max_aum_usd=20000000.0, max_duration_months=9),
    "SEBI_IN": SandboxParameters("SEBI_IN_SANDBOX", "IN", max_allowed_clients=200, max_transaction_volume_usd=2000000.0, max_aum_usd=5000000.0, max_duration_months=6),
}

@dataclass
class SandboxTelemetry:
    program_key: str
    active_clients: int
    cumulative_volume_usd: float
    current_aum_usd: float
    elapsed_months: int
    has_exit_plan: bool = True

@dataclass
class SandboxBreach:
    breach_type: str                     # 'CLIENT_LIMIT_BREACH', 'VOLUME_CAP_BREACH', 'AUM_CAP_BREACH', 'SANDBOX_EXPIRED', 'MISSING_EXIT_PLAN'
    detail: str

@dataclass
class SandboxAuditReport:
    program_name: str
    is_within_limits: bool
    breaches: List[SandboxBreach]
    client_capacity_pct: float
    volume_capacity_pct: float
    aum_capacity_pct: float
    time_remaining_months: int
    status: str                          # 'SANDBOX_COMPLIANT', 'SANDBOX_BREACHED', 'PROGRAM_NOT_FOUND'
    audit_notes: str

class RegulatorySandboxProgramsForFintechTestingEngine:
    """
    Regulatory sandbox program engine evaluating live testing telemetry against statutory sandbox
    boundaries (FCA, MAS, SEBI) to prevent unpermitted retail exposure or volume cap overruns.
    """
    def __init__(self, custom_programs: Optional[Dict[str, SandboxParameters]] = None):
        self.programs = dict(DEFAULT_SANDBOX_PROGRAMS)
        if custom_programs:
            self.programs.update(custom_programs)

    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    def audit_sandbox_telemetry(self, telemetry: SandboxTelemetry) -> SandboxAuditReport:
        """
        Audits live testing telemetry against sandbox program boundaries.
        """
        params = self.programs.get(telemetry.program_key.upper())

        if not params:
            return SandboxAuditReport(
                program_name=telemetry.program_key,
                is_within_limits=False,
                breaches=[SandboxBreach("PROGRAM_NOT_FOUND", f"Sandbox program '{telemetry.program_key}' not found.")],
                client_capacity_pct=0.0,
                volume_capacity_pct=0.0,
                aum_capacity_pct=0.0,
                time_remaining_months=0,
                status="PROGRAM_NOT_FOUND",
                audit_notes=f"Sandbox program '{telemetry.program_key}' not registered."
            )

        breaches: List[SandboxBreach] = []

        # 1. Client limit check
        client_cap_pct = round((telemetry.active_clients / params.max_allowed_clients) * 100.0, 2)
        if telemetry.active_clients > params.max_allowed_clients:
            breaches.append(SandboxBreach(
                "CLIENT_LIMIT_BREACH",
                f"Active clients ({telemetry.active_clients}) exceeds sandbox cap ({params.max_allowed_clients})."
            ))

        # 2. Volume cap check
        vol_cap_pct = round((telemetry.cumulative_volume_usd / params.max_transaction_volume_usd) * 100.0, 2)
        if telemetry.cumulative_volume_usd > params.max_transaction_volume_usd:
            breaches.append(SandboxBreach(
                "VOLUME_CAP_BREACH",
                f"Cumulative volume (${telemetry.cumulative_volume_usd:,.2f}) exceeds sandbox cap (${params.max_transaction_volume_usd:,.2f})."
            ))

        # 3. AUM cap check
        aum_cap_pct = round((telemetry.current_aum_usd / params.max_aum_usd) * 100.0, 2)
        if telemetry.current_aum_usd > params.max_aum_usd:
            breaches.append(SandboxBreach(
                "AUM_CAP_BREACH",
                f"Current AUM (${telemetry.current_aum_usd:,.2f}) exceeds sandbox cap (${params.max_aum_usd:,.2f})."
            ))

        # 4. Duration check
        time_rem = params.max_duration_months - telemetry.elapsed_months
        if telemetry.elapsed_months > params.max_duration_months:
            breaches.append(SandboxBreach(
                "SANDBOX_EXPIRED",
                f"Elapsed testing ({telemetry.elapsed_months} mos) exceeds sandbox max duration ({params.max_duration_months} mos)."
            ))

        # 5. Exit plan check
        if not telemetry.has_exit_plan or not params.exit_strategy_documented:
            breaches.append(SandboxBreach(
                "MISSING_EXIT_PLAN",
                "Testing exit strategy / risk mitigation plan is not documented."
            ))

        is_ok = len(breaches) == 0
        status = "SANDBOX_COMPLIANT" if is_ok else "SANDBOX_BREACHED"

        notes = (
            f"REGULATORY SANDBOX [{status}] ({params.program_name}): "
            f"Clients = {telemetry.active_clients}/{params.max_allowed_clients} ({client_cap_pct}%), "
            f"Volume = ${telemetry.cumulative_volume_usd:,.2f}/{params.max_transaction_volume_usd:,.2f} ({vol_cap_pct}%), "
            f"Time Rem = {time_rem} mos, Breaches = {len(breaches)}."
        )

        if not is_ok:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SandboxAuditReport(
            program_name=params.program_name,
            is_within_limits=is_ok,
            breaches=breaches,
            client_capacity_pct=client_cap_pct,
            volume_capacity_pct=vol_cap_pct,
            aum_capacity_pct=aum_cap_pct,
            time_remaining_months=time_rem,
            status=status,
            audit_notes=notes
        )

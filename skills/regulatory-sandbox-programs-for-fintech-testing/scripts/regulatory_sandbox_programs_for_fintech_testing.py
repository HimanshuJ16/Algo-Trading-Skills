"""Regulatory sandbox boundary-condition monitoring engine.

Financial-sector regulators (FCA UK, MAS Singapore, SEBI India, and others) do NOT
publish universal numeric caps for sandbox tests. Every published framework sets the
limits -- number of customers, transaction/exposure thresholds, duration -- per test,
in the approval letter or agreed "boundary conditions"/"testing parameters" document:

  * FCA, "Default standards for sandbox testing parameters": the FCA "will set a strict
    limit to the size of the test"; testing plans must state "Testing parameters
    (duration, customer/transaction limit)". Stakeholder consensus at the FCA sandbox
    event was that 3-6 months is an appropriate duration. No firm-independent numbers.
  * MAS, "FinTech Regulatory Sandbox Guidelines": boundary conditions "should clearly
    define the start and end date", "limits on the number of customers" and "other
    quantifiable limits such as transaction thresholds or cash holding limits", agreed
    with MAS per experiment. Only Sandbox Express has a published fixed ceiling
    (up to nine months).
  * SEBI, Revised Framework for Regulatory Sandbox: two testing stages with user sets
    approved by SEBI, cumulatively not exceeding twelve months unless extended.

This module therefore ships NO default numeric caps. The caller must register the
limits transcribed from its own sandbox approval, and an unregistered program fails
closed with ``PROGRAM_NOT_FOUND`` rather than being silently audited against a
plausible-looking but fabricated cap.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Breach type constants (also usable as report/alert routing keys).
CLIENT_LIMIT_BREACH = "CLIENT_LIMIT_BREACH"
VOLUME_CAP_BREACH = "VOLUME_CAP_BREACH"
AUM_CAP_BREACH = "AUM_CAP_BREACH"
SANDBOX_EXPIRED = "SANDBOX_EXPIRED"
MISSING_EXIT_PLAN = "MISSING_EXIT_PLAN"
PROGRAM_NOT_FOUND = "PROGRAM_NOT_FOUND"


@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str


@dataclass(frozen=True)
class SandboxFramework:
    """Descriptive, source-backed metadata about a regulator's sandbox framework.

    Deliberately carries no numeric caps: these frameworks set limits per test, so a
    numeric value here would be misinformation. Use it to document which regulator and
    citation a set of :class:`SandboxParameters` was transcribed from.
    """
    key: str
    regulator: str
    jurisdiction: str
    program_name: str
    live_customers_permitted: bool
    limits_set_per_test: bool
    published_duration_note: str
    citation: str


#: Reference metadata only -- never a source of enforceable limits.
SANDBOX_FRAMEWORKS: Dict[str, SandboxFramework] = {
    "FCA_UK": SandboxFramework(
        key="FCA_UK",
        regulator="Financial Conduct Authority",
        jurisdiction="UK",
        program_name="FCA Regulatory Sandbox",
        live_customers_permitted=True,
        limits_set_per_test=True,
        published_duration_note=(
            "No published statutory maximum. FCA 'Default standards for sandbox testing "
            "parameters' records 3-6 months as the duration most stakeholders considered "
            "appropriate; the agreed duration is set per test."
        ),
        citation="https://www.fca.org.uk/publication/policy/default-standards-for-sandbox-testing-parameters.pdf",
    ),
    "MAS_SG": SandboxFramework(
        key="MAS_SG",
        regulator="Monetary Authority of Singapore",
        jurisdiction="SG",
        program_name="MAS FinTech Regulatory Sandbox",
        live_customers_permitted=True,
        limits_set_per_test=True,
        published_duration_note=(
            "Boundary conditions (start/end date, customer-type and customer-number "
            "limits, transaction thresholds, maximum loss) are agreed with MAS per "
            "experiment. Only Sandbox Express carries a published ceiling of up to nine "
            "months."
        ),
        citation="https://www.mas.gov.sg/-/media/mas-media-library/development/regulatory-sandbox/sandbox/fintech-regulatory-sandbox-guidelines-jan-2022.pdf",
    ),
    "SEBI_IN": SandboxFramework(
        key="SEBI_IN",
        regulator="Securities and Exchange Board of India",
        jurisdiction="IN",
        program_name="SEBI Regulatory Sandbox",
        live_customers_permitted=True,
        limits_set_per_test=True,
        published_duration_note=(
            "Open to SEBI-regulated entities testing on a limited set of real users. "
            "Stage-I and Stage-II testing cumulatively must not exceed twelve months "
            "unless extended; Stage-II eligibility requires at least 90 days in Stage-I."
        ),
        citation="https://www.sebi.gov.in/legal/circulars/jun-2021/revised-framework-for-regulatory-sandbox_50521.html",
    ),
    "SEBI_IN_INNOVATION": SandboxFramework(
        key="SEBI_IN_INNOVATION",
        regulator="Securities and Exchange Board of India",
        jurisdiction="IN",
        program_name="SEBI Innovation Sandbox",
        live_customers_permitted=False,
        limits_set_per_test=False,
        published_duration_note=(
            "OUT OF SCOPE for this engine. The Innovation Sandbox is offline testing "
            "'in isolation from the live market' on historical, anonymised datasets; "
            "'Live data shall not be made available to participants'. It has no live "
            "clients, no live AUM and no live traded volume to audit."
        ),
        citation="https://www.sebi.gov.in/legal/circulars/may-2019/framework-for-innovation-sandbox_43027.html",
    ),
}


@dataclass
class SandboxParameters:
    """Boundary conditions transcribed from a specific sandbox approval.

    Every cap must come from the firm's own approval letter / agreed testing
    parameters. There is no defensible cross-firm default for any of these values.

    Args:
        program_name: Human-readable label for the approved test.
        jurisdiction: Jurisdiction the approval was granted in.
        max_allowed_clients: Approved ceiling on concurrently onboarded test clients.
        max_transaction_volume_usd: Approved cumulative transaction-value ceiling.
        max_aum_usd: Approved ceiling on assets held/managed under the test.
        max_duration_months: Approved testing duration, excluding any extension.
        approved_extension_months: Formally granted extension, if any. Only set this
            once the extension is granted in writing -- an assumed extension is the
            most common way a firm ends up trading without regulatory relief.
        requires_exit_strategy: Whether the approval conditions require a documented
            exit / client-transition plan. True for every framework this engine ships
            metadata for; set False only if a specific approval genuinely does not.
        framework_key: Optional key into :data:`SANDBOX_FRAMEWORKS`, recording which
            regulator's framework these limits were transcribed from.
    """
    program_name: str
    jurisdiction: str
    max_allowed_clients: int
    max_transaction_volume_usd: float
    max_aum_usd: float
    max_duration_months: int
    approved_extension_months: int = 0
    requires_exit_strategy: bool = True
    framework_key: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.program_name or not self.program_name.strip():
            raise ValueError("program_name must be a non-empty string.")
        if self.max_allowed_clients <= 0:
            raise ValueError(
                f"max_allowed_clients must be > 0 (got {self.max_allowed_clients}); "
                "a zero or negative cap cannot be audited against."
            )
        if self.max_transaction_volume_usd <= 0:
            raise ValueError(
                f"max_transaction_volume_usd must be > 0 (got {self.max_transaction_volume_usd})."
            )
        if self.max_aum_usd <= 0:
            raise ValueError(f"max_aum_usd must be > 0 (got {self.max_aum_usd}).")
        if self.max_duration_months <= 0:
            raise ValueError(
                f"max_duration_months must be > 0 (got {self.max_duration_months})."
            )
        if self.approved_extension_months < 0:
            raise ValueError(
                f"approved_extension_months must be >= 0 (got {self.approved_extension_months})."
            )
        if self.framework_key is not None and self.framework_key not in SANDBOX_FRAMEWORKS:
            raise ValueError(
                f"Unknown framework_key '{self.framework_key}'. "
                f"Known keys: {sorted(SANDBOX_FRAMEWORKS)}."
            )

    @property
    def effective_duration_months(self) -> int:
        """Approved duration including any formally granted extension."""
        return self.max_duration_months + self.approved_extension_months


@dataclass
class SandboxTelemetry:
    """Observed live testing state for one sandbox program.

    Args:
        program_key: Registry key of the approved program to audit against.
        active_clients: Clients currently onboarded into the test.
        cumulative_volume_usd: Transaction value traded since the test start, not the
            current open position -- sandbox volume caps are cumulative.
        current_aum_usd: Assets currently held or managed under the test.
        elapsed_months: Whole months elapsed since the approved test start date.
        has_exit_plan: Whether a documented exit / client-transition plan is in place.
    """
    program_key: str
    active_clients: int
    cumulative_volume_usd: float
    current_aum_usd: float
    elapsed_months: int
    has_exit_plan: bool = True

    def __post_init__(self) -> None:
        if not self.program_key or not self.program_key.strip():
            raise ValueError("program_key must be a non-empty string.")
        if self.active_clients < 0:
            raise ValueError(f"active_clients must be >= 0 (got {self.active_clients}).")
        if self.cumulative_volume_usd < 0:
            raise ValueError(
                f"cumulative_volume_usd must be >= 0 (got {self.cumulative_volume_usd})."
            )
        if self.current_aum_usd < 0:
            raise ValueError(f"current_aum_usd must be >= 0 (got {self.current_aum_usd}).")
        if self.elapsed_months < 0:
            raise ValueError(f"elapsed_months must be >= 0 (got {self.elapsed_months}).")


@dataclass
class SandboxBreach:
    breach_type: str
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
    warnings: List[str] = field(default_factory=list)


class RegulatorySandboxProgramsForFintechTestingEngine:
    """Audits live sandbox testing telemetry against approved boundary conditions.

    The engine ships no default limits. Register the caps from your own approval:

        engine = RegulatorySandboxProgramsForFintechTestingEngine({
            "FCA_UK": SandboxParameters(
                program_name="FCA_UK_SANDBOX",
                jurisdiction="UK",
                max_allowed_clients=500,          # from the approval letter
                max_transaction_volume_usd=5_000_000.0,
                max_aum_usd=10_000_000.0,
                max_duration_months=6,
                framework_key="FCA_UK",
            ),
        })

    An unregistered ``program_key`` fails closed with ``PROGRAM_NOT_FOUND`` and
    ``is_within_limits=False``.
    """

    #: Default capacity utilisation (%) at which a pre-breach warning is emitted.
    DEFAULT_WARNING_THRESHOLD_PCT = 80.0

    def __init__(
        self,
        custom_programs: Optional[Dict[str, SandboxParameters]] = None,
        warning_threshold_pct: float = DEFAULT_WARNING_THRESHOLD_PCT,
    ) -> None:
        if not 0.0 < warning_threshold_pct <= 100.0:
            raise ValueError(
                f"warning_threshold_pct must be in (0, 100] (got {warning_threshold_pct})."
            )
        self.warning_threshold_pct = warning_threshold_pct
        self.programs: Dict[str, SandboxParameters] = {}
        if custom_programs:
            for key, params in custom_programs.items():
                self.register_program(key, params)

    def register_program(self, key: str, params: SandboxParameters) -> None:
        """Register (or replace) the approved boundary conditions for one program."""
        if not key or not key.strip():
            raise ValueError("Program key must be a non-empty string.")
        if not isinstance(params, SandboxParameters):
            raise TypeError(
                f"params must be a SandboxParameters instance, got {type(params).__name__}."
            )
        self.programs[key.upper()] = params

    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    def audit_sandbox_telemetry(self, telemetry: SandboxTelemetry) -> SandboxAuditReport:
        """Audit live testing telemetry against the registered sandbox boundaries.

        Caps are inclusive maxima: utilisation exactly at the cap is compliant, and any
        value strictly above it is a breach. Elapsed testing time is compared against
        the approved duration plus any formally granted extension.
        """
        params = self.programs.get(telemetry.program_key.upper())

        if params is None:
            notes = (
                f"Sandbox program '{telemetry.program_key}' is not registered. Register "
                "the boundary conditions from the regulator's approval before auditing; "
                "this engine ships no default caps."
            )
            logger.error(notes)
            return SandboxAuditReport(
                program_name=telemetry.program_key,
                is_within_limits=False,
                breaches=[SandboxBreach(PROGRAM_NOT_FOUND, notes)],
                client_capacity_pct=0.0,
                volume_capacity_pct=0.0,
                aum_capacity_pct=0.0,
                time_remaining_months=0,
                status=PROGRAM_NOT_FOUND,
                audit_notes=notes,
            )

        breaches: List[SandboxBreach] = []
        warnings: List[str] = []

        # 1. Client limit check.
        client_cap_pct = round((telemetry.active_clients / params.max_allowed_clients) * 100.0, 2)
        if telemetry.active_clients > params.max_allowed_clients:
            breaches.append(SandboxBreach(
                CLIENT_LIMIT_BREACH,
                f"Active clients ({telemetry.active_clients}) exceeds approved cap "
                f"({params.max_allowed_clients})."
            ))

        # 2. Cumulative volume cap check.
        vol_cap_pct = round(
            (telemetry.cumulative_volume_usd / params.max_transaction_volume_usd) * 100.0, 2
        )
        if telemetry.cumulative_volume_usd > params.max_transaction_volume_usd:
            breaches.append(SandboxBreach(
                VOLUME_CAP_BREACH,
                f"Cumulative volume (${telemetry.cumulative_volume_usd:,.2f}) exceeds "
                f"approved cap (${params.max_transaction_volume_usd:,.2f})."
            ))

        # 3. AUM cap check.
        aum_cap_pct = round((telemetry.current_aum_usd / params.max_aum_usd) * 100.0, 2)
        if telemetry.current_aum_usd > params.max_aum_usd:
            breaches.append(SandboxBreach(
                AUM_CAP_BREACH,
                f"Current AUM (${telemetry.current_aum_usd:,.2f}) exceeds approved cap "
                f"(${params.max_aum_usd:,.2f})."
            ))

        # 4. Duration check, inclusive of any formally granted extension.
        effective_months = params.effective_duration_months
        time_rem = effective_months - telemetry.elapsed_months
        if telemetry.elapsed_months > effective_months:
            breaches.append(SandboxBreach(
                SANDBOX_EXPIRED,
                f"Elapsed testing ({telemetry.elapsed_months} mos) exceeds approved "
                f"duration ({effective_months} mos, including "
                f"{params.approved_extension_months} mos extension)."
            ))

        # 5. Exit / client-transition plan check.
        if params.requires_exit_strategy and not telemetry.has_exit_plan:
            breaches.append(SandboxBreach(
                MISSING_EXIT_PLAN,
                "Approval requires a documented exit / client-transition plan, but none "
                "is recorded."
            ))

        # 6. Pre-breach warnings: a breach has already voided the relief, so the
        #    actionable signal is utilisation approaching the cap.
        for label, pct in (
            ("client", client_cap_pct),
            ("volume", vol_cap_pct),
            ("AUM", aum_cap_pct),
        ):
            if self.warning_threshold_pct <= pct <= 100.0:
                warnings.append(
                    f"{label} utilisation at {pct}% of approved cap "
                    f"(warn >= {self.warning_threshold_pct}%)."
                )
        if 0 <= time_rem <= 1:
            warnings.append(
                f"{time_rem} month(s) of approved testing remaining; confirm the exit "
                "or extension path now."
            )

        is_ok = not breaches
        status = "SANDBOX_COMPLIANT" if is_ok else "SANDBOX_BREACHED"

        notes = (
            f"REGULATORY SANDBOX [{status}] ({params.program_name}): "
            f"Clients = {telemetry.active_clients}/{params.max_allowed_clients} ({client_cap_pct}%), "
            f"Volume = ${telemetry.cumulative_volume_usd:,.2f}/${params.max_transaction_volume_usd:,.2f} ({vol_cap_pct}%), "
            f"AUM = ${telemetry.current_aum_usd:,.2f}/${params.max_aum_usd:,.2f} ({aum_cap_pct}%), "
            f"Time Rem = {time_rem} mos, Breaches = {len(breaches)}, Warnings = {len(warnings)}."
        )

        if not is_ok:
            logger.warning(notes)
        elif warnings:
            logger.info("%s Warnings: %s", notes, "; ".join(warnings))
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
            audit_notes=notes,
            warnings=warnings,
        )

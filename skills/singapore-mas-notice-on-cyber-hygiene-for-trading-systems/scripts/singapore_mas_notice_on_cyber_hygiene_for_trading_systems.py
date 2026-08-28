"""Audit engine for the MAS Notice on Cyber Hygiene, applied to trading system assets.

WHICH NOTICE BINDS YOU
----------------------
There is no single "MAS Notice on Cyber Hygiene". MAS issues the *same* six
requirements as a separate Notice to each class of financial institution, and
the notice number differs by class. On 10 May 2024 the earlier class notices
(Notice 655 for banks, Notice CMG-N03 for capital markets entities, PSN06,
TCA-N06 and others) were cancelled and reissued under the Financial Services
and Markets Act 2022:

  * ``FSM-N06`` -- banks in Singapore.
  * ``FSM-N22`` -- capital markets financial institutions, the successor to
    CMG-N03. A trading firm holding a Capital Markets Services licence sits
    here, **not** under FSM-N06.

Citing FSM-N06 in a capital markets firm's audit file cites a notice that does
not bind it. The engine therefore requires the caller to declare which notice
applies (`entity_notice`) and stamps it on every report.

WHAT THE NOTICE ACTUALLY REQUIRES
---------------------------------
Six requirements, every one of them mandatory. Paragraph numbers below are
those of the Notice on Cyber Hygiene as originally issued (paragraphs 4.1-4.6);
confirm the numbering in the reissued notice that binds your entity class.

  4.1 Administrative accounts -- every administrative account in respect of any
      operating system, database, application, security appliance or network
      device must be secured to prevent any unauthorised access to or use of
      such account.
  4.2 Security patches -- (a) security patches must be applied to address
      vulnerabilities to every system, *within a timeframe that is commensurate
      with the risks posed by each vulnerability*; (b) where no security patch
      is available, controls must be instituted to reduce the risk posed.
  4.3 Security standards -- (a) a written set of security standards for every
      system; (b) every system conforms to them, subject to (c); (c) where a
      system cannot conform, controls must be instituted to reduce the risk
      posed by that non-conformity.
  4.4 Network perimeter defence -- controls implemented at the network
      perimeter to restrict all unauthorised network traffic.
  4.5 Malware protection -- one or more malware protection measures on every
      system, *where such measures are available and can be implemented*.
  4.6 Multi-factor authentication -- (a) all administrative accounts in respect
      of any operating system, database, application, security appliance or
      network device **that is a critical system**; and (b) all accounts on any
      system used to access **customer information through the internet**.

THREE THINGS THE NOTICE DOES NOT SAY
------------------------------------
  * **No 30-day patching SLA.** The Notice fixes no number. It requires a
    timeframe commensurate with the risk each vulnerability poses, and the MAS
    Technology Risk Management Guidelines say the same in guidance form
    ("commensurate with the criticality of the patches and the FI's IT
    systems"). Shipping "30 days" as a MAS mandate is regulatory
    misinformation in both directions: it is far too slow for an actively
    exploited remote code execution flaw on an order gateway, and it invents an
    obligation for a low-severity issue on an isolated host. The remediation
    deadline is therefore a **firm-set** `PatchRemediationPolicy` the caller
    must supply and calibrate.
  * **MFA is not required on every administrative account everywhere.** Limb
    (a) is scoped to *critical systems*. A firm that reads it as universal will
    fail its own audit on hosts the Notice never reached; a firm that ignores
    limb (b) will pass a non-critical internet-facing system that handles
    customer information and is squarely in scope.
  * **CIS benchmarks are not mandated.** Paragraph 4.3 requires *a written set
    of security standards* and conformance to it. CIS Benchmarks are a common
    and reasonable way to author that set; they are an industry benchmark, not
    the regulatory requirement.

MODEL BOUNDARIES
----------------
This engine reads assertions the caller supplies. It cannot observe a host, and
a clean report is evidence that a control was *attested*, never that it is in
place. Unknown scope resolves conservatively -- an asset of unknown criticality
is audited as critical -- so a missing field can never make a breaching asset
look compliant.

Sources: see references/standards.md.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

STATUS_COMPLIANT: str = "MAS_CYBER_HYGIENE_COMPLIANT"
STATUS_BREACH: str = "MAS_CYBER_HYGIENE_BREACH"

# Cyber Hygiene notice numbers in force since the 10 May 2024 reissuance under
# the Financial Services and Markets Act 2022, keyed by entity class. The
# operative requirements are the same in each; the applicable notice number is
# not. Extend for other regulated classes as needed -- see references/standards.md.
CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS: Mapping[str, str] = {
    "BANK": "FSM-N06",
    "CAPITAL_MARKETS": "FSM-N22",
}

# Severity labels a vulnerability may carry. The labels are the caller's own
# risk taxonomy; MAS prescribes none.
VULNERABILITY_SEVERITIES: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class MASCyberHygieneRequirement(str, Enum):
    """The six requirements of the Notice, in paragraph order.

    Member names are preserved from version 1.0.0 for report compatibility; the
    values map to Notice paragraphs 4.1 through 4.6.
    """

    ADMIN_ACCOUNT_SECURITY = "ADMIN_ACCOUNT_SECURITY"            # para 4.1
    SECURITY_PATCH_MANAGEMENT = "SECURITY_PATCH_MANAGEMENT"      # para 4.2
    BASELINE_SECURITY_STANDARDS = "BASELINE_SECURITY_STANDARDS"  # para 4.3
    NETWORK_PERIMETER_DEFENSE = "NETWORK_PERIMETER_DEFENSE"      # para 4.4
    ANTI_MALWARE_PROTECTION = "ANTI_MALWARE_PROTECTION"          # para 4.5
    MULTI_FACTOR_AUTH = "MULTI_FACTOR_AUTH"                      # para 4.6


@dataclass(frozen=True)
class PatchRemediationPolicy:
    """FIRM-SET remediation deadlines, in days, by vulnerability severity.

    The Notice requires patches to be applied "within a timeframe that is
    commensurate with the risks posed by each vulnerability" and fixes no
    number. This policy is the firm's articulation of that timeframe. It is
    deliberately mandatory and has no default: a default here would be a
    fabricated regulatory threshold, which is exactly the defect this class
    exists to prevent.

    Attributes:
        max_days_by_severity: Maximum days a vulnerability of each severity may
            remain unpatched once a patch is available. A severity that appears
            on an asset but not in this mapping raises, rather than silently
            passing an unmeasured vulnerability. The mapping supplied by the
            caller is copied and exposed read-only, so approved deadlines cannot
            be edited -- past validation -- after the policy is constructed.
    """

    max_days_by_severity: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.max_days_by_severity:
            raise ValueError(
                "max_days_by_severity must not be empty; the firm must set its own "
                "risk-commensurate patching deadlines (MAS Notice on Cyber Hygiene "
                "para 4.2(a) prescribes no figure)."
            )
        for severity, days in self.max_days_by_severity.items():
            if severity not in VULNERABILITY_SEVERITIES:
                raise ValueError(
                    f"unknown severity {severity!r}; expected one of {VULNERABILITY_SEVERITIES}"
                )
            if isinstance(days, bool) or not isinstance(days, int) or days < 0:
                raise ValueError(
                    f"max_days_by_severity[{severity!r}] must be a non-negative int, got {days!r}"
                )
        # The dataclass is frozen but the mapping it was handed is not. Copy and
        # wrap it, so a caller holding the original dict cannot rewrite the
        # firm's approved deadlines behind an already-validated policy.
        object.__setattr__(
            self, "max_days_by_severity", MappingProxyType(dict(self.max_days_by_severity))
        )

    def max_days_for(self, severity: str) -> int:
        """Deadline for `severity`, raising if the policy does not cover it."""
        if severity not in self.max_days_by_severity:
            raise ValueError(
                f"PatchRemediationPolicy does not cover severity {severity!r}. "
                "Failing closed rather than leaving the vulnerability unmeasured."
            )
        return self.max_days_by_severity[severity]


@dataclass(frozen=True)
class OpenVulnerability:
    """One unremediated vulnerability on a trading system asset.

    Attributes:
        vulnerability_id: Tracking identifier, e.g. a CVE or internal ticket.
        severity: One of `VULNERABILITY_SEVERITIES`, per the firm's taxonomy.
        days_since_patch_released: Days elapsed since a patch became available.
            `None` means **no patch is available**, which moves the
            vulnerability from Notice para 4.2(a) to para 4.2(b).
        compensating_controls_in_place: Controls instituted to reduce the risk
            posed. This satisfies para 4.2(b) where no patch exists. It does
            **not** excuse an overdue *available* patch under para 4.2(a).
    """

    vulnerability_id: str
    severity: str
    days_since_patch_released: Optional[int] = None
    compensating_controls_in_place: bool = False

    def __post_init__(self) -> None:
        if not self.vulnerability_id or not self.vulnerability_id.strip():
            raise ValueError("vulnerability_id must be a non-empty string")
        if self.severity not in VULNERABILITY_SEVERITIES:
            raise ValueError(
                f"severity must be one of {VULNERABILITY_SEVERITIES}, got {self.severity!r}"
            )
        days = self.days_since_patch_released
        if days is not None:
            # A negative age would compare below every deadline and silently
            # pass; a bool is an int in Python and must not be accepted here.
            if isinstance(days, bool) or not isinstance(days, int) or days < 0:
                raise ValueError(
                    "days_since_patch_released must be None (no patch available) "
                    f"or a non-negative int, got {days!r}"
                )


@dataclass(frozen=True)
class TradingSystemAsset:
    """A trading system asset presented for a Cyber Hygiene audit.

    Every control flag defaults to the non-compliant value so that an
    incompletely populated asset fails closed rather than passing by omission.

    Attributes:
        system_id: Stable asset identifier used in the audit trail.
        system_name: Human-readable name.
        asset_type: Free-form class, e.g. 'ORDER_ROUTER', 'MARKET_DATA_GATEWAY',
            'TRADING_DB'.
        is_critical_system: Whether the asset is a *critical system* -- one
            whose failure will cause significant disruption to the entity's
            operations or materially impact its service to customers. This
            determines whether MFA limb 4.6(a) applies. `None` means unknown
            and resolves **conservatively to critical**.
        accesses_customer_information_over_internet: Whether the asset is used
            to access customer information through the internet, which brings
            every account on it into MFA limb 4.6(b). `None` means unknown and
            resolves **conservatively to in scope**.
        administrative_accounts_secured: Para 4.1 -- every administrative
            account on the asset is secured against unauthorised access or use.
        open_vulnerabilities: Para 4.2 -- vulnerabilities currently open on the
            asset. An empty tuple asserts none are open.
        has_written_security_standards: Para 4.3(a).
        conforms_to_security_standards: Para 4.3(b).
        nonconformity_controls_in_place: Para 4.3(c) -- controls instituted to
            reduce the risk posed by a documented non-conformity.
        network_perimeter_controls_implemented: Para 4.4.
        malware_protection_implemented: Para 4.5.
        malware_protection_unavailable_justification: Para 4.5 applies only
            "where such malware protection measures are available and can be
            implemented". A non-blank justification records that carve-out and
            converts the finding from a breach into an auditable warning. Blank
            or `None` means no carve-out is claimed.
        mfa_on_administrative_accounts: Para 4.6(a).
        mfa_on_customer_information_accounts: Para 4.6(b).
    """

    system_id: str
    system_name: str
    asset_type: str
    is_critical_system: Optional[bool] = None
    accesses_customer_information_over_internet: Optional[bool] = None
    administrative_accounts_secured: bool = False
    open_vulnerabilities: Tuple[OpenVulnerability, ...] = ()
    has_written_security_standards: bool = False
    conforms_to_security_standards: bool = False
    nonconformity_controls_in_place: bool = False
    network_perimeter_controls_implemented: bool = False
    malware_protection_implemented: bool = False
    malware_protection_unavailable_justification: Optional[str] = None
    mfa_on_administrative_accounts: bool = False
    mfa_on_customer_information_accounts: bool = False

    def __post_init__(self) -> None:
        for field_name in ("system_id", "system_name", "asset_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
        if not isinstance(self.open_vulnerabilities, tuple):
            raise TypeError(
                "open_vulnerabilities must be a tuple so the audited asset cannot be "
                f"mutated after the audit, got {type(self.open_vulnerabilities).__name__}"
            )
        for vulnerability in self.open_vulnerabilities:
            if not isinstance(vulnerability, OpenVulnerability):
                raise TypeError(
                    "open_vulnerabilities must contain OpenVulnerability instances, "
                    f"got {type(vulnerability).__name__}"
                )
        seen: List[str] = [v.vulnerability_id for v in self.open_vulnerabilities]
        if len(set(seen)) != len(seen):
            raise ValueError("open_vulnerabilities contains duplicate vulnerability_id values")


@dataclass(frozen=True)
class MASCyberHygieneBreach:
    """One failed requirement, pinned to the Notice paragraph it comes from."""

    requirement: MASCyberHygieneRequirement
    notice_paragraph: str
    detail: str
    remediation: str


@dataclass(frozen=True)
class MASCyberHygieneAuditReport:
    """Outcome of auditing one asset.

    Every requirement of the Notice is mandatory, so `is_compliant` is the only
    figure with regulatory meaning. `remediation_progress_pct` is an internal
    tracking metric over the requirements that actually applied to this asset;
    it is **not** a compliance grade, and 83% is not "mostly compliant".
    """

    system_id: str
    system_name: str
    entity_notice: str
    is_compliant: bool
    status: str
    breaches: Tuple[MASCyberHygieneBreach, ...]
    failed_requirements: Tuple[MASCyberHygieneRequirement, ...]
    applicable_requirements: Tuple[MASCyberHygieneRequirement, ...]
    not_applicable_requirements: Tuple[MASCyberHygieneRequirement, ...]
    warnings: Tuple[str, ...]
    mandatory_remediations: Tuple[str, ...]
    remediation_progress_pct: float
    audit_notes: str


class SingaporeMASCyberHygieneEngine:
    """Stateless audit of a trading system asset against the Notice on Cyber Hygiene.

    Every requirement is evaluated on every asset; nothing short-circuits, because
    an asset can breach several requirements at once and remediation needs the
    full list.

    Args:
        patch_policy: The firm's risk-commensurate patching deadlines. Mandatory
            -- the Notice publishes no figure.
        entity_class: Which class of financial institution the audited entity
            belongs to, used to stamp the applicable notice number on reports.
            Must be a key of `CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS`.
    """

    def __init__(
        self,
        patch_policy: PatchRemediationPolicy,
        entity_class: str = "CAPITAL_MARKETS",
    ) -> None:
        if not isinstance(patch_policy, PatchRemediationPolicy):
            raise TypeError(
                "patch_policy must be a PatchRemediationPolicy; the Notice fixes no "
                "patching deadline, so the firm must supply its own."
            )
        if entity_class not in CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS:
            raise ValueError(
                f"entity_class must be one of "
                f"{tuple(CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS)}, got {entity_class!r}"
            )
        self.patch_policy = patch_policy
        self.entity_class = entity_class
        self.entity_notice = CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS[entity_class]

    def audit_trading_asset(self, asset: TradingSystemAsset) -> MASCyberHygieneAuditReport:
        """Audit one asset against all six requirements of the Notice."""
        if not isinstance(asset, TradingSystemAsset):
            raise TypeError(f"asset must be a TradingSystemAsset, got {type(asset).__name__}")

        breaches: List[MASCyberHygieneBreach] = []
        warnings: List[str] = []
        not_applicable: List[MASCyberHygieneRequirement] = []

        self._check_administrative_accounts(asset, breaches)
        self._check_security_patches(asset, breaches, warnings)
        self._check_security_standards(asset, breaches, warnings)
        self._check_network_perimeter(asset, breaches)
        self._check_malware_protection(asset, breaches, warnings)
        self._check_multi_factor_authentication(asset, breaches, warnings, not_applicable)

        return self._build_report(asset, breaches, warnings, not_applicable)

    def audit_estate(
        self, assets: Tuple[TradingSystemAsset, ...]
    ) -> Tuple[MASCyberHygieneAuditReport, ...]:
        """Audit an inventory of assets, one report each, in the order supplied.

        The Notice binds every system, so an estate is compliant only if every
        report is. This helper does not aggregate that judgement for the caller;
        it deliberately returns the individual reports so that a breach on one
        host cannot be averaged away against clean hosts.
        """
        return tuple(self.audit_trading_asset(asset) for asset in assets)

    # -- Individual requirements -------------------------------------------------

    def _check_administrative_accounts(
        self, asset: TradingSystemAsset, breaches: List[MASCyberHygieneBreach]
    ) -> None:
        """Para 4.1 -- administrative accounts secured against unauthorised use."""
        if not asset.administrative_accounts_secured:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.ADMIN_ACCOUNT_SECURITY,
                    notice_paragraph="4.1",
                    detail=(
                        "Administrative accounts on this asset are not attested as secured "
                        "against unauthorised access or use."
                    ),
                    remediation=(
                        "Grant administrative accounts on a need-to-use basis, remove or "
                        "disable those no longer required, and review the remaining grants "
                        "periodically."
                    ),
                )
            )

    def _check_security_patches(
        self,
        asset: TradingSystemAsset,
        breaches: List[MASCyberHygieneBreach],
        warnings: List[str],
    ) -> None:
        """Para 4.2 -- (a) risk-commensurate patching, (b) controls where no patch exists."""
        overdue: List[str] = []
        unmitigated: List[str] = []

        for vulnerability in asset.open_vulnerabilities:
            if vulnerability.days_since_patch_released is None:
                # 4.2(b): no patch available -- controls must reduce the risk.
                if vulnerability.compensating_controls_in_place:
                    warnings.append(
                        f"[4.2(b)] {vulnerability.vulnerability_id} ({vulnerability.severity}): "
                        "no patch available; compensating controls recorded. Re-test once a "
                        "patch is released."
                    )
                else:
                    unmitigated.append(
                        f"{vulnerability.vulnerability_id} ({vulnerability.severity})"
                    )
                continue

            # 4.2(a): a patch exists, so the firm's risk-commensurate deadline runs.
            # The deadline is inclusive -- "within N days" is met at exactly N.
            deadline = self.patch_policy.max_days_for(vulnerability.severity)
            if vulnerability.days_since_patch_released > deadline:
                overdue.append(
                    f"{vulnerability.vulnerability_id} ({vulnerability.severity}): "
                    f"{vulnerability.days_since_patch_released}d open vs {deadline}d policy"
                )
                if vulnerability.compensating_controls_in_place:
                    warnings.append(
                        f"[4.2(a)] {vulnerability.vulnerability_id}: compensating controls are "
                        "recorded, but para 4.2(b) applies only where no patch is available. "
                        "A patch exists and is overdue; this remains a breach."
                    )

        if overdue:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT,
                    notice_paragraph="4.2(a)",
                    detail=(
                        "Available security patches unapplied beyond the firm's "
                        f"risk-commensurate deadline: {'; '.join(overdue)}."
                    ),
                    remediation=(
                        "Apply the outstanding security patches, or re-derive the firm's "
                        "deadline for that severity if the current figure is not commensurate "
                        "with the risk the vulnerability poses."
                    ),
                )
            )
        if unmitigated:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT,
                    notice_paragraph="4.2(b)",
                    detail=(
                        "Vulnerabilities with no available patch and no compensating "
                        f"controls: {'; '.join(unmitigated)}."
                    ),
                    remediation=(
                        "Institute controls to reduce the risk posed by each unpatchable "
                        "vulnerability -- network isolation, compensating detection, or "
                        "withdrawal of the affected component from the trading path."
                    ),
                )
            )

    def _check_security_standards(
        self,
        asset: TradingSystemAsset,
        breaches: List[MASCyberHygieneBreach],
        warnings: List[str],
    ) -> None:
        """Para 4.3 -- written standards, conformance, and controls where non-conforming."""
        if not asset.has_written_security_standards:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.BASELINE_SECURITY_STANDARDS,
                    notice_paragraph="4.3(a)",
                    detail=(
                        "No written set of security standards exists for this asset, so "
                        "conformance under 4.3(b) cannot be evaluated."
                    ),
                    remediation=(
                        "Author a written set of security standards covering this asset. "
                        "CIS Benchmarks are a common basis; the Notice mandates that written "
                        "standards exist, not any particular benchmark."
                    ),
                )
            )
            return

        if asset.conforms_to_security_standards:
            return

        if asset.nonconformity_controls_in_place:
            warnings.append(
                "[4.3(c)] Asset does not conform to the written security standards; "
                "controls to reduce the risk of that non-conformity are recorded. Review "
                "the exception on the firm's standing cycle."
            )
        else:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.BASELINE_SECURITY_STANDARDS,
                    notice_paragraph="4.3(b)",
                    detail=(
                        "Asset does not conform to the written security standards and no "
                        "controls are instituted to reduce the risk of that non-conformity."
                    ),
                    remediation=(
                        "Bring the asset into conformance, or institute and document "
                        "controls reducing the risk posed by the non-conformity."
                    ),
                )
            )

    def _check_network_perimeter(
        self, asset: TradingSystemAsset, breaches: List[MASCyberHygieneBreach]
    ) -> None:
        """Para 4.4 -- perimeter controls restricting all unauthorised traffic."""
        if not asset.network_perimeter_controls_implemented:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.NETWORK_PERIMETER_DEFENSE,
                    notice_paragraph="4.4",
                    detail=(
                        "No controls attested at the network perimeter serving this asset."
                    ),
                    remediation=(
                        "Implement perimeter controls restricting all unauthorised network "
                        "traffic to and from the asset, including traffic reaching it via "
                        "third-party or overseas-hosted networks."
                    ),
                )
            )

    def _check_malware_protection(
        self,
        asset: TradingSystemAsset,
        breaches: List[MASCyberHygieneBreach],
        warnings: List[str],
    ) -> None:
        """Para 4.5 -- malware protection where such measures can be implemented."""
        if asset.malware_protection_implemented:
            return

        justification = asset.malware_protection_unavailable_justification
        if justification and justification.strip():
            warnings.append(
                "[4.5] No malware protection implemented; the 'where such measures are "
                f"available and can be implemented' carve-out is claimed: {justification.strip()}. "
                "Re-assess whenever the platform or its tooling changes."
            )
            return

        breaches.append(
            MASCyberHygieneBreach(
                requirement=MASCyberHygieneRequirement.ANTI_MALWARE_PROTECTION,
                notice_paragraph="4.5",
                detail=(
                    "No malware protection measure implemented and no justification recorded "
                    "that such measures are unavailable or cannot be implemented."
                ),
                remediation=(
                    "Implement one or more malware protection measures on the asset, or "
                    "record why none can be implemented on this platform."
                ),
            )
        )

    def _check_multi_factor_authentication(
        self,
        asset: TradingSystemAsset,
        breaches: List[MASCyberHygieneBreach],
        warnings: List[str],
        not_applicable: List[MASCyberHygieneRequirement],
    ) -> None:
        """Para 4.6 -- (a) admin accounts on critical systems, (b) internet customer-information accounts."""
        # Unknown scope resolves conservatively toward the requirement applying,
        # so an absent field can never make a breaching asset look compliant.
        is_critical = True if asset.is_critical_system is None else asset.is_critical_system
        if asset.is_critical_system is None:
            warnings.append(
                "[4.6(a)] is_critical_system not supplied; audited conservatively as a "
                "critical system. Determine criticality and re-run."
            )

        internet_customer_info = (
            True
            if asset.accesses_customer_information_over_internet is None
            else asset.accesses_customer_information_over_internet
        )
        if asset.accesses_customer_information_over_internet is None:
            warnings.append(
                "[4.6(b)] accesses_customer_information_over_internet not supplied; audited "
                "conservatively as in scope."
            )

        if not is_critical and not internet_customer_info:
            not_applicable.append(MASCyberHygieneRequirement.MULTI_FACTOR_AUTH)
            return

        if is_critical and not asset.mfa_on_administrative_accounts:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.MULTI_FACTOR_AUTH,
                    notice_paragraph="4.6(a)",
                    detail=(
                        "Asset is a critical system but multi-factor authentication is not "
                        "implemented for its administrative accounts."
                    ),
                    remediation=(
                        "Enforce multi-factor authentication on every administrative account "
                        "for this critical system's operating system, database, application, "
                        "security appliance and network device layers."
                    ),
                )
            )

        if internet_customer_info and not asset.mfa_on_customer_information_accounts:
            breaches.append(
                MASCyberHygieneBreach(
                    requirement=MASCyberHygieneRequirement.MULTI_FACTOR_AUTH,
                    notice_paragraph="4.6(b)",
                    detail=(
                        "Asset is used to access customer information through the internet "
                        "but multi-factor authentication is not implemented for all accounts "
                        "on it."
                    ),
                    remediation=(
                        "Enforce multi-factor authentication on ALL accounts on this system, "
                        "not only administrative ones -- limb 4.6(b) is not restricted to "
                        "administrators."
                    ),
                )
            )

    # -- Report assembly ---------------------------------------------------------

    def _build_report(
        self,
        asset: TradingSystemAsset,
        breaches: List[MASCyberHygieneBreach],
        warnings: List[str],
        not_applicable: List[MASCyberHygieneRequirement],
    ) -> MASCyberHygieneAuditReport:
        applicable = tuple(
            requirement
            for requirement in MASCyberHygieneRequirement
            if requirement not in not_applicable
        )
        # Preserve paragraph order and de-duplicate: para 4.2 and 4.6 can each
        # raise two breaches, but they are one failed requirement.
        failed = tuple(
            requirement for requirement in applicable
            if any(breach.requirement is requirement for breach in breaches)
        )
        remediations = tuple(
            dict.fromkeys(breach.remediation for breach in breaches)
        )

        is_compliant = not breaches
        passed = len(applicable) - len(failed)
        progress_pct = round(passed / len(applicable) * 100.0, 2) if applicable else 100.0

        status = STATUS_COMPLIANT if is_compliant else STATUS_BREACH
        notes = (
            f"MAS Notice on Cyber Hygiene ({self.entity_notice}) [{status}] "
            f"{asset.system_id}: {passed}/{len(applicable)} applicable requirements met, "
            f"failed = {[requirement.value for requirement in failed]}, "
            f"breached paragraphs = {[breach.notice_paragraph for breach in breaches]}."
        )
        if is_compliant:
            logger.info(notes)
        else:
            logger.warning(notes)

        return MASCyberHygieneAuditReport(
            system_id=asset.system_id,
            system_name=asset.system_name,
            entity_notice=self.entity_notice,
            is_compliant=is_compliant,
            status=status,
            breaches=tuple(breaches),
            failed_requirements=failed,
            applicable_requirements=applicable,
            not_applicable_requirements=tuple(not_applicable),
            warnings=tuple(warnings),
            mandatory_remediations=remediations,
            remediation_progress_pct=progress_pct,
            audit_notes=notes,
        )

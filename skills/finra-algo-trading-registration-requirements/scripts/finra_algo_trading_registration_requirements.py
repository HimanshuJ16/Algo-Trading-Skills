"""FINRA Rule 1220(b)(4) Securities Trader registration gate for algo development.

Decides whether a change to an automated trading system triggers the Securities
Trader registration requirement, and whether the people who authored and
supervised that change actually hold it.

Regulatory anchors (full citations in ``references/standards.md``):

* **FINRA Rule 1220(b)(4)(A)(iii)** requires each associated person of a member
  who is "primarily responsible for the design, development or significant
  modification of an algorithmic trading strategy relating to equity, preferred
  or convertible debt securities", or who is "responsible for the day-to-day
  supervision or direction of such activities", to register as a Securities
  Trader. Rule 1220(b)(4)(B) requires the SIE and the Securities Trader
  (Series 57) examination for persons registering on or after 1 Oct 2018.
* **Regulatory Notice 16-21** (effective 30 Jan 2017, then NASD Rule 1032(f))
  supplies the operative definitions used below: an "algorithmic trading
  strategy" is an automated system that generates or routes orders (including
  order-related messages such as cancellations) but *not* one that "solely
  routes orders, in their entirety, to a market center"; covered systems act
  "in any equity security (including options), preferred security or
  convertible debt security, whether sent to an exchange or handled over the
  counter"; a "significant modification" is "any change to the code of the
  algorithm that impacts the logic and functioning of the trading strategy".
* **FINRA Rule 1220(a)(7)** -- a principal supervising those trading activities
  registers as a Securities Trader Principal (Securities Trader registration
  *plus* the General Securities Principal examination, Series 24). Notice 16-21
  accepts assignment of a lead developer to a Securities Trader **or** a
  Securities Trader Principal for FINRA Rule 3110(a)(5) purposes.
* **FINRA Rule 1240(a)** -- a registered person who misses the annual Regulatory
  Element becomes CE inactive and "shall cease all activities as a registered
  person". A Series 57 that is CE inactive does not satisfy this gate.

Scope discipline is the point of this module. The registration requirement is
narrow: it is limited to associated persons of a FINRA member, to four security
types, to systems that actually generate or route orders, and to persons
*primarily* responsible. Applying it to a futures or FX algorithm, or to a junior
contributor, is regulatory misinformation in the other direction. An out-of-scope
change therefore returns ``OUT_OF_SCOPE_RULE_1220B4`` -- never
``COMPLIANCE_APPROVED`` -- because Rule 1220(b)(4) not applying says nothing about
the firm's Rule 3110 supervision or its Notice 15-09 change-management duties.

This module is a decision and evidence engine. It reads a *snapshot* of
registration status; it does not query CRD, and it cannot tell you whether that
snapshot is stale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Security types (Rule 1220(b)(4)(A)(iii) product scope) -----------------
SECURITY_EQUITY = "EQUITY"
SECURITY_EQUITY_OPTION = "EQUITY_OPTION"
SECURITY_PREFERRED = "PREFERRED"
SECURITY_CONVERTIBLE_DEBT = "CONVERTIBLE_DEBT"

#: The rule reaches these and only these. Notice 16-21 covers "any equity
#: security (including options), preferred security or convertible debt
#: security, whether sent to an exchange or handled over the counter".
COVERED_SECURITY_TYPES: FrozenSet[str] = frozenset(
    {
        SECURITY_EQUITY,
        SECURITY_EQUITY_OPTION,
        SECURITY_PREFERRED,
        SECURITY_CONVERTIBLE_DEBT,
    }
)

SECURITY_CORPORATE_DEBT = "CORPORATE_DEBT"
SECURITY_MUNICIPAL = "MUNICIPAL"
SECURITY_TREASURY = "TREASURY"
SECURITY_FUTURE = "FUTURE"
SECURITY_FUTURES_OPTION = "FUTURES_OPTION"
SECURITY_FX = "FX"
SECURITY_CRYPTO = "CRYPTO"

#: Explicitly enumerated non-covered types. Rule 1220(b)(4) does not reach them;
#: other regimes may (CFTC/NFA for futures, MSRB for municipal securities).
NON_COVERED_SECURITY_TYPES: FrozenSet[str] = frozenset(
    {
        SECURITY_CORPORATE_DEBT,
        SECURITY_MUNICIPAL,
        SECURITY_TREASURY,
        SECURITY_FUTURE,
        SECURITY_FUTURES_OPTION,
        SECURITY_FX,
        SECURITY_CRYPTO,
    }
)
KNOWN_SECURITY_TYPES: FrozenSet[str] = COVERED_SECURITY_TYPES | NON_COVERED_SECURITY_TYPES

# --- What the system does (definition of "algorithmic trading strategy") ----
#: Generates orders, routes them with discretion, or emits order-related
#: messages (Notice 16-21: parent/child orders, slicing, pricing, cancellations).
SYSTEM_GENERATES_OR_ROUTES_ORDERS = "GENERATES_OR_ROUTES_ORDERS"
#: "a standard order router that routes retail orders in their entirety to a
#: particular market center for handling and execution is not covered".
SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS = "SOLELY_ROUTES_ENTIRE_ORDERS"
#: Generates trading ideas or investment allocations but is not equipped to emit
#: orders or order-related messages into the market.
SYSTEM_IDEA_GENERATION_ONLY = "IDEA_GENERATION_ONLY"

KNOWN_SYSTEM_BEHAVIORS: FrozenSet[str] = frozenset(
    {
        SYSTEM_GENERATES_OR_ROUTES_ORDERS,
        SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS,
        SYSTEM_IDEA_GENERATION_ONLY,
    }
)

# --- What the person did ----------------------------------------------------
ACTIVITY_DESIGN = "DESIGN"
ACTIVITY_DEVELOPMENT = "DEVELOPMENT"
ACTIVITY_SIGNIFICANT_MODIFICATION = "SIGNIFICANT_MODIFICATION"
#: Directing a third party's design/development, or directing a third party to
#: significantly modify an algorithm (Notice 16-21, "Third-Party Algorithms").
ACTIVITY_THIRD_PARTY_DIRECTION = "THIRD_PARTY_DIRECTION"
#: "the associated person responsible for monitoring or reviewing the
#: performance of the algorithm must be a Securities Trader" -- true even for an
#: unmodified off-the-shelf algorithm.
ACTIVITY_PERFORMANCE_MONITORING = "PERFORMANCE_MONITORING"
ACTIVITY_DAY_TO_DAY_SUPERVISION = "DAY_TO_DAY_SUPERVISION"

#: Activities that trigger registration when performed by the person *primarily*
#: responsible for them.
REGISTRABLE_ACTIVITIES: FrozenSet[str] = frozenset(
    {
        ACTIVITY_DESIGN,
        ACTIVITY_DEVELOPMENT,
        ACTIVITY_SIGNIFICANT_MODIFICATION,
        ACTIVITY_THIRD_PARTY_DIRECTION,
        ACTIVITY_PERFORMANCE_MONITORING,
        ACTIVITY_DAY_TO_DAY_SUPERVISION,
    }
)

ACTIVITY_MINOR_MODIFICATION = "MINOR_MODIFICATION"
#: Notice 16-21 endnote 4: integrating the algorithm into the firm's
#: technological infrastructure and testing linkages "would not be required to
#: be performed by a Securities Trader".
ACTIVITY_INFRASTRUCTURE_INTEGRATION = "INFRASTRUCTURE_INTEGRATION"
ACTIVITY_TESTING_LINKAGES = "TESTING_LINKAGES"

NON_REGISTRABLE_ACTIVITIES: FrozenSet[str] = frozenset(
    {
        ACTIVITY_MINOR_MODIFICATION,
        ACTIVITY_INFRASTRUCTURE_INTEGRATION,
        ACTIVITY_TESTING_LINKAGES,
    }
)
KNOWN_ACTIVITIES: FrozenSet[str] = REGISTRABLE_ACTIVITIES | NON_REGISTRABLE_ACTIVITIES

# --- Gate outcomes ----------------------------------------------------------
GATE_APPROVED = "COMPLIANCE_APPROVED"
GATE_BLOCKED = "REGISTRATION_VIOLATION_BLOCKED"
#: Rule 1220(b)(4) does not reach this change. Deliberately distinct from
#: approval: the firm's Rule 3110 supervision and Notice 15-09 change management
#: still apply, and another regime may cover the instrument.
GATE_OUT_OF_SCOPE = "OUT_OF_SCOPE_RULE_1220B4"

# --- Scope reason codes -----------------------------------------------------
SCOPE_APPLICABLE = "RULE_1220B4_APPLICABLE"
SCOPE_OUT_NOT_FINRA_MEMBER = "OUT_OF_SCOPE_NOT_FINRA_MEMBER"
SCOPE_OUT_SECURITY_TYPE = "OUT_OF_SCOPE_SECURITY_TYPE"
SCOPE_OUT_NOT_ALGO_STRATEGY = "OUT_OF_SCOPE_NOT_ALGORITHMIC_TRADING_STRATEGY"
SCOPE_OUT_ACTIVITY = "OUT_OF_SCOPE_ACTIVITY_NOT_REGISTRABLE"
SCOPE_OUT_NOT_PRIMARILY_RESPONSIBLE = "OUT_OF_SCOPE_NOT_PRIMARILY_RESPONSIBLE"

# --- Violation codes --------------------------------------------------------
VIOLATION_AUTHOR_UNKNOWN = "AUTHOR_NOT_IN_REGISTRY"
VIOLATION_AUTHOR_NO_SERIES_57 = "AUTHOR_NO_ACTIVE_SERIES_57"
VIOLATION_AUTHOR_NO_SIE = "AUTHOR_SIE_NOT_SATISFIED"
VIOLATION_AUTHOR_CE_INACTIVE = "AUTHOR_CE_INACTIVE"
VIOLATION_SUPERVISOR_UNIDENTIFIED = "SUPERVISOR_NOT_IDENTIFIED"
VIOLATION_SUPERVISOR_UNKNOWN = "SUPERVISOR_NOT_IN_REGISTRY"
VIOLATION_SUPERVISOR_NO_SERIES_57 = "SUPERVISOR_NO_ACTIVE_SERIES_57"
VIOLATION_SUPERVISOR_NO_SIE = "SUPERVISOR_SIE_NOT_SATISFIED"
VIOLATION_SUPERVISOR_CE_INACTIVE = "SUPERVISOR_CE_INACTIVE"
VIOLATION_SELF_APPROVAL = "SELF_APPROVAL"

# --- Supervisor registration basis -----------------------------------------
BASIS_SECURITIES_TRADER_PRINCIPAL = "SECURITIES_TRADER_PRINCIPAL"
BASIS_SECURITIES_TRADER = "SECURITIES_TRADER"
BASIS_NOT_QUALIFIED = "NOT_QUALIFIED"
BASIS_NOT_IDENTIFIED = "NOT_IDENTIFIED"

RULE_CITATION = "FINRA Rule 1220(b)(4)(A)(iii); Regulatory Notice 16-21"


def _norm(value: Optional[str]) -> str:
    """Normalise an identifier for comparison: stripped and case-folded."""
    return (value or "").strip().casefold()


def _utc_iso(moment: datetime) -> str:
    """Render an instant as a UTC ISO-8601 string.

    A naive datetime is treated as already-UTC rather than as local time: an
    audit timestamp silently shifted by the host's timezone is worse than one
    that is explicit about its assumption.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc).isoformat()
    return moment.astimezone(timezone.utc).isoformat()


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_token(value: str, allowed: FrozenSet[str], field_name: str) -> str:
    token = _require_text(value, field_name).upper()
    if token not in allowed:
        raise ValueError(
            f"{field_name} {value!r} is not a recognised value. "
            f"Map it explicitly to one of: {sorted(allowed)}"
        )
    return token


@dataclass(frozen=True)
class DeveloperCredentials:
    """A snapshot of one associated person's registration status.

    The booleans are a point-in-time copy of CRD / FINRA Gateway state, not a
    live query. ``is_series_57_active`` must mean *registration is currently
    effective*: a Securities Trader registration that lapsed for two or more
    years requires requalification (FINRA Rule 1210.08) and is not "active".
    """

    personnel_id: str
    name: str
    role_title: str  # e.g. 'QUANT_DEVELOPER', 'TRADING_SYSTEMS_ENGINEER'
    is_series_57_active: bool
    is_sie_active: bool
    crd_number: Optional[str] = None
    #: FINRA Rule 1240(a)(3): a person who fails to complete the annual
    #: Regulatory Element is CE inactive and must "cease all activities as a
    #: registered person". Active Series 57 + CE inactive does not qualify.
    is_ce_inactive: bool = False
    #: Rule 1220(b)(4)(B): a person registered as a Securities Trader before
    #: 1 Oct 2018 who maintained that registration is considered to have passed
    #: the SIE. Such a person legitimately has no SIE exam record in CRD.
    is_sie_grandfathered: bool = False
    #: Series 24. Combined with Securities Trader registration this makes the
    #: person a Securities Trader Principal under Rule 1220(a)(7).
    is_general_securities_principal: bool = False

    def __post_init__(self) -> None:
        _require_text(self.personnel_id, "personnel_id")
        _require_text(self.name, "name")

    @property
    def sie_satisfied(self) -> bool:
        """SIE met by examination or by pre-1 Oct 2018 grandfathering."""
        return bool(self.is_sie_active or self.is_sie_grandfathered)

    @property
    def is_securities_trader(self) -> bool:
        """Currently able to act in a capacity requiring Securities Trader registration."""
        return bool(self.is_series_57_active and self.sie_satisfied and not self.is_ce_inactive)

    @property
    def is_securities_trader_principal(self) -> bool:
        """Securities Trader registration plus the General Securities Principal exam."""
        return bool(self.is_securities_trader and self.is_general_securities_principal)


@dataclass(frozen=True)
class AlgoCodeCommitRequest:
    """One code change submitted to the deployment gate.

    ``security_type``, ``system_behavior`` and ``author_activity`` are the fields
    that actually decide scope. ``is_significant_modification`` and
    ``modifies_order_routing_logic`` are retained for backward compatibility and
    are consulted only to derive the newer fields when those are left unset. Note
    that the legacy pair cannot express initial *design* or *development* of a new
    algorithm, which is squarely in scope under the rule -- classifying from
    legacy flags alone emits a warning for that reason.
    """

    commit_id: str
    algorithm_id: str
    algorithm_name: str
    author_id: str
    approving_supervisor_id: str
    is_significant_modification: bool = False  # legacy input, see class docstring
    modifies_order_routing_logic: bool = False  # legacy input, see class docstring
    security_type: Optional[str] = None
    system_behavior: Optional[str] = None
    author_activity: Optional[str] = None
    #: The rule reaches the person *primarily* responsible. A junior developer
    #: working under a lead is not (Notice 16-21, endnote 5).
    author_primarily_responsible: bool = True

    def __post_init__(self) -> None:
        _require_text(self.commit_id, "commit_id")
        _require_text(self.algorithm_id, "algorithm_id")
        _require_text(self.algorithm_name, "algorithm_name")
        _require_text(self.author_id, "author_id")


@dataclass(frozen=True)
class FinraRegistrationAuditReport:
    """Auditable record of one gate decision.

    Retained as a book and record: FINRA Rule 4511(b) sets a six-year default
    where no other period is specified, and Rule 4511(c) requires a format and
    media complying with SEA Rule 17a-4.
    """

    commit_id: str
    algorithm_id: str
    author_id: str
    supervisor_id: str
    is_rule_1220b4_applicable: bool
    author_series_57_valid: bool
    supervisor_series_57_valid: bool
    cicd_gate_status: str  # GATE_APPROVED | GATE_BLOCKED | GATE_OUT_OF_SCOPE
    audit_notes: str
    security_type: str = SECURITY_EQUITY
    system_behavior: str = SYSTEM_GENERATES_OR_ROUTES_ORDERS
    author_activity: str = ACTIVITY_SIGNIFICANT_MODIFICATION
    scope_reason: str = SCOPE_APPLICABLE
    supervisor_registration_basis: str = BASIS_NOT_IDENTIFIED
    violations: Tuple[str, ...] = ()
    rule_citation: str = RULE_CITATION
    decision_timestamp_utc: str = ""
    #: True whenever the changed system is an algorithmic trading strategy,
    #: whether or not the Rule 1220(b)(4) registration prong is triggered. For a
    #: covered security this carries the Notice 15-09 change-management
    #: expectation; for a non-covered instrument treat it as firm policy, since
    #: FINRA guidance does not reach that business.
    requires_change_management_review: bool = False

    @property
    def blocks_deployment(self) -> bool:
        """The single field CI should gate on. Only a violation blocks."""
        return self.cicd_gate_status == GATE_BLOCKED


class FinraAlgoRegistrationEngine:
    """Audits code changes against FINRA Rule 1220(b)(4) Securities Trader registration.

    Args:
        is_finra_member: whether the deploying firm is a FINRA member broker-dealer.
            Rule 1220 applies to associated persons of members only; a non-member
            proprietary trading firm sits entirely outside it.
        require_supervisor_registration: enforce that the approving supervisor is a
            Securities Trader or Securities Trader Principal. Notice 16-21 permits
            either for the person supervising covered activities under Rule
            3110(a)(5); disable only where the assigned Rule 3110(a)(5) supervisor
            is tracked outside this gate.
        block_self_approval: treat author == approving supervisor as a violation.
            This is firm supervisory policy (Rule 3110(a)(5) assignment and the
            Notice 15-09 approval protocol), not a Rule 1220(b)(4) requirement --
            the rule itself contemplates one person designing, coding and
            modifying an algorithm alone.
        clock: injectable UTC clock, for deterministic tests.
    """

    def __init__(
        self,
        *,
        is_finra_member: bool = True,
        require_supervisor_registration: bool = True,
        block_self_approval: bool = True,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.personnel_registry: Dict[str, DeveloperCredentials] = {}
        self.is_finra_member = bool(is_finra_member)
        self.require_supervisor_registration = bool(require_supervisor_registration)
        self.block_self_approval = bool(block_self_approval)
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._audit_trail: List[FinraRegistrationAuditReport] = []

    # -- registry ----------------------------------------------------------
    def register_personnel(self, creds: DeveloperCredentials) -> None:
        """Record or replace one person's registration snapshot."""
        if not isinstance(creds, DeveloperCredentials):
            raise TypeError("creds must be a DeveloperCredentials instance")
        key = _norm(creds.personnel_id)
        if key in self.personnel_registry:
            logger.warning(
                "Replacing existing registration snapshot for personnel_id '%s'",
                creds.personnel_id,
            )
        self.personnel_registry[key] = creds

    def get_personnel(self, personnel_id: str) -> Optional[DeveloperCredentials]:
        """Look up a snapshot; identifier matching ignores case and surrounding space."""
        return self.personnel_registry.get(_norm(personnel_id))

    @property
    def audit_trail(self) -> Tuple[FinraRegistrationAuditReport, ...]:
        """Every decision this engine made, in order. In-memory reference only."""
        return tuple(self._audit_trail)

    # -- scope -------------------------------------------------------------
    def assess_scope(self, req: AlgoCodeCommitRequest) -> Tuple[bool, str, str, str, str]:
        """Classify a change against the Rule 1220(b)(4)(A)(iii) scope tests.

        Returns:
            ``(is_applicable, scope_reason, security_type, system_behavior,
            author_activity)`` -- the three resolved tokens are returned so the
            caller records exactly what was classified, not what was submitted.

        Raises:
            ValueError: if a supplied token is not one this module recognises. An
                unmapped instrument or activity fails loudly rather than silently
                dropping out of scope.
        """
        security_type = _require_token(
            req.security_type if req.security_type is not None else SECURITY_EQUITY,
            KNOWN_SECURITY_TYPES,
            "security_type",
        )

        if req.system_behavior is not None:
            system_behavior = _require_token(
                req.system_behavior, KNOWN_SYSTEM_BEHAVIORS, "system_behavior"
            )
        else:
            system_behavior = (
                SYSTEM_GENERATES_OR_ROUTES_ORDERS
                if req.modifies_order_routing_logic
                else SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS
            )

        if req.author_activity is not None:
            author_activity = _require_token(req.author_activity, KNOWN_ACTIVITIES, "author_activity")
        else:
            author_activity = (
                ACTIVITY_SIGNIFICANT_MODIFICATION
                if req.is_significant_modification
                else ACTIVITY_MINOR_MODIFICATION
            )
            logger.warning(
                "Commit '%s' classified from legacy flags only; these cannot express "
                "initial design or development of a new algorithm. Set author_activity "
                "explicitly.",
                req.commit_id,
            )

        resolved = (security_type, system_behavior, author_activity)
        if not self.is_finra_member:
            return (False, SCOPE_OUT_NOT_FINRA_MEMBER) + resolved
        if security_type not in COVERED_SECURITY_TYPES:
            return (False, SCOPE_OUT_SECURITY_TYPE) + resolved
        if system_behavior != SYSTEM_GENERATES_OR_ROUTES_ORDERS:
            return (False, SCOPE_OUT_NOT_ALGO_STRATEGY) + resolved
        if author_activity not in REGISTRABLE_ACTIVITIES:
            return (False, SCOPE_OUT_ACTIVITY) + resolved
        if not req.author_primarily_responsible:
            return (False, SCOPE_OUT_NOT_PRIMARILY_RESPONSIBLE) + resolved
        return (True, SCOPE_APPLICABLE) + resolved

    # -- qualification -----------------------------------------------------
    @staticmethod
    def _qualification_defects(
        creds: Optional[DeveloperCredentials],
        unknown_code: str,
        no_series_57_code: str,
        no_sie_code: str,
        ce_inactive_code: str,
    ) -> List[str]:
        """List every reason this person is not a usable Securities Trader."""
        if creds is None:
            return [unknown_code]
        defects: List[str] = []
        if not creds.is_series_57_active:
            defects.append(no_series_57_code)
        if not creds.sie_satisfied:
            defects.append(no_sie_code)
        if creds.is_ce_inactive:
            defects.append(ce_inactive_code)
        return defects

    @staticmethod
    def _supervisor_basis(creds: Optional[DeveloperCredentials]) -> str:
        if creds is None:
            return BASIS_NOT_IDENTIFIED
        if creds.is_securities_trader_principal:
            return BASIS_SECURITIES_TRADER_PRINCIPAL
        if creds.is_securities_trader:
            return BASIS_SECURITIES_TRADER
        return BASIS_NOT_QUALIFIED

    # -- audit -------------------------------------------------------------
    def audit_code_commit(self, req: AlgoCodeCommitRequest) -> FinraRegistrationAuditReport:
        """Audit one commit and return the gate decision.

        A change outside the rule returns ``GATE_OUT_OF_SCOPE``, which is *not* an
        all-clear: it means this rule does not reach the change. Gate CI on
        ``report.blocks_deployment``, never on equality with ``GATE_APPROVED``.
        """
        if not isinstance(req, AlgoCodeCommitRequest):
            raise TypeError("req must be an AlgoCodeCommitRequest instance")

        applicable, scope_reason, security_type, system_behavior, author_activity = self.assess_scope(req)

        author = self.get_personnel(req.author_id)
        supervisor_id = (req.approving_supervisor_id or "").strip()
        supervisor = self.get_personnel(supervisor_id) if supervisor_id else None

        author_valid = author is not None and author.is_securities_trader
        supervisor_basis = self._supervisor_basis(supervisor)
        supervisor_valid = supervisor_basis in (
            BASIS_SECURITIES_TRADER,
            BASIS_SECURITIES_TRADER_PRINCIPAL,
        )
        is_algo_strategy = system_behavior == SYSTEM_GENERATES_OR_ROUTES_ORDERS

        if not applicable:
            notes = (
                f"FINRA Rule 1220(b)(4) NOT APPLICABLE [{req.commit_id} - {req.algorithm_name}]: "
                f"{scope_reason} (security_type={security_type}, system_behavior={system_behavior}, "
                f"author_activity={author_activity}). Scope finding, not a compliance clearance -- "
                f"Rule 3110 supervision and Notice 15-09 change management still apply."
            )
            logger.info(notes)
            return self._finalise(
                req,
                supervisor_id=supervisor_id,
                applicable=False,
                author_valid=author_valid,
                supervisor_valid=supervisor_valid,
                supervisor_basis=supervisor_basis,
                status=GATE_OUT_OF_SCOPE,
                notes=notes,
                security_type=security_type,
                system_behavior=system_behavior,
                author_activity=author_activity,
                scope_reason=scope_reason,
                violations=(),
                change_management=is_algo_strategy,
            )

        violations: List[str] = self._qualification_defects(
            author,
            VIOLATION_AUTHOR_UNKNOWN,
            VIOLATION_AUTHOR_NO_SERIES_57,
            VIOLATION_AUTHOR_NO_SIE,
            VIOLATION_AUTHOR_CE_INACTIVE,
        )

        if self.require_supervisor_registration:
            if not supervisor_id:
                violations.append(VIOLATION_SUPERVISOR_UNIDENTIFIED)
            else:
                violations.extend(
                    self._qualification_defects(
                        supervisor,
                        VIOLATION_SUPERVISOR_UNKNOWN,
                        VIOLATION_SUPERVISOR_NO_SERIES_57,
                        VIOLATION_SUPERVISOR_NO_SIE,
                        VIOLATION_SUPERVISOR_CE_INACTIVE,
                    )
                )

        if self.block_self_approval and supervisor_id and _norm(req.author_id) == _norm(supervisor_id):
            violations.append(VIOLATION_SELF_APPROVAL)

        if violations:
            status = GATE_BLOCKED
            notes = (
                f"FINRA Rule 1220(b)(4) VIOLATION [{req.commit_id} - {req.algorithm_name}]: "
                f"{author_activity} of a covered {security_type} algorithmic trading strategy by "
                f"author '{req.author_id}'. Findings: {', '.join(violations)}. "
                f"Deployment REJECTED by compliance gate."
            )
            logger.critical(notes)
        else:
            status = GATE_APPROVED
            notes = (
                f"FINRA Rule 1220(b)(4) PASSED [{req.commit_id} - {req.algorithm_name}]: "
                f"{author_activity} of a covered {security_type} algorithmic trading strategy. "
                f"Author '{req.author_id}' holds active Securities Trader registration; supervisor "
                f"'{supervisor_id or 'N/A'}' basis {supervisor_basis}. Deployment APPROVED."
            )
            logger.info(notes)

        return self._finalise(
            req,
            supervisor_id=supervisor_id,
            applicable=True,
            author_valid=author_valid,
            supervisor_valid=supervisor_valid,
            supervisor_basis=supervisor_basis,
            status=status,
            notes=notes,
            security_type=security_type,
            system_behavior=system_behavior,
            author_activity=author_activity,
            scope_reason=scope_reason,
            violations=tuple(violations),
            change_management=True,
        )

    def _finalise(
        self,
        req: AlgoCodeCommitRequest,
        *,
        supervisor_id: str,
        applicable: bool,
        author_valid: bool,
        supervisor_valid: bool,
        supervisor_basis: str,
        status: str,
        notes: str,
        security_type: str,
        system_behavior: str,
        author_activity: str,
        scope_reason: str,
        violations: Tuple[str, ...],
        change_management: bool,
    ) -> FinraRegistrationAuditReport:
        report = FinraRegistrationAuditReport(
            commit_id=req.commit_id,
            algorithm_id=req.algorithm_id,
            author_id=req.author_id,
            supervisor_id=supervisor_id,
            is_rule_1220b4_applicable=applicable,
            author_series_57_valid=author_valid,
            supervisor_series_57_valid=supervisor_valid,
            cicd_gate_status=status,
            audit_notes=notes,
            security_type=security_type,
            system_behavior=system_behavior,
            author_activity=author_activity,
            scope_reason=scope_reason,
            supervisor_registration_basis=supervisor_basis,
            violations=violations,
            decision_timestamp_utc=_utc_iso(self._clock()),
            requires_change_management_review=change_management,
        )
        self._audit_trail.append(report)
        return report


__all__ = [
    "AlgoCodeCommitRequest",
    "DeveloperCredentials",
    "FinraAlgoRegistrationEngine",
    "FinraRegistrationAuditReport",
    "COVERED_SECURITY_TYPES",
    "NON_COVERED_SECURITY_TYPES",
    "KNOWN_SECURITY_TYPES",
    "REGISTRABLE_ACTIVITIES",
    "NON_REGISTRABLE_ACTIVITIES",
    "KNOWN_ACTIVITIES",
    "KNOWN_SYSTEM_BEHAVIORS",
    "GATE_APPROVED",
    "GATE_BLOCKED",
    "GATE_OUT_OF_SCOPE",
]

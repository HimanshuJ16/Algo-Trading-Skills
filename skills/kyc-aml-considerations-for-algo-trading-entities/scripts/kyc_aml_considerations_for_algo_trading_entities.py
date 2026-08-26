"""
kyc-aml-considerations-for-algo-trading-entities: entity-level KYC/AML onboarding
audit for algorithmic trading funds, proprietary trading firms, and trading
corporates.

What this module is and is not
------------------------------
It is a **structured, auditable decision aid** that turns a documented corporate
ownership and screening file into a reproducible onboarding recommendation with
an explicit finding trail. It is not legal advice, it is not a sanctions
screening system, and it does not connect to any watchlist.

In particular, every ``is_sanctioned`` / ``is_pep`` boolean is an **input**: the
caller must already have run the name against OFAC SDN/Consolidated, EU, UN and
UK HMT data using a real screening tool (see ``sanctions-screening-for-
counterparties-and-instruments``) and recorded the outcome. This engine
evaluates the *policy consequences* of those outcomes; it cannot detect a name
that was never screened.

Who the rule actually binds
---------------------------
The FinCEN CDD Rule (31 CFR 1010.230) binds **covered financial institutions** —
banks, registered broker-dealers, mutual funds, FCMs and introducing brokers in
commodities. An algorithmic trading fund is normally the *legal entity customer*
on the other side of that obligation, not the covered institution. The 2024 rule
that would have made registered investment advisers and exempt reporting
advisers BSA "financial institutions" had its effective date moved from
2026-01-01 to **2028-01-01** by a FinCEN final rule dated 2025-12-31 (published
2026-01-02). So this engine serves two audiences:

* the covered institution running CDD on a trading-entity customer, and
* the trading entity assembling the file it will be asked for.

See ``references/standards.md`` for the citations behind every threshold here.

Determinism
-----------
``audit_entity_compliance`` accepts an ``assessment_date``. It defaults to today
only as a convenience; pass it explicitly for reproducible, auditable output.
The jurisdiction lists carry an ``as_of`` date and the engine raises an advisory
finding once they are older than ``max_list_age_days``, because FATF republishes
them at every plenary (roughly February, June and October).
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Outcome codes
# --------------------------------------------------------------------------
STATUS_APPROVED = "KYC_AML_APPROVED"
STATUS_EDD_REQUIRED = "KYC_AML_EDD_REQUIRED"
STATUS_REJECTED_OFAC_50 = "REJECTED_OFAC_50_PERCENT_RULE"
STATUS_REJECTED_SANCTIONS = "REJECTED_SANCTIONS_MATCH"
STATUS_REJECTED_FATF = "REJECTED_FATF_HIGH_RISK"
STATUS_REJECTED_UNVERIFIED_UBO = "REJECTED_UNVERIFIED_UBO"
STATUS_REJECTED_NO_CONTROL_PERSON = "REJECTED_NO_CONTROL_PERSON"
STATUS_REJECTED_OWNERSHIP_OPACITY = "REJECTED_OWNERSHIP_OPACITY"

#: Deterministic precedence. The first blocking finding present decides
#: ``status``; every other finding is still recorded in ``report.findings``, so a
#: rejection never hides a second problem from the reviewer.
_STATUS_PRECEDENCE: Tuple[str, ...] = (
    STATUS_REJECTED_OFAC_50,
    STATUS_REJECTED_SANCTIONS,
    STATUS_REJECTED_FATF,
    STATUS_REJECTED_UNVERIFIED_UBO,
    STATUS_REJECTED_NO_CONTROL_PERSON,
    STATUS_REJECTED_OWNERSHIP_OPACITY,
)

SEVERITY_BLOCKING = "BLOCKING"
SEVERITY_EDD = "EDD"
SEVERITY_ADVISORY = "ADVISORY"

# --------------------------------------------------------------------------
# PEP categories (FATF Recommendation 12 / FATF glossary)
# --------------------------------------------------------------------------
PEP_NONE = "NONE"
PEP_FOREIGN = "FOREIGN"
PEP_DOMESTIC = "DOMESTIC"
PEP_INTERNATIONAL_ORGANISATION = "INTERNATIONAL_ORGANISATION"
_VALID_PEP_CATEGORIES = frozenset(
    {PEP_NONE, PEP_FOREIGN, PEP_DOMESTIC, PEP_INTERNATIONAL_ORGANISATION}
)

#: OFAC treats an entity owned **in the aggregate, directly or indirectly, 50
#: percent or more** by one or more blocked persons as itself blocked, whether or
#: not it appears on the SDN List (OFAC Revised Guidance, 2014-08-13).
OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT = 50.0

_OWNERSHIP_SUM_TOLERANCE_PCT = 1e-6


class KycAmlValidationError(ValueError):
    """Raised when an entity payload or engine configuration is unusable.

    A KYC/AML file must fail loudly rather than quietly. A blank country, a NaN
    ownership percentage, or a cap table summing above 100% is a data-entry or
    upstream-mapping error, and screening it anyway produces an authoritative
    looking ``KYC_AML_APPROVED`` that was never actually tested.
    """


# --------------------------------------------------------------------------
# Country normalisation
#
# The dangerous failure mode here is not a typo, it is a *namespace mismatch*: an
# onboarding system that emits ISO alpha-2 ("IR") screened against a list holding
# names ("IRAN") clears every sanctioned jurisdiction silently. So the engine
# resolves everything to ISO 3166-1 alpha-2 and raises on anything it cannot
# resolve, rather than treating an unrecognised string as low risk.
# --------------------------------------------------------------------------
_COUNTRY_ALIASES: Dict[str, str] = {
    # FATF call-for-action jurisdictions and their common spellings.
    "IRAN": "IR", "ISLAMIC_REPUBLIC_OF_IRAN": "IR", "IRN": "IR",
    "NORTH_KOREA": "KP", "DPRK": "KP", "PRK": "KP",
    "DEMOCRATIC_PEOPLES_REPUBLIC_OF_KOREA": "KP",
    "KOREA_DEMOCRATIC_PEOPLES_REPUBLIC_OF": "KP",
    "MYANMAR": "MM", "BURMA": "MM", "MMR": "MM",
    # FATF increased-monitoring jurisdictions.
    "ANGOLA": "AO", "BOLIVIA": "BO", "BOSNIA_AND_HERZEGOVINA": "BA",
    "BULGARIA": "BG", "CAMEROON": "CM", "COTE_DIVOIRE": "CI",
    "C_TE_D_IVOIRE": "CI", "IVORY_COAST": "CI",
    "DEMOCRATIC_REPUBLIC_OF_THE_CONGO": "CD", "DR_CONGO": "CD",
    "HAITI": "HT", "IRAQ": "IQ", "KENYA": "KE", "KUWAIT": "KW",
    "LAOS": "LA", "LAO_PDR": "LA", "LEBANON": "LB", "MONACO": "MC",
    "NEPAL": "NP", "PAPUA_NEW_GUINEA": "PG", "SOUTH_SUDAN": "SS",
    "SYRIA": "SY", "VENEZUELA": "VE", "VIETNAM": "VN", "VIET_NAM": "VN",
    "BRITISH_VIRGIN_ISLANDS": "VG", "VIRGIN_ISLANDS_UK": "VG", "BVI": "VG",
    "YEMEN": "YE",
    # Jurisdictions that recur in trading-entity structures.
    "USA": "US", "UNITED_STATES": "US", "UNITED_STATES_OF_AMERICA": "US",
    "UK": "GB", "UNITED_KINGDOM": "GB", "GREAT_BRITAIN": "GB", "GBR": "GB",
    "CAYMAN_ISLANDS": "KY", "CYM": "KY",
    "UAE": "AE", "UNITED_ARAB_EMIRATES": "AE",
    "SINGAPORE": "SG", "SWITZERLAND": "CH", "IRELAND": "IE",
    "LUXEMBOURG": "LU", "GERMANY": "DE", "FRANCE": "FR",
    "NETHERLANDS": "NL", "INDIA": "IN", "JAPAN": "JP", "HONG_KONG": "HK",
    "AUSTRALIA": "AU", "CANADA": "CA", "BERMUDA": "BM", "JERSEY": "JE",
    "GUERNSEY": "GG", "MAURITIUS": "MU", "MALTA": "MT", "PANAMA": "PA",
    "SEYCHELLES": "SC", "BAHAMAS": "BS",
}

_NON_ALNUM_RUN = re.compile(r"[^A-Z0-9]+")
_ALPHA2 = re.compile(r"^[A-Z]{2}$")


def normalize_country(raw: str, *, aliases: Optional[Dict[str, str]] = None) -> str:
    """Resolve a country identifier to its ISO 3166-1 alpha-2 code.

    Accepts an alpha-2 code directly, or any spelling present in the alias table
    (``"Cayman Islands"``, ``"CAYMAN_ISLANDS"``, ``"DPRK"``, ``"Burma"``).

    Args:
        raw: The country identifier as supplied by the onboarding system.
        aliases: Replaces the built-in alias table entirely when supplied.

    Returns:
        The ISO 3166-1 alpha-2 code.

    Raises:
        KycAmlValidationError: if the value is blank or cannot be resolved. This
            is deliberate. Silently treating an unrecognised jurisdiction string
            as "not on any list" is the easiest way to build a screening system
            that clears Iran.

    Limitation: any two-letter token is accepted as an alpha-2 code without
    checking that the code is assigned, because doing so would mean vendoring a
    country table. A system that emits a placeholder such as ``"XX"`` for unknown
    jurisdictions will therefore screen clean against every list. Map placeholders
    to a rejection upstream, or pass an ``aliases`` table that excludes them.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise KycAmlValidationError("Country identifier must be a non-empty string.")

    token = _NON_ALNUM_RUN.sub("_", raw.strip().upper()).strip("_")
    table = _COUNTRY_ALIASES if aliases is None else aliases
    if token in table:
        return table[token]
    if _ALPHA2.match(token):
        return token
    raise KycAmlValidationError(
        f"Unrecognised country identifier {raw!r}. Pass an ISO 3166-1 alpha-2 code "
        f"(e.g. 'KY', 'GB', 'IR') or extend the alias table. Unresolvable "
        f"jurisdictions are rejected rather than assumed low risk."
    )


# --------------------------------------------------------------------------
# Jurisdiction risk lists
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class JurisdictionRiskLists:
    """A dated snapshot of the FATF public-statement jurisdiction lists.

    FATF draws a distinction this engine preserves and the first version of this
    skill collapsed: of the three "Call for Action" jurisdictions, only **Iran
    and the DPRK** carry a call for *counter-measures*. **Myanmar** is subject to
    a call for action requiring *enhanced due diligence proportionate to the
    risk* — FATF has expressly not called for counter-measures against it, and
    asks that humanitarian, NPO and remittance flows not be disrupted.

    Attributes:
        as_of: Date of the FATF statement this snapshot reflects.
        counter_measures: Alpha-2 codes where FATF calls for counter-measures.
        enhanced_due_diligence: Alpha-2 codes subject to a call for action
            requiring EDD but not counter-measures.
        increased_monitoring: Alpha-2 codes on the "grey list".
        source: Human-readable provenance for the audit file.
    """

    as_of: date
    counter_measures: FrozenSet[str]
    enhanced_due_diligence: FrozenSet[str]
    increased_monitoring: FrozenSet[str]
    source: str = ""

    def tier_for(self, alpha2: str) -> str:
        """Return ``"COUNTER_MEASURES"``, ``"EDD"``, ``"MONITORING"`` or ``"NONE"``."""
        if alpha2 in self.counter_measures:
            return "COUNTER_MEASURES"
        if alpha2 in self.enhanced_due_diligence:
            return "EDD"
        if alpha2 in self.increased_monitoring:
            return "MONITORING"
        return "NONE"


#: Snapshot of the FATF statements of 19 June 2026. Replace it at every plenary;
#: it is a starting default, not a maintained feed. The engine raises an advisory
#: finding once this snapshot is older than the engine's ``max_list_age_days``.
FATF_LISTS_2026_06_19 = JurisdictionRiskLists(
    as_of=date(2026, 6, 19),
    counter_measures=frozenset({"IR", "KP"}),
    enhanced_due_diligence=frozenset({"MM"}),
    increased_monitoring=frozenset({
        "AO", "BO", "BA", "BG", "CM", "CI", "CD", "HT", "IQ", "KE", "KW",
        "LA", "LB", "MC", "NP", "PG", "SS", "SY", "VE", "VN", "VG", "YE",
    }),
    source="FATF public statements, 19 June 2026 plenary",
)


# --------------------------------------------------------------------------
# Payload types
# --------------------------------------------------------------------------
@dataclass
class UboRecord:
    """A natural person declared as holding equity in the entity.

    ``is_sanctioned`` and ``is_pep`` are **screening outcomes the caller must
    already have obtained**, not questions the engine answers.

    ``pep_category`` refines ``is_pep`` for FATF Recommendation 12, which imposes
    mandatory measures on *foreign* PEPs and a risk-based approach to domestic
    and international-organisation PEPs. Left blank while ``is_pep`` is True, the
    engine assumes ``FOREIGN`` — the conservative reading — and records that it
    did so.
    """

    name: str
    ownership_pct: float
    is_pep: bool
    is_sanctioned: bool
    is_identity_verified: bool
    pep_category: str = ""


@dataclass
class ControlPerson:
    """The control-prong individual.

    31 CFR 1010.230(d)(2) requires a single individual with significant
    responsibility to control, manage, or direct the entity — a CEO, CFO, COO,
    Managing Member, General Partner, President, Vice President or Treasurer, or
    anyone regularly performing similar functions. This prong is **independent of
    ownership**: it must be satisfied even when nobody reaches 25%, which is
    exactly the widely-held or nominee-layered structure where the ownership
    prong on its own returns nobody at all.

    It carries no ownership field by design. If the control person also holds
    equity, declare that equity as a ``UboRecord`` so it is counted exactly once.
    """

    name: str
    title: str
    is_pep: bool
    is_sanctioned: bool
    is_identity_verified: bool
    pep_category: str = ""


@dataclass
class EntityKycAmlPayload:
    """The documented onboarding file for one legal entity."""

    entity_name: str
    incorporation_country: str
    banking_country: str
    ubos: List[UboRecord]
    has_audited_financials: bool = True
    #: Outcome of screening the *entity's own* name against sanctions data. A
    #: False here asserts that screening was run and returned nothing.
    entity_is_sanctioned: bool = False
    control_person: Optional[ControlPerson] = None
    #: FATF R.12(b)/(c) evidence, required before onboarding a foreign PEP.
    senior_management_approval_obtained: bool = False
    source_of_wealth_documented: bool = False


@dataclass(frozen=True)
class KycAmlFinding:
    """One recorded observation, with the authority it rests on."""

    code: str
    severity: str
    detail: str
    citation: str = ""


@dataclass
class KycAmlAuditReport:
    """The auditable output. Every check runs; nothing short-circuits.

    The first version of this engine returned early on a jurisdiction hit and
    stamped ``has_sanctions_hit=False`` without ever screening — a compliance
    record that affirmatively denied a finding it had not tested. Every field
    below reflects a check that actually ran.
    """

    entity_name: str
    total_ubo_ownership_accounted_pct: float
    unverified_ubos_count: int
    has_sanctions_hit: bool
    has_pep_hit: bool
    is_fatf_blacklisted: bool
    status: str
    audit_notes: str
    findings: List[KycAmlFinding] = field(default_factory=list)
    edd_conditions: List[str] = field(default_factory=list)
    aggregate_sanctioned_ownership_pct: float = 0.0
    unaccounted_ownership_pct: float = 0.0
    is_fatf_increased_monitoring: bool = False
    requires_enhanced_due_diligence: bool = False
    control_person_verified: bool = False
    incorporation_country: str = ""
    banking_country: str = ""
    assessment_date: Optional[date] = None
    screening_lists_as_of: Optional[date] = None

    @property
    def is_approved(self) -> bool:
        """True only for an unconditional approval, never for ``EDD_REQUIRED``."""
        return self.status == STATUS_APPROVED

    @property
    def blocking_findings(self) -> List[KycAmlFinding]:
        """Findings that caused, or would have caused, a rejection."""
        return [f for f in self.findings if f.severity == SEVERITY_BLOCKING]


_NAME_KEY_STRIP = re.compile(r"[^A-Z0-9]+")


def _person_key(name: str) -> str:
    """Naive normalisation key used to aggregate one person's several holdings.

    This is a *deduplication aid*, not entity resolution: it will not merge
    "Jane A. Doe" with "Doe, Jane Anne", nor separate two different people who
    happen to share a name. Production systems need a real identifier (passport,
    national ID, LEI) — see ``references/workflows.md``.
    """
    return _NAME_KEY_STRIP.sub(" ", name.strip().upper()).strip()


class KycAmlEntityComplianceEngine:
    """Entity-level KYC/AML onboarding audit for algorithmic trading entities.

    Evaluates the FinCEN CDD Rule's two beneficial-ownership prongs, the OFAC
    50 Percent Rule, FATF jurisdiction risk tiers, and FATF Recommendation 12
    PEP measures, and returns every finding rather than only the first one.

    Args:
        ubo_ownership_threshold_pct: Ownership prong threshold, applied as
            ``>=``. 25.0 matches 31 CFR 1010.230(d)(1) and EU Regulation
            2024/1624. Under AMLD4/AMLD5, in force until the AMLR applies on
            2027-07-10, the EU test is *more than* 25% — supply a slightly larger
            threshold if you must reproduce that exactly.
        risk_lists: Dated FATF jurisdiction snapshot. Defaults to the 19 June
            2026 statements.
        require_control_person: Enforce the control prong. Default True. Turn it
            off only deliberately, for a regime with no equivalent requirement,
            and record why.
        max_unaccounted_ownership_pct: Residual undeclared ownership that will be
            tolerated. Defaults to ``ubo_ownership_threshold_pct``, which is the
            principled value: if more than one threshold's worth of the cap table
            is unattributed, an undisclosed owner could sit at or above the
            threshold and the "all beneficial owners identified" assertion cannot
            be supported.
        max_list_age_days: Age at which the jurisdiction snapshot raises an
            advisory finding. 180 days spans roughly one missed FATF plenary.
        country_aliases: Replaces the built-in alias table when supplied.

    Raises:
        KycAmlValidationError: on unusable configuration.
    """

    def __init__(
        self,
        ubo_ownership_threshold_pct: float = 25.0,
        risk_lists: JurisdictionRiskLists = FATF_LISTS_2026_06_19,
        *,
        require_control_person: bool = True,
        max_unaccounted_ownership_pct: Optional[float] = None,
        max_list_age_days: int = 180,
        country_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        if not isinstance(ubo_ownership_threshold_pct, (int, float)) or not math.isfinite(
            ubo_ownership_threshold_pct
        ) or not 0.0 < ubo_ownership_threshold_pct <= 100.0:
            raise KycAmlValidationError(
                f"ubo_ownership_threshold_pct must be a finite number in (0, 100]; "
                f"got {ubo_ownership_threshold_pct!r}."
            )
        if max_unaccounted_ownership_pct is None:
            max_unaccounted_ownership_pct = float(ubo_ownership_threshold_pct)
        if not isinstance(max_unaccounted_ownership_pct, (int, float)) or not math.isfinite(
            max_unaccounted_ownership_pct
        ) or not 0.0 <= max_unaccounted_ownership_pct <= 100.0:
            raise KycAmlValidationError(
                f"max_unaccounted_ownership_pct must be a finite number in [0, 100]; "
                f"got {max_unaccounted_ownership_pct!r}."
            )
        if max_list_age_days < 0:
            raise KycAmlValidationError("max_list_age_days must be non-negative.")

        self.ubo_ownership_threshold_pct = float(ubo_ownership_threshold_pct)
        self.risk_lists = risk_lists
        self.require_control_person = require_control_person
        self.max_unaccounted_ownership_pct = float(max_unaccounted_ownership_pct)
        self.max_list_age_days = max_list_age_days
        self.country_aliases = country_aliases

    # -- validation --------------------------------------------------------
    def _resolve_pep_category(
        self, name: str, is_pep: bool, pep_category: str, role: str
    ) -> str:
        """Validate a person's PEP fields and return the effective category."""
        if not isinstance(name, str) or not name.strip():
            raise KycAmlValidationError(f"{role} name must be a non-empty string.")
        category = (pep_category or "").strip().upper()
        if category and category not in _VALID_PEP_CATEGORIES:
            raise KycAmlValidationError(
                f"{role} {name!r}: pep_category {pep_category!r} is not one of "
                f"{sorted(_VALID_PEP_CATEGORIES)}."
            )
        if is_pep and category == PEP_NONE:
            raise KycAmlValidationError(
                f"{role} {name!r}: is_pep=True contradicts pep_category='NONE'."
            )
        if not is_pep and category not in ("", PEP_NONE):
            raise KycAmlValidationError(
                f"{role} {name!r}: pep_category={category!r} contradicts is_pep=False."
            )
        if not is_pep:
            return PEP_NONE
        # An unqualified PEP flag defaults to the category carrying mandatory
        # measures, so an incomplete file cannot quietly downgrade its own risk.
        return category or PEP_FOREIGN

    def _validate_payload(self, payload: EntityKycAmlPayload) -> None:
        if not isinstance(payload.entity_name, str) or not payload.entity_name.strip():
            raise KycAmlValidationError("entity_name must be a non-empty string.")
        if not isinstance(payload.ubos, list):
            raise KycAmlValidationError("ubos must be a list of UboRecord.")

        total = 0.0
        for ubo in payload.ubos:
            self._resolve_pep_category(ubo.name, ubo.is_pep, ubo.pep_category, "UBO")
            pct = ubo.ownership_pct
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
                raise KycAmlValidationError(
                    f"UBO {ubo.name!r}: ownership_pct must be a finite number; got {pct!r}. "
                    f"A NaN compares below every threshold and would pass silently."
                )
            if not 0.0 <= pct <= 100.0:
                raise KycAmlValidationError(
                    f"UBO {ubo.name!r}: ownership_pct {pct} is outside [0, 100]."
                )
            total += float(pct)

        if total > 100.0 + _OWNERSHIP_SUM_TOLERANCE_PCT:
            raise KycAmlValidationError(
                f"Declared UBO ownership sums to {total:.4f}%, which exceeds 100%. "
                f"Resolve the cap table before screening."
            )

        cp = payload.control_person
        if cp is not None:
            self._resolve_pep_category(cp.name, cp.is_pep, cp.pep_category, "Control person")
            if not isinstance(cp.title, str) or not cp.title.strip():
                raise KycAmlValidationError(
                    f"Control person {cp.name!r}: title must be a non-empty string "
                    f"(e.g. 'Chief Executive Officer', 'General Partner')."
                )

    # -- screening subroutines --------------------------------------------
    def _screen_jurisdictions(
        self,
        inc: str,
        bank: str,
        findings: List[KycAmlFinding],
        edd: List[str],
    ) -> Tuple[bool, bool]:
        """Screen both jurisdictions. Returns (call_for_action, increased_monitoring)."""
        call_for_action = False
        monitored = False
        as_of = self.risk_lists.as_of.isoformat()

        for label, code in (("incorporation", inc), ("banking", bank)):
            tier = self.risk_lists.tier_for(code)
            if tier == "COUNTER_MEASURES":
                call_for_action = True
                findings.append(KycAmlFinding(
                    code=STATUS_REJECTED_FATF,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        f"{label.capitalize()} jurisdiction '{code}' is subject to a "
                        f"FATF call for action with counter-measures."
                    ),
                    citation=f"FATF Recommendation 19; FATF call-for-action statement, {as_of}",
                ))
            elif tier == "EDD":
                call_for_action = True
                findings.append(KycAmlFinding(
                    code="FATF_CALL_FOR_ACTION_EDD",
                    severity=SEVERITY_EDD,
                    detail=(
                        f"{label.capitalize()} jurisdiction '{code}' is subject to a "
                        f"FATF call for action requiring enhanced due diligence "
                        f"proportionate to the risk. FATF has not called for "
                        f"counter-measures against it, and asks that humanitarian, NPO "
                        f"and remittance flows not be disrupted."
                    ),
                    citation=f"FATF Recommendation 19; FATF call-for-action statement, {as_of}",
                ))
                edd.append(
                    f"Apply risk-proportionate EDD to the {label} jurisdiction '{code}'."
                )
            elif tier == "MONITORING":
                monitored = True
                findings.append(KycAmlFinding(
                    code="FATF_INCREASED_MONITORING",
                    severity=SEVERITY_EDD,
                    detail=(
                        f"{label.capitalize()} jurisdiction '{code}' is under FATF "
                        f"increased monitoring (grey list)."
                    ),
                    citation=f"FATF 'Jurisdictions under Increased Monitoring', {as_of}",
                ))
        return call_for_action, monitored

    def _screen_persons(
        self,
        payload: EntityKycAmlPayload,
        findings: List[KycAmlFinding],
    ) -> Tuple[bool, bool, bool, float]:
        """Screen every declared person.

        Returns (has_sanctions, has_foreign_pep, has_any_pep, blocked_ownership_pct).
        """
        has_sanctions = False
        has_foreign_pep = False
        has_any_pep = False
        blocked_pct = 0.0

        people: List[Tuple[str, str, bool, bool, str, float]] = [
            ("UBO", u.name, u.is_pep, u.is_sanctioned, u.pep_category, float(u.ownership_pct))
            for u in payload.ubos
        ]
        if payload.control_person is not None:
            cp = payload.control_person
            people.append(
                ("Control person", cp.name, cp.is_pep, cp.is_sanctioned, cp.pep_category, 0.0)
            )

        for role, name, is_pep, is_sanctioned, raw_category, pct in people:
            category = self._resolve_pep_category(name, is_pep, raw_category, role)
            if is_sanctioned:
                has_sanctions = True
                blocked_pct += pct
                findings.append(KycAmlFinding(
                    code=STATUS_REJECTED_SANCTIONS,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        f"{role} {name!r} is a recorded sanctions match "
                        f"({pct:.1f}% of declared equity)."
                    ),
                    citation="OFAC blocking regulations, 31 CFR chapter V",
                ))
            if is_pep:
                has_any_pep = True
                if category == PEP_FOREIGN:
                    has_foreign_pep = True
                    if not (raw_category or "").strip():
                        findings.append(KycAmlFinding(
                            code="PEP_CATEGORY_ASSUMED_FOREIGN",
                            severity=SEVERITY_ADVISORY,
                            detail=(
                                f"{role} {name!r} is flagged as a PEP with no "
                                f"pep_category; treated as FOREIGN, which carries the "
                                f"mandatory measures. Record the actual category."
                            ),
                            citation="FATF Recommendation 12",
                        ))
                else:
                    findings.append(KycAmlFinding(
                        code="PEP_DOMESTIC_OR_IO",
                        severity=SEVERITY_ADVISORY,
                        detail=(
                            f"{role} {name!r} is a {category} PEP. FATF applies a "
                            f"risk-based approach here; escalate to the foreign-PEP "
                            f"measures where the relationship is higher risk."
                        ),
                        citation="FATF Recommendation 12",
                    ))

        if payload.entity_is_sanctioned:
            has_sanctions = True
            findings.append(KycAmlFinding(
                code=STATUS_REJECTED_SANCTIONS,
                severity=SEVERITY_BLOCKING,
                detail=(
                    f"The entity {payload.entity_name!r} is itself a recorded "
                    f"sanctions match."
                ),
                citation="OFAC blocking regulations, 31 CFR chapter V",
            ))

        return has_sanctions, has_foreign_pep, has_any_pep, blocked_pct

    def _screen_ownership_prong(
        self,
        payload: EntityKycAmlPayload,
        findings: List[KycAmlFinding],
    ) -> Tuple[int, float, float]:
        """Aggregate holdings per person, then apply the ownership prong.

        Returns (unverified_count, total_declared_pct, unaccounted_pct).
        """
        aggregated: Dict[str, Tuple[str, float, bool]] = {}
        for ubo in payload.ubos:
            key = _person_key(ubo.name)
            display, pct, verified = aggregated.get(key, (ubo.name, 0.0, True))
            aggregated[key] = (
                display,
                pct + float(ubo.ownership_pct),
                verified and bool(ubo.is_identity_verified),
            )

        total = sum(pct for _, pct, _ in aggregated.values())
        unverified = 0
        for display, pct, verified in sorted(aggregated.values(), key=lambda t: t[0]):
            if pct >= self.ubo_ownership_threshold_pct and not verified:
                unverified += 1
                findings.append(KycAmlFinding(
                    code=STATUS_REJECTED_UNVERIFIED_UBO,
                    severity=SEVERITY_BLOCKING,
                    detail=(
                        f"Beneficial owner {display!r} holds {pct:.1f}% "
                        f"(>= {self.ubo_ownership_threshold_pct}%) without verified "
                        f"identity."
                    ),
                    citation="31 CFR 1010.230(b)(2), (d)(1)",
                ))

        unaccounted = max(0.0, 100.0 - total)
        if unaccounted > self.max_unaccounted_ownership_pct:
            findings.append(KycAmlFinding(
                code=STATUS_REJECTED_OWNERSHIP_OPACITY,
                severity=SEVERITY_BLOCKING,
                detail=(
                    f"Declared ownership accounts for {total:.1f}%, leaving "
                    f"{unaccounted:.1f}% unattributed - more than the "
                    f"{self.max_unaccounted_ownership_pct}% tolerated. An undisclosed "
                    f"holder at or above the {self.ubo_ownership_threshold_pct}% "
                    f"threshold cannot be ruled out, so beneficial ownership is not "
                    f"established."
                ),
                citation=(
                    "31 CFR 1010.230(d)(1) 'directly or indirectly'; "
                    "FATF Recommendation 24"
                ),
            ))
        return unverified, total, unaccounted

    # -- entry point -------------------------------------------------------
    def audit_entity_compliance(
        self,
        payload: EntityKycAmlPayload,
        assessment_date: Optional[date] = None,
    ) -> KycAmlAuditReport:
        """Audit one entity file and return every finding.

        Args:
            payload: The documented entity file.
            assessment_date: Date the audit is deemed to run on. Defaults to
                today; pass it explicitly for reproducible output.

        Returns:
            A ``KycAmlAuditReport`` whose ``status`` is the highest-precedence
            blocking finding, else ``KYC_AML_EDD_REQUIRED`` if EDD conditions are
            outstanding, else ``KYC_AML_APPROVED``.

        Raises:
            KycAmlValidationError: if the payload cannot be screened as given.
        """
        self._validate_payload(payload)
        when = assessment_date or date.today()

        inc = normalize_country(payload.incorporation_country, aliases=self.country_aliases)
        bank = normalize_country(payload.banking_country, aliases=self.country_aliases)

        findings: List[KycAmlFinding] = []
        edd: List[str] = []

        list_age_days = (when - self.risk_lists.as_of).days
        if list_age_days > self.max_list_age_days:
            findings.append(KycAmlFinding(
                code="STALE_JURISDICTION_LISTS",
                severity=SEVERITY_ADVISORY,
                detail=(
                    f"Jurisdiction lists are {list_age_days} days old (as_of "
                    f"{self.risk_lists.as_of.isoformat()}). FATF republishes them at "
                    f"each plenary; refresh before relying on this result."
                ),
                citation=self.risk_lists.source,
            ))

        # Every check runs before any decision is taken, so the report never
        # denies a finding it did not test.
        call_for_action, monitored = self._screen_jurisdictions(inc, bank, findings, edd)
        has_sanctions, has_foreign_pep, has_any_pep, blocked_pct = self._screen_persons(
            payload, findings
        )
        unverified, total, unaccounted = self._screen_ownership_prong(payload, findings)

        # OFAC 50 Percent Rule: aggregate blocked ownership makes the entity
        # itself blocked property — a materially different consequence from
        # declining to onboard.
        if blocked_pct >= OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT:
            findings.insert(0, KycAmlFinding(
                code=STATUS_REJECTED_OFAC_50,
                severity=SEVERITY_BLOCKING,
                detail=(
                    f"Blocked persons hold {blocked_pct:.1f}% in the aggregate "
                    f"(>= {OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT}%). The entity is "
                    f"itself blocked property whether or not it appears on the SDN "
                    f"List: property must be blocked and reported to OFAC, not merely "
                    f"declined."
                ),
                citation=(
                    "OFAC Revised Guidance on Entities Owned by Persons Whose Property "
                    "and Interests in Property Are Blocked, 13 August 2014"
                ),
            ))

        # Control prong — independent of ownership.
        cp = payload.control_person
        control_verified = bool(cp is not None and cp.is_identity_verified)
        if self.require_control_person and not control_verified:
            reason = (
                "no control-prong individual was declared"
                if cp is None
                else f"control person {cp.name!r} has no verified identity"
            )
            findings.append(KycAmlFinding(
                code=STATUS_REJECTED_NO_CONTROL_PERSON,
                severity=SEVERITY_BLOCKING,
                detail=(
                    f"Control prong unsatisfied: {reason}. One individual with "
                    f"significant responsibility to control, manage or direct the "
                    f"entity must be identified and verified even when no owner "
                    f"reaches the {self.ubo_ownership_threshold_pct}% threshold."
                ),
                citation="31 CFR 1010.230(d)(2)",
            ))

        # FATF R.12 measures for foreign PEPs.
        if has_foreign_pep:
            pep_conditions: List[str] = []
            if not payload.senior_management_approval_obtained:
                pep_conditions.append(
                    "Obtain senior management approval for the foreign-PEP relationship."
                )
            if not payload.source_of_wealth_documented:
                pep_conditions.append(
                    "Establish and document source of wealth and source of funds."
                )
            edd.extend(pep_conditions)
            findings.append(KycAmlFinding(
                code="FOREIGN_PEP_EDD",
                severity=SEVERITY_EDD if pep_conditions else SEVERITY_ADVISORY,
                detail=(
                    "A foreign PEP is associated with this entity. FATF requires senior "
                    "management approval, source of wealth and source of funds, and "
                    "enhanced ongoing monitoring. PEP status is a preventive "
                    "classification, not an allegation of criminality, and is not on "
                    "its own a basis for refusing the relationship."
                ),
                citation="FATF Recommendation 12; FATF PEP Guidance (June 2013)",
            ))

        if (call_for_action or monitored) and not payload.source_of_wealth_documented:
            edd.append("Document source of funds for the high-risk jurisdiction exposure.")
        if (call_for_action or monitored or has_foreign_pep) and not payload.has_audited_financials:
            findings.append(KycAmlFinding(
                code="NO_AUDITED_FINANCIALS_UNDER_EDD",
                severity=SEVERITY_ADVISORY,
                detail=(
                    "Enhanced due diligence is engaged and the entity has no audited "
                    "financial statements. Not a regulatory bar; most firms' policies "
                    "treat it as a corroboration gap worth recording."
                ),
                citation="Firm policy - no regulatory basis asserted",
            ))

        edd = sorted(set(edd))
        status = next(
            (s for s in _STATUS_PRECEDENCE
             if any(f.code == s and f.severity == SEVERITY_BLOCKING for f in findings)),
            STATUS_EDD_REQUIRED if edd else STATUS_APPROVED,
        )

        notes = self._compose_notes(payload, status, inc, bank, total, findings, edd)
        if status == STATUS_APPROVED:
            logger.info(notes)
        elif status == STATUS_EDD_REQUIRED:
            logger.warning(notes)
        else:
            logger.critical(notes)

        return KycAmlAuditReport(
            entity_name=payload.entity_name,
            total_ubo_ownership_accounted_pct=total,
            unverified_ubos_count=unverified,
            has_sanctions_hit=has_sanctions,
            has_pep_hit=has_any_pep,
            is_fatf_blacklisted=call_for_action,
            status=status,
            audit_notes=notes,
            findings=findings,
            edd_conditions=edd,
            aggregate_sanctioned_ownership_pct=blocked_pct,
            unaccounted_ownership_pct=unaccounted,
            is_fatf_increased_monitoring=monitored,
            requires_enhanced_due_diligence=bool(edd),
            control_person_verified=control_verified,
            incorporation_country=inc,
            banking_country=bank,
            assessment_date=when,
            screening_lists_as_of=self.risk_lists.as_of,
        )

    @staticmethod
    def _compose_notes(
        payload: EntityKycAmlPayload,
        status: str,
        inc: str,
        bank: str,
        total: float,
        findings: Iterable[KycAmlFinding],
        edd: List[str],
    ) -> str:
        blocking = [f for f in findings if f.severity == SEVERITY_BLOCKING]
        notes = (
            f"KYC/AML {status} [{payload.entity_name}]: jurisdiction {inc}/{bank}, "
            f"declared UBO ownership {total:.1f}%."
        )
        if blocking:
            notes += " Blocking: " + " | ".join(f.detail for f in blocking)
        if edd:
            notes += " Outstanding EDD: " + " | ".join(edd)
        return notes

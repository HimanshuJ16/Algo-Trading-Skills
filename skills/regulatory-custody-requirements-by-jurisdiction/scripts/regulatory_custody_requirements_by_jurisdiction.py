"""
regulatory-custody-requirements-by-jurisdiction: a custody compliance engine that
audits a documented custody arrangement against the requirements of the regime
that actually governs it.

What this module is and is not
------------------------------
It is a **structured, citation-carrying compliance aid**. Every requirement it
evaluates names the instrument it comes from, so a report is auditable rather
than merely assertive. It is not legal advice, and it does not determine legal
status; custody qualification is a conclusion for counsel.

Why regimes, not jurisdictions
------------------------------
"Custody rules in the US" is not a single thing. The Advisers Act custody rule
(17 CFR 275.206(4)-2) governs SEC-registered investment advisers; broker-dealer
customer protection (17 CFR 240.15c3-3) is a different rule entirely. In the EU,
crypto custody is MiCA Article 75 while custody of financial instruments is
MiFID II / AIFMD. In the UK, safeguarding cryptoassets does not even become a
regulated activity until **25 October 2027**. Rules are therefore keyed by
``"<JURISDICTION>:<ASSET_SCOPE>"`` and an unsupported combination is reported as
such instead of being silently answered with the wrong regime's rules.

Three corrections worth stating explicitly
------------------------------------------
Earlier revisions of this skill asserted that the EU and Singapore *mandate*
insurance for custodied digital assets, and that every listed jurisdiction
mandates an unconditional annual custody audit. None of that is supported:

* **MiCA does not mandate custody insurance.** Article 75 (custody and
  administration of crypto-assets) never mentions insurance. Article 67 requires
  *prudential safeguards* of at least the higher of the Annex IV permanent
  minimum capital and one quarter of the previous year's fixed overheads, and
  Article 67(4) allows those safeguards to take the form of own funds **or** an
  insurance policy **or** a comparable guarantee. Insurance is a permitted
  *form* of a capital requirement, not a custody mandate.
* **MAS does not mandate custody insurance either**, nor an independent
  third-party custodian. The finalised DPT requirements are a statutory trust,
  segregation from the provider's own assets, and a supervisory expectation of
  at least 90% cold storage.
* **The Advisers Act surprise examination has exceptions.** Rule 206(4)-2(b)(3)
  (custody solely from fee deduction) and (b)(4) (audited pooled vehicle,
  statements distributed within 120 days) both relieve it. Reporting a violation
  against an adviser that properly relies on one of them is a false positive.

Absence of evidence is not compliance
-------------------------------------
Every evidence attribute is ``Optional[bool]`` and defaults to ``None``. A
mandatory requirement whose evidence is ``None`` is reported as a violation with
severity ``UNEVIDENCED`` -- never silently as satisfied. A compliance engine that
returns "compliant" for a setup it knows nothing about is worse than no engine.

Determinism
-----------
``audit_custody_setup`` accepts ``as_of``. It defaults to today only as a
convenience; pass it explicitly so output is reproducible and the audit trail
records what was known when. It matters: the UK cryptoasset regime is dated.

See ``references/standards.md`` for the full source list.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

ASSET_SCOPE_SECURITIES = "SECURITIES"
ASSET_SCOPE_CRYPTO = "CRYPTO"
_VALID_ASSET_SCOPES = frozenset({ASSET_SCOPE_SECURITIES, ASSET_SCOPE_CRYPTO})

#: A third party that meets the governing regime's institutional test.
CUSTODY_QUALIFIED_CUSTODIAN = "QUALIFIED_CUSTODIAN"
#: A custodian that is the firm itself or a related person of it.
CUSTODY_AFFILIATED_CUSTODIAN = "AFFILIATED_CUSTODIAN"
#: US-only conditional route for crypto assets (2025-09-30 staff no-action letter).
CUSTODY_STATE_CHARTERED_TRUST = "STATE_CHARTERED_TRUST"
#: The firm holds the assets (or the keys) itself.
CUSTODY_SELF_CUSTODY = "SELF_CUSTODY"
#: Assets left in an account at a trading venue.
CUSTODY_EXCHANGE_CUSTODY = "EXCHANGE_CUSTODY"

_VALID_CUSTODY_TYPES = frozenset({
    CUSTODY_QUALIFIED_CUSTODIAN,
    CUSTODY_AFFILIATED_CUSTODIAN,
    CUSTODY_STATE_CHARTERED_TRUST,
    CUSTODY_SELF_CUSTODY,
    CUSTODY_EXCHANGE_CUSTODY,
})

SEVERITY_MANDATORY = "MANDATORY"
SEVERITY_UNEVIDENCED = "UNEVIDENCED"
SEVERITY_ADVISORY = "ADVISORY"

STATUS_COMPLIANT = "CUSTODY_COMPLIANT"
STATUS_VIOLATION = "CUSTODY_VIOLATION"
STATUS_UNKNOWN_JURISDICTION = "UNKNOWN_JURISDICTION"
STATUS_UNSUPPORTED_REGIME = "UNSUPPORTED_REGIME"
#: The regime exists and is made, but has not commenced as of ``as_of``. Findings
#: are a readiness assessment, not a live compliance determination.
STATUS_PRE_COMMENCEMENT = "PRE_COMMENCEMENT_READINESS"

#: MiCA Annex IV permanent minimum capital for Class 2 services, which is the
#: class that includes custody and administration of crypto-assets.
MICA_ANNEX_IV_CLASS_2_EUR = 125_000.0
#: MiCA Art. 67(1)(b): one quarter of the preceding year's fixed overheads.
MICA_FIXED_OVERHEAD_FRACTION = 0.25
#: MAS supervisory expectation for DPT service providers (guidance, not statute).
MAS_COLD_STORAGE_EXPECTATION_PCT = 90.0
#: Commencement of the UK regulated activity of safeguarding qualifying
#: cryptoassets. Before this date CASS 17 does not bite.
UK_CRYPTOASSET_REGIME_COMMENCEMENT = date(2027, 10, 25)


class CustodyRegimeError(ValueError):
    """Raised when a custody setup or engine configuration is invalid.

    A compliance audit must fail loudly on bad input. An unrecognised
    ``custody_type`` is a typo, not a substantive finding: scoring it as an
    unqualified-custodian violation would manufacture a breach out of a spelling
    mistake, so it raises instead.
    """


# --------------------------------------------------------------------------
# Setup under audit
# --------------------------------------------------------------------------

@dataclass
class CustodySetup:
    """A documented custody arrangement.

    Every ``Optional[bool]`` is an assertion the reviewer must be able to
    support with an artefact (charter or licence, custody agreement, trust deed,
    auditor's report). ``None`` means "not evidenced" and is reported as such --
    it is never treated as satisfied.
    """

    jurisdiction: str
    custodian_name: str
    custody_type: str
    is_asset_segregated: Optional[bool]
    #: The regime's periodic independent examination: an Advisers Act surprise
    #: examination in the US, an auditor's client assets report in the UK. The
    #: applicable requirement names which one it means.
    has_annual_audit: Optional[bool]
    has_insurance_coverage: bool = False
    asset_scope: str = ASSET_SCOPE_CRYPTO

    # -- institutional standing
    custodian_is_authorised_in_jurisdiction: Optional[bool] = None
    custodian_is_related_person: bool = False

    # -- MiCA Article 75 operating conditions
    has_documented_custody_policy: Optional[bool] = None
    maintains_client_position_register: Optional[bool] = None

    # -- MiCA Article 67 prudential safeguards, in EUR. The safeguard may be own
    #    funds, a qualifying insurance policy, or a comparable guarantee.
    prudential_safeguard_eur: Optional[float] = None
    fixed_overheads_prior_year_eur: Optional[float] = None

    # -- MAS DPT safeguarding
    holds_client_assets_on_statutory_trust: Optional[bool] = None
    cold_storage_pct: Optional[float] = None

    # -- Advisers Act surprise-examination exceptions
    custody_solely_for_fee_deduction: bool = False
    pooled_vehicle_audited_within_120_days: bool = False
    #: Rule 206(4)-2(a)(6) internal control report, required when the qualified
    #: custodian is the adviser itself or a related person.
    has_internal_control_report: Optional[bool] = None
    #: Conditions of the 2025-09-30 staff no-action letter, verified in full.
    #: See `custody-solution-vendor-due-diligence-checklist`, which models them.
    state_trust_no_action_conditions_verified: Optional[bool] = None


@dataclass
class CustodyViolation:
    """A finding. ``severity`` distinguishes a breach from missing evidence and
    from unmet supervisory guidance."""

    jurisdiction: str
    violation_type: str
    detail: str
    citation: str = ""
    severity: str = SEVERITY_MANDATORY


@dataclass
class JurisdictionalCustodyAuditReport:
    jurisdiction: str
    is_compliant: bool
    violations: List[CustodyViolation]
    status: str
    audit_notes: str
    regime_id: str = ""
    regulator: str = ""
    instrument: str = ""
    asset_scope: str = ""
    #: Unmet guidance, and unmet requirements of a regime not yet in force.
    advisories: List[CustodyViolation] = field(default_factory=list)
    satisfied_requirements: List[str] = field(default_factory=list)
    #: Requirements disapplied by a codified exception, with the exception cited.
    exemptions_applied: List[str] = field(default_factory=list)
    as_of: Optional[date] = None
    scope_note: str = ""


# --------------------------------------------------------------------------
# Requirement model
# --------------------------------------------------------------------------

#: A check returns True (satisfied), False (breached), or None (not evidenced).
CheckFn = Callable[[CustodySetup], Optional[bool]]


@dataclass(frozen=True)
class CustodyRequirement:
    requirement_id: str
    description: str
    citation: str
    check: CheckFn
    mandatory: bool = True
    #: Returns a citation for a codified exception that disapplies the
    #: requirement, or None if no exception is available on these facts.
    exemption: Optional[Callable[[CustodySetup], Optional[str]]] = None
    #: Returns True when the requirement is engaged on these facts at all.
    applies_when: Optional[Callable[[CustodySetup], bool]] = None


def _evidence(attribute: str) -> CheckFn:
    """Check that reads a single tri-state evidence attribute."""

    def _check(setup: CustodySetup) -> Optional[bool]:
        return getattr(setup, attribute)

    _check.__name__ = f"_evidence_{attribute}"
    return _check


def _custodian_type_in(accepted: frozenset) -> CheckFn:
    def _check(setup: CustodySetup) -> Optional[bool]:
        return setup.custody_type in accepted

    _check.__name__ = "_custodian_type_accepted"
    return _check


def _mica_prudential_safeguard(setup: CustodySetup) -> Optional[bool]:
    """MiCA Art. 67(1): safeguards >= max(Annex IV minimum, 1/4 fixed overheads).

    Returns None when the higher-of test cannot be computed, rather than passing
    the setup on the Annex IV floor alone -- a CASP with large fixed overheads can
    clear EUR 125,000 and still be undercapitalised.
    """
    if setup.prudential_safeguard_eur is None:
        return None
    if setup.prudential_safeguard_eur < MICA_ANNEX_IV_CLASS_2_EUR:
        return False
    if setup.fixed_overheads_prior_year_eur is None:
        return None
    overheads_limb = MICA_FIXED_OVERHEAD_FRACTION * setup.fixed_overheads_prior_year_eur
    return setup.prudential_safeguard_eur >= overheads_limb


def _mas_cold_storage(setup: CustodySetup) -> Optional[bool]:
    if setup.cold_storage_pct is None:
        return None
    return setup.cold_storage_pct >= MAS_COLD_STORAGE_EXPECTATION_PCT


def _surprise_examination_exemption(setup: CustodySetup) -> Optional[str]:
    if setup.custody_solely_for_fee_deduction:
        return ("17 CFR 275.206(4)-2(b)(3): custody arises solely from the "
                "authority to deduct advisory fees")
    if setup.pooled_vehicle_audited_within_120_days:
        return ("17 CFR 275.206(4)-2(b)(4): audited pooled investment vehicle "
                "distributing GAAP financial statements within 120 days")
    return None


def _is_related_person_custodian(setup: CustodySetup) -> bool:
    return bool(setup.custodian_is_related_person) or (
        setup.custody_type == CUSTODY_AFFILIATED_CUSTODIAN)


def _is_state_trust_route(setup: CustodySetup) -> bool:
    return setup.custody_type == CUSTODY_STATE_CHARTERED_TRUST


# --------------------------------------------------------------------------
# Regime specifications
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CustodyRuleSpec:
    jurisdiction: str
    regime_id: str
    regulator: str
    instrument: str
    asset_scope: str
    requirements: Tuple[CustodyRequirement, ...]
    #: Commencement date, where the regime is made but not yet in force.
    effective_from: Optional[date] = None
    scope_note: str = ""


_US_ADVISERS_ACT_CUSTODIANS = frozenset({
    CUSTODY_QUALIFIED_CUSTODIAN, CUSTODY_AFFILIATED_CUSTODIAN,
})
_US_CRYPTO_CUSTODIANS = _US_ADVISERS_ACT_CUSTODIANS | {CUSTODY_STATE_CHARTERED_TRUST}

_US_SEGREGATION = CustodyRequirement(
    requirement_id="CLIENT_ASSET_SEGREGATION",
    description=("Client funds and securities held in a separate account in the "
                 "client's name, or in accounts containing only clients' assets "
                 "in the adviser's name as agent or trustee."),
    citation="17 CFR 275.206(4)-2(a)(1)(i)-(ii)",
    check=_evidence("is_asset_segregated"),
)
_US_SURPRISE_EXAM = CustodyRequirement(
    requirement_id="ANNUAL_SURPRISE_EXAMINATION",
    description=("Client assets verified by actual examination at least once "
                 "each calendar year by an independent public accountant, at a "
                 "time chosen by the accountant without prior notice."),
    citation="17 CFR 275.206(4)-2(a)(4)",
    check=_evidence("has_annual_audit"),
    exemption=_surprise_examination_exemption,
)
_US_INTERNAL_CONTROL_REPORT = CustodyRequirement(
    requirement_id="INTERNAL_CONTROL_REPORT",
    description=("Written internal control report from an independent public "
                 "accountant on the custodial controls, required where the "
                 "qualified custodian is the adviser or a related person."),
    citation="17 CFR 275.206(4)-2(a)(6)",
    check=_evidence("has_internal_control_report"),
    applies_when=_is_related_person_custodian,
)

_UK_SEGREGATION_CITATION = (
    "FCA Handbook CASS 6.2 (holding), CASS 6.6 (records and reconciliations)")
_UK_CLIENT_ASSETS_REPORT = CustodyRequirement(
    requirement_id="ANNUAL_CLIENT_ASSETS_REPORT",
    description=("Auditor's client assets report on the firm's compliance with "
                 "the custody rules, delivered to the FCA within four months of "
                 "the end of the period it covers."),
    citation="FCA Handbook SUP 3.10",
    check=_evidence("has_annual_audit"),
)

DEFAULT_CUSTODY_RULES: Dict[str, CustodyRuleSpec] = {
    "US:SECURITIES": CustodyRuleSpec(
        jurisdiction="US",
        regime_id="US:SECURITIES",
        regulator="SEC",
        instrument="17 CFR 275.206(4)-2 (Advisers Act custody rule)",
        asset_scope=ASSET_SCOPE_SECURITIES,
        requirements=(
            CustodyRequirement(
                requirement_id="QUALIFIED_CUSTODIAN",
                description=("Client funds and securities maintained with a "
                             "qualified custodian, meaning an institution within "
                             "one of the categories the rule defines (bank or "
                             "savings association, registered broker-dealer, "
                             "registered FCM, or qualifying foreign financial "
                             "institution). There is no SEC-granted designation "
                             "to hold; the entity either falls in a category or "
                             "it does not."),
                citation="17 CFR 275.206(4)-2(a)(1) and (d)(6)",
                check=_custodian_type_in(_US_ADVISERS_ACT_CUSTODIANS),
            ),
            _US_SEGREGATION,
            _US_SURPRISE_EXAM,
            _US_INTERNAL_CONTROL_REPORT,
        ),
        scope_note=("Applies to SEC-registered investment advisers. State-"
                    "registered advisers are governed by their state's rules, and "
                    "broker-dealer customer protection (17 CFR 240.15c3-3) is a "
                    "separate regime; neither is modelled here."),
    ),
    "US:CRYPTO": CustodyRuleSpec(
        jurisdiction="US",
        regime_id="US:CRYPTO",
        regulator="SEC",
        instrument="17 CFR 275.206(4)-2, as applied to crypto assets",
        asset_scope=ASSET_SCOPE_CRYPTO,
        requirements=(
            CustodyRequirement(
                requirement_id="QUALIFIED_CUSTODIAN",
                description=("A qualified custodian category, or a state-"
                             "chartered trust company relying on the conditional "
                             "staff no-action route."),
                citation="17 CFR 275.206(4)-2(a)(1) and (d)(6)",
                check=_custodian_type_in(_US_CRYPTO_CUSTODIANS),
            ),
            _US_SEGREGATION,
            _US_SURPRISE_EXAM,
            _US_INTERNAL_CONTROL_REPORT,
            CustodyRequirement(
                requirement_id="STATE_TRUST_NO_ACTION_CONDITIONS",
                description=("All conditions of the staff no-action letter "
                             "verified: state banking authority authorisation to "
                             "custody crypto (re-verified annually), audited GAAP "
                             "financials, a recent SOC 1 or SOC 2 report, a custody "
                             "agreement barring lending, pledging, rehypothecation "
                             "or transfer without written consent and requiring "
                             "segregation from proprietary assets, client or board "
                             "risk disclosure, and a documented best-interest "
                             "determination."),
                citation=("SEC Division of Investment Management staff no-action "
                          "letter, 2025-09-30 -- conditional, fact-specific and "
                          "revocable; it did not hold that state trust companies "
                          "satisfy the Advisers Act 'bank' definition"),
                check=_evidence("state_trust_no_action_conditions_verified"),
                applies_when=_is_state_trust_route,
            ),
        ),
        scope_note=("Rule 206(4)-2 remains the operative custody rule: the "
                    "proposed Safeguarding Rule, which would have extended it "
                    "expressly to non-security crypto assets, was withdrawn on "
                    "2025-06-12. Qualification is a legal conclusion for counsel."),
    ),
    "EU:CRYPTO": CustodyRuleSpec(
        jurisdiction="EU",
        regime_id="EU:CRYPTO",
        regulator="National competent authority (ESMA and EBA at Union level)",
        instrument="Regulation (EU) 2023/1114 (MiCA)",
        asset_scope=ASSET_SCOPE_CRYPTO,
        requirements=(
            CustodyRequirement(
                requirement_id="AUTHORISED_CASP",
                description=("Custody and administration of crypto-assets on "
                             "behalf of clients provided by an authorised crypto-"
                             "asset service provider. MiCA imposes no 'qualified "
                             "custodian' test; the gate is authorisation."),
                citation=("MiCA Art. 59(1); the Art. 143(3) transitional regime "
                          "ran to an outer limit of 2026-07-01, and several "
                          "Member States closed it earlier"),
                check=_evidence("custodian_is_authorised_in_jurisdiction"),
            ),
            CustodyRequirement(
                requirement_id="CLIENT_ASSET_SEGREGATION",
                description=("Client crypto-asset holdings segregated from the "
                             "provider's own holdings, with the means of access "
                             "to clients' crypto-assets clearly identified."),
                citation="MiCA Art. 75(7)",
                check=_evidence("is_asset_segregated"),
            ),
            CustodyRequirement(
                requirement_id="CUSTODY_POLICY",
                description=("A custody policy with internal rules and procedures "
                             "for the safekeeping or control of clients' crypto-"
                             "assets."),
                citation="MiCA Art. 75(3)",
                check=_evidence("has_documented_custody_policy"),
            ),
            CustodyRequirement(
                requirement_id="REGISTER_OF_POSITIONS",
                description=("A register of positions opened in the name of each "
                             "client, corresponding to each client's rights."),
                citation="MiCA Art. 75(2)",
                check=_evidence("maintains_client_position_register"),
            ),
            CustodyRequirement(
                requirement_id="PRUDENTIAL_SAFEGUARDS",
                description=("Prudential safeguards of at least the higher of the "
                             "Annex IV permanent minimum capital for the service "
                             "class and one quarter of the preceding year's fixed "
                             "overheads. The safeguard may take the form of own "
                             "funds, a qualifying insurance policy, or a "
                             "comparable guarantee -- insurance is one permitted "
                             "form, not a separate custody mandate."),
                citation=("MiCA Art. 67(1), (4) and (5); Annex IV Class 2 "
                          f"(EUR {MICA_ANNEX_IV_CLASS_2_EUR:,.0f}), the class "
                          "covering custody and administration"),
                check=_mica_prudential_safeguard,
            ),
        ),
        scope_note=("MiCA has applied to crypto-asset service providers since "
                    "2024-12-30. Article 75(8) caps the provider's liability for "
                    "loss at the market value of the crypto-asset lost at the time "
                    "of loss. Custody of financial instruments is governed by "
                    "MiFID II and AIFMD Art. 21 and is not modelled here."),
    ),
    "UK:SECURITIES": CustodyRuleSpec(
        jurisdiction="UK",
        regime_id="UK:SECURITIES",
        regulator="FCA",
        instrument=("FCA Handbook CASS 6 (custody rules); SUP 3.10 (client assets "
                    "report)"),
        asset_scope=ASSET_SCOPE_SECURITIES,
        requirements=(
            CustodyRequirement(
                requirement_id="FCA_AUTHORISED_FIRM",
                description=("Safeguarding and administering investments carried "
                             "on by an authorised firm. CASS has no 'qualified "
                             "custodian' concept; the gate is FCA authorisation "
                             "for the regulated activity."),
                citation="FSMA 2000 s.19; FCA Handbook CASS 6.1",
                check=_evidence("custodian_is_authorised_in_jurisdiction"),
            ),
            CustodyRequirement(
                requirement_id="CLIENT_ASSET_SEGREGATION",
                description=("Safe custody assets held so that they are "
                             "identifiable and separate from the firm's own "
                             "assets, with records and reconciliations to match."),
                citation=_UK_SEGREGATION_CITATION,
                check=_evidence("is_asset_segregated"),
            ),
            _UK_CLIENT_ASSETS_REPORT,
        ),
        scope_note=("CASS 6 also applies to the custody of relevant specified "
                    "investment cryptoassets, which are cryptoassets that are "
                    "themselves specified investments."),
    ),
    "UK:CRYPTO": CustodyRuleSpec(
        jurisdiction="UK",
        regime_id="UK:CRYPTO",
        regulator="FCA",
        instrument=("Financial Services and Markets Act 2000 (Cryptoassets) "
                    "Regulations 2026; FCA PS26/11 and PS26/13; CASS 17"),
        asset_scope=ASSET_SCOPE_CRYPTO,
        effective_from=UK_CRYPTOASSET_REGIME_COMMENCEMENT,
        requirements=(
            CustodyRequirement(
                requirement_id="FCA_AUTHORISED_FIRM",
                description=("Safeguarding qualifying cryptoassets carried on by "
                             "an FCA-authorised firm once the regime commences."),
                citation=("Financial Services and Markets Act 2000 (Cryptoassets) "
                          "Regulations 2026; FCA PS26/11"),
                check=_evidence("custodian_is_authorised_in_jurisdiction"),
            ),
            CustodyRequirement(
                requirement_id="CLIENT_ASSET_SEGREGATION",
                description=("Cryptoasset safeguarding requirements covering "
                             "ownership rights, record-keeping, reconciliation "
                             "and private key management."),
                citation="FCA Handbook CASS 17 (per FCA PS26/13)",
                check=_evidence("is_asset_segregated"),
            ),
            _UK_CLIENT_ASSETS_REPORT,
        ),
        scope_note=("The regime was made on 2026-06-30 and commences on "
                    f"{UK_CRYPTOASSET_REGIME_COMMENCEMENT.isoformat()}. Before "
                    "that date, safeguarding cryptoassets that are not specified "
                    "investments is not a regulated activity in the UK and CASS 17 "
                    "does not apply, so an audit dated earlier is a readiness "
                    "assessment. Firms already safeguarding relevant specified "
                    "investment cryptoassets should be audited under UK:SECURITIES."),
    ),
    "SG:CRYPTO": CustodyRuleSpec(
        jurisdiction="SG",
        regime_id="SG:CRYPTO",
        regulator="MAS",
        instrument=("Payment Services Act 2019 and the Payment Services "
                    "Regulations as amended for digital payment token services; "
                    "MAS guidelines on consumer protection measures by DPT "
                    "service providers"),
        asset_scope=ASSET_SCOPE_CRYPTO,
        requirements=(
            CustodyRequirement(
                requirement_id="MAS_LICENSED_DPT_SERVICE",
                description=("Digital payment token service provided under a MAS "
                             "licence."),
                citation="Payment Services Act 2019",
                check=_evidence("custodian_is_authorised_in_jurisdiction"),
            ),
            CustodyRequirement(
                requirement_id="STATUTORY_TRUST",
                description=("Customers' assets deposited in a trust account held "
                             "on trust for the customer, so that they are "
                             "recoverable on the provider's insolvency."),
                citation=("Payment Services Act 2019 and the Payment Services "
                          "Regulations, as amended for DPT services"),
                check=_evidence("holds_client_assets_on_statutory_trust"),
            ),
            CustodyRequirement(
                requirement_id="CLIENT_ASSET_SEGREGATION",
                description=("Customers' assets segregated from the provider's "
                             "own assets, with proper books and records."),
                citation=("Payment Services Act 2019 and the Payment Services "
                          "Regulations, as amended for DPT services"),
                check=_evidence("is_asset_segregated"),
            ),
            CustodyRequirement(
                requirement_id="COLD_STORAGE_MAJORITY",
                description=(f"At least {MAS_COLD_STORAGE_EXPECTATION_PCT:.0f}% of "
                             "customers' digital payment tokens held in wallets "
                             "not connected to the internet."),
                citation=("MAS guidelines on consumer protection measures by DPT "
                          "service providers -- a supervisory expectation, not a "
                          "statutory obligation"),
                check=_mas_cold_storage,
                mandatory=False,
            ),
        ),
        scope_note=("Covers digital payment token services under the Payment "
                    "Services Act. MAS does not mandate an independent third-party "
                    "custodian -- a provider may maintain the trust account itself "
                    "-- and does not mandate insurance over custodied tokens. "
                    "Custody by capital markets services licensees under the "
                    "Securities and Futures Act is a separate regime and is not "
                    "modelled here."),
    ),
}


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class RegulatoryCustodyRequirementsByJurisdictionEngine:
    """Audits a custody arrangement against the regime that governs it.

    Rules are keyed ``"<JURISDICTION>:<ASSET_SCOPE>"``. Pass ``custom_rules`` to
    add or override a regime; keys must match the spec's ``regime_id``.
    """

    def __init__(self, custom_rules: Optional[Dict[str, CustodyRuleSpec]] = None) -> None:
        self.rules: Dict[str, CustodyRuleSpec] = dict(DEFAULT_CUSTODY_RULES)
        if custom_rules:
            for key, spec in custom_rules.items():
                if not isinstance(spec, CustodyRuleSpec):
                    raise CustodyRegimeError(
                        f"custom_rules['{key}'] is not a CustodyRuleSpec.")
                if spec.regime_id != key:
                    raise CustodyRegimeError(
                        f"custom_rules key '{key}' does not match its "
                        f"regime_id '{spec.regime_id}'.")
                self.rules[key] = spec

    # -- helpers ---------------------------------------------------------

    @property
    def known_jurisdictions(self) -> frozenset:
        return frozenset(spec.jurisdiction for spec in self.rules.values())

    def resolve_regime(self, setup: CustodySetup,
                       regime_id: Optional[str] = None) -> Optional[CustodyRuleSpec]:
        """Return the governing rule spec, or None if none is registered."""
        key = regime_id or (f"{setup.jurisdiction.strip().upper()}:"
                            f"{setup.asset_scope.strip().upper()}")
        return self.rules.get(key)

    @staticmethod
    def _validate(setup: CustodySetup) -> None:
        if not isinstance(setup, CustodySetup):
            raise CustodyRegimeError("setup must be a CustodySetup instance.")
        if not isinstance(setup.jurisdiction, str) or not setup.jurisdiction.strip():
            raise CustodyRegimeError("jurisdiction must be a non-empty string.")
        if not isinstance(setup.custodian_name, str) or not setup.custodian_name.strip():
            raise CustodyRegimeError(
                "custodian_name must be a non-empty string; an unnamed custodian "
                "cannot be evidenced.")
        if not isinstance(setup.custody_type, str) or (
                setup.custody_type.strip().upper() not in _VALID_CUSTODY_TYPES):
            raise CustodyRegimeError(
                f"custody_type {setup.custody_type!r} is not recognised. Expected "
                f"one of {sorted(_VALID_CUSTODY_TYPES)}. A typo must not be "
                "reported as an unqualified-custodian violation.")
        if not isinstance(setup.asset_scope, str) or (
                setup.asset_scope.strip().upper() not in _VALID_ASSET_SCOPES):
            raise CustodyRegimeError(
                f"asset_scope {setup.asset_scope!r} is not recognised. Expected "
                f"one of {sorted(_VALID_ASSET_SCOPES)}.")
        if setup.cold_storage_pct is not None:
            pct = setup.cold_storage_pct
            if not math.isfinite(pct) or not 0.0 <= pct <= 100.0:
                raise CustodyRegimeError(
                    "cold_storage_pct must be a finite percentage in [0, 100]; "
                    f"got {pct!r}.")
        for name in ("prudential_safeguard_eur", "fixed_overheads_prior_year_eur"):
            value = getattr(setup, name)
            if value is None:
                continue
            if not math.isfinite(value) or value < 0.0:
                raise CustodyRegimeError(
                    f"{name} must be a finite non-negative amount; got {value!r}.")

    @staticmethod
    def _unsupported(setup: CustodySetup, status: str, detail: str,
                     as_of: date) -> JurisdictionalCustodyAuditReport:
        jur = setup.jurisdiction.strip().upper()
        notes = f"REGULATORY CUSTODY AUDIT [{status}] ({jur}): {detail}"
        logger.warning(notes)
        return JurisdictionalCustodyAuditReport(
            jurisdiction=jur,
            is_compliant=False,
            violations=[CustodyViolation(jur, status, detail)],
            status=status,
            audit_notes=notes,
            asset_scope=setup.asset_scope.strip().upper(),
            as_of=as_of,
        )

    # -- main entry point ------------------------------------------------

    def audit_custody_setup(self, setup: CustodySetup,
                            regime_id: Optional[str] = None,
                            as_of: Optional[date] = None
                            ) -> JurisdictionalCustodyAuditReport:
        """Audit ``setup`` against its governing regime.

        Args:
            setup: the documented custody arrangement.
            regime_id: an explicit regime key, overriding jurisdiction and asset
                scope resolution.
            as_of: the date the audit speaks to. Defaults to today; pass it
                explicitly for reproducible output.

        Raises:
            CustodyRegimeError: if the setup is malformed. Bad input never
                produces a compliance verdict.
        """
        self._validate(setup)
        if as_of is None:
            as_of = date.today()
        elif not isinstance(as_of, date):
            raise CustodyRegimeError("as_of must be a datetime.date.")

        jur = setup.jurisdiction.strip().upper()
        scope = setup.asset_scope.strip().upper()
        # Work on a normalised copy so vocabulary comparisons are case-insensitive
        # without mutating the caller's object.
        setup = replace(setup, jurisdiction=jur, asset_scope=scope,
                        custody_type=setup.custody_type.strip().upper())
        rule = self.resolve_regime(setup, regime_id)

        if rule is None:
            if jur in self.known_jurisdictions:
                return self._unsupported(
                    setup, STATUS_UNSUPPORTED_REGIME,
                    f"No custody regime registered for '{jur}:{scope}'. The "
                    "jurisdiction is known but this asset scope is not modelled; "
                    "auditing it under another scope's rules would apply the "
                    "wrong regime.", as_of)
            return self._unsupported(
                setup, STATUS_UNKNOWN_JURISDICTION,
                f"No regulatory rules registered for jurisdiction '{jur}'.", as_of)

        in_force = rule.effective_from is None or as_of >= rule.effective_from

        violations: List[CustodyViolation] = []
        advisories: List[CustodyViolation] = []
        satisfied: List[str] = []
        exemptions: List[str] = []
        # Unmet mandatory requirements, counted whether or not the regime has
        # commenced. A pre-commencement audit must not report "compliant" while
        # carrying readiness gaps.
        mandatory_unmet = 0

        for req in rule.requirements:
            if req.applies_when is not None and not req.applies_when(setup):
                continue

            if req.exemption is not None:
                exemption = req.exemption(setup)
                if exemption:
                    exemptions.append(f"{req.requirement_id}: {exemption}")
                    continue

            outcome = req.check(setup)
            if outcome is True:
                satisfied.append(req.requirement_id)
                continue

            if outcome is None:
                finding = CustodyViolation(
                    jurisdiction=jur,
                    violation_type=f"{req.requirement_id}_NOT_EVIDENCED",
                    detail=(f"No evidence supplied for: {req.description} "
                            "Missing evidence is not compliance."),
                    citation=req.citation,
                    severity=SEVERITY_UNEVIDENCED,
                )
            else:
                finding = CustodyViolation(
                    jurisdiction=jur,
                    violation_type=req.requirement_id,
                    detail=f"Requirement not met: {req.description}",
                    citation=req.citation,
                    severity=SEVERITY_MANDATORY if req.mandatory else SEVERITY_ADVISORY,
                )

            if req.mandatory:
                mandatory_unmet += 1
            if req.mandatory and in_force:
                violations.append(finding)
            else:
                if not in_force:
                    finding.severity = SEVERITY_ADVISORY
                advisories.append(finding)

        is_compliant = mandatory_unmet == 0
        if not in_force:
            status = STATUS_PRE_COMMENCEMENT
        elif is_compliant:
            status = STATUS_COMPLIANT
        else:
            status = STATUS_VIOLATION

        notes = (
            f"REGULATORY CUSTODY AUDIT [{status}] ({rule.regime_id} | "
            f"{rule.regulator} | {rule.instrument}) as of {as_of.isoformat()}: "
            f"custodian '{setup.custodian_name}', {len(violations)} violation(s), "
            f"{len(advisories)} advisory finding(s), "
            f"{len(exemptions)} codified exception(s) applied."
        )
        if not in_force:
            notes += (f" Regime commences {rule.effective_from.isoformat()}; "
                      f"{mandatory_unmet} readiness gap(s); findings are a "
                      "readiness assessment, not a live compliance "
                      "determination.")

        if not is_compliant:
            logger.warning(notes)
        else:
            logger.info(notes)

        return JurisdictionalCustodyAuditReport(
            jurisdiction=jur,
            is_compliant=is_compliant,
            violations=violations,
            status=status,
            audit_notes=notes,
            regime_id=rule.regime_id,
            regulator=rule.regulator,
            instrument=rule.instrument,
            asset_scope=rule.asset_scope,
            advisories=advisories,
            satisfied_requirements=satisfied,
            exemptions_applied=exemptions,
            as_of=as_of,
            scope_note=rule.scope_note,
        )

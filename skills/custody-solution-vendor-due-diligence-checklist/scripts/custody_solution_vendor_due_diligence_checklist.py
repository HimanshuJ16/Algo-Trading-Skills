"""
custody-solution-vendor-due-diligence-checklist: institutional due diligence engine
for third-party digital asset custodians.

What this module is and is not
------------------------------
It is a **structured scoring aid** that turns a documented vendor profile into an
auditable, weighted score with explicit red flags. It is not a legal determination.

In particular, there is no SEC-granted designation called "Qualified Custodian".
Rule 206(4)-2 defines the *categories* of institution that qualify (banks and
savings associations, registered broker-dealers, registered futures commission
merchants, and certain foreign financial institutions); an entity either falls in
a category or it does not, and that conclusion is a legal one for counsel.

For **state-chartered trust companies** custodying crypto assets, the position
rests on a Division of Investment Management **staff no-action letter dated
2025-09-30**, which is conditional and revocable, and which pointedly did *not*
state that state trust companies satisfy the Advisers Act "bank" definition.
This module therefore treats a state trust charter as *conditional* — it checks
the letter's substantive conditions and says so in the findings — rather than as
a settled qualification. See ``references/standards.md``.

Determinism
-----------
``evaluate_custodian`` accepts an ``assessment_date``. It defaults to today only
as a convenience; pass it explicitly for reproducible, auditable output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

# NIST CMVP moves all remaining FIPS 140-2 validation certificates to the
# Historical List on this date. Historical is not revocation -- modules keep
# working -- but agencies "should not include" them in new procurements, so a
# custodian relying solely on a 140-2 certificate has a live remediation item.
FIPS_140_2_HISTORICAL_LIST_DATE = date(2026, 9, 21)

FIPS_140_2 = "140-2"
FIPS_140_3 = "140-3"
_VALID_FIPS_STANDARDS = (FIPS_140_2, FIPS_140_3)

# Categories drawn from the Rule 206(4)-2(d)(6) definition, plus the conditional
# state-trust-company route created by the 2025-09-30 staff no-action letter.
CHARTER_FEDERAL_BANK = "FEDERAL_BANK"
CHARTER_STATE_TRUST = "STATE_CHARTERED_TRUST"
CHARTER_BROKER_DEALER = "SEC_BROKER_DEALER"
CHARTER_FCM = "REGISTERED_FCM"
CHARTER_FOREIGN_FI = "FOREIGN_FINANCIAL_INSTITUTION"
CHARTER_UNLICENSED = "UNLICENSED"

#: Charters that map directly onto a Rule 206(4)-2(d)(6) category.
_DIRECT_QUALIFYING_CHARTERS = frozenset({
    CHARTER_FEDERAL_BANK, CHARTER_BROKER_DEALER, CHARTER_FCM, CHARTER_FOREIGN_FI,
})
#: Charters qualifying only through conditional staff relief.
_CONDITIONAL_CHARTERS = frozenset({CHARTER_STATE_TRUST})
_VALID_CHARTERS = _DIRECT_QUALIFYING_CHARTERS | _CONDITIONAL_CHARTERS | {CHARTER_UNLICENSED}

DECISION_APPROVED = "APPROVED"
DECISION_CONDITIONAL = "CONDITIONAL_APPROVAL"
DECISION_REJECTED = "REJECTED"


class CustodyDueDiligenceError(ValueError):
    """Raised when a vendor profile or engine configuration is invalid.

    Due diligence must fail loudly: a profile carrying impossible values (a 150%
    uptime SLA, a negative insurance limit) is a data-entry error, and scoring it
    anyway produces an authoritative-looking number built on garbage.
    """


@dataclass
class CustodyVendorProfile:
    """Documented, evidence-backed attributes of a candidate custodian.

    Every boolean is an assertion the reviewer must be able to support with the
    underlying artefact (charter, SOC report, insurance binder, executed custody
    agreement). The engine scores what it is told; it cannot verify claims.
    """

    vendor_id: str
    vendor_name: str
    charter_type: str
    has_soc2_type2_unqualified: bool
    is_asset_bankruptcy_remote: bool
    crime_insurance_coverage_usd: float
    fips_level: int                                  # 1-4
    uptime_sla_pct: float
    rto_hours: float
    conducts_annual_pen_tests: bool
    fips_standard: str = FIPS_140_3                  # "140-2" or "140-3"
    has_segregation_of_duties: bool = False
    # Conditions of the 2025-09-30 staff no-action letter, material for
    # state-chartered trust companies custodying crypto assets.
    custody_agreement_prohibits_rehypothecation: bool = False
    provides_audited_gaap_financials: bool = False
    state_authorization_verified: bool = False
    #: Assets the custodian would hold for this client. Required to judge whether
    #: an insurance limit is meaningful; scoring falls back to an absolute
    #: threshold (and says so) when it is not supplied.
    assets_under_custody_usd: Optional[float] = None


@dataclass
class PillarScore:
    pillar_name: str
    score: float                                     # 0.0 to 100.0
    weight: float                                    # fractions summing to 1.0
    findings: List[str] = field(default_factory=list)


@dataclass
class CustodyVendorDueDiligenceReport:
    vendor_id: str
    vendor_name: str
    composite_due_diligence_score: float
    decision_status: str
    pillar_breakdown: List[PillarScore]
    critical_red_flags: List[str]
    remediation_action_items: List[str]
    assessment_date: str = ""
    insurance_coverage_ratio: Optional[float] = None


def _require_number(value: object, name: str, *, minimum: float,
                    maximum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise CustodyDueDiligenceError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if numeric < minimum or (maximum is not None and numeric > maximum):
        bound = f"[{minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        raise CustodyDueDiligenceError(f"{name} must be {bound}, got {numeric!r}")
    return numeric


class CustodyVendorDueDiligenceEngine:
    """Scores a custodian across five weighted risk pillars and flags red flags.

    Thresholds are **engineering defaults, not regulatory prescriptions**. Neither
    the SEC nor any other regulator prescribes a due diligence score, an insurance
    coverage ratio, or an uptime SLA for custodians. Calibrate them to your firm's
    mandate and record the calibration.
    """

    #: Pillar weights. Validated to sum to 1.0 so the composite stays on 0-100.
    DEFAULT_WEIGHTS = {
        "REGULATORY_LEGAL": 0.25,
        "CYBERSECURITY": 0.25,
        "INSURANCE_COVERAGE": 0.20,
        "OPERATIONAL_RESILIENCE": 0.15,
        "GOVERNANCE_CONTROLS": 0.15,
    }

    def __init__(
        self,
        min_passing_score: float = 80.0,
        min_insurance_usd: float = 50_000_000.0,
        min_insurance_coverage_ratio: float = 0.10,
        min_uptime_sla_pct: float = 99.9,
        max_rto_hours: float = 4.0,
        weights: Optional[dict] = None,
    ) -> None:
        self.min_passing_score = _require_number(
            min_passing_score, "min_passing_score", minimum=0.0, maximum=100.0)
        self.min_insurance_usd = _require_number(
            min_insurance_usd, "min_insurance_usd", minimum=0.0)
        self.min_insurance_coverage_ratio = _require_number(
            min_insurance_coverage_ratio, "min_insurance_coverage_ratio",
            minimum=0.0, maximum=1.0)
        self.min_uptime_sla_pct = _require_number(
            min_uptime_sla_pct, "min_uptime_sla_pct", minimum=0.0, maximum=100.0)
        self.max_rto_hours = _require_number(max_rto_hours, "max_rto_hours", minimum=0.0)

        self.weights = dict(weights) if weights else dict(self.DEFAULT_WEIGHTS)
        # Check membership before the sum: a partial dict can still sum to 1.0 and
        # would otherwise surface as a bare KeyError from inside pillar scoring.
        expected = set(self.DEFAULT_WEIGHTS)
        supplied = set(self.weights)
        if supplied != expected:
            raise CustodyDueDiligenceError(
                f"weights must define exactly the five pillars {sorted(expected)}; "
                f"missing={sorted(expected - supplied)}, unexpected={sorted(supplied - expected)}"
            )
        for name, weight in self.weights.items():
            _require_number(weight, f"weight[{name}]", minimum=0.0, maximum=1.0)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise CustodyDueDiligenceError(
                f"pillar weights must sum to 1.0 so the composite stays on a 0-100 "
                f"scale, got {total!r}"
            )

    # -- validation ---------------------------------------------------------
    @staticmethod
    def _validate_profile(profile: CustodyVendorProfile) -> None:
        if not isinstance(profile.vendor_id, str) or not profile.vendor_id.strip():
            raise CustodyDueDiligenceError("vendor_id must be a non-empty string")
        if profile.charter_type not in _VALID_CHARTERS:
            raise CustodyDueDiligenceError(
                f"charter_type {profile.charter_type!r} is not recognised; expected one of "
                f"{sorted(_VALID_CHARTERS)}. Refusing to guess -- an unrecognised charter "
                "silently scoring as non-qualifying would misreport the reason for rejection."
            )
        if profile.fips_standard not in _VALID_FIPS_STANDARDS:
            raise CustodyDueDiligenceError(
                f"fips_standard must be one of {_VALID_FIPS_STANDARDS}, got {profile.fips_standard!r}"
            )
        _require_number(profile.fips_level, "fips_level", minimum=1, maximum=4)
        _require_number(profile.uptime_sla_pct, "uptime_sla_pct", minimum=0.0, maximum=100.0)
        _require_number(profile.rto_hours, "rto_hours", minimum=0.0)
        _require_number(profile.crime_insurance_coverage_usd,
                        "crime_insurance_coverage_usd", minimum=0.0)
        if profile.assets_under_custody_usd is not None:
            _require_number(profile.assets_under_custody_usd,
                            "assets_under_custody_usd", minimum=0.0)

    # -- pillars ------------------------------------------------------------
    def _score_regulatory(self, profile: CustodyVendorProfile,
                          red_flags: List[str], actions: List[str]) -> PillarScore:
        """Qualifying basis (60) + bankruptcy-remote segregation (40).

        Segregation carries real weight here. Previously a co-mingling custodian
        still scored 100 on this pillar and so could show a perfect composite
        beside a REJECTED decision.
        """
        findings: List[str] = []
        score = 0.0

        if profile.charter_type in _DIRECT_QUALIFYING_CHARTERS:
            score += 60.0
            findings.append(
                f"Charter ({profile.charter_type}) maps to a Rule 206(4)-2(d)(6) qualified "
                "custodian category. Confirm the specific entity's status with counsel."
            )
        elif profile.charter_type in _CONDITIONAL_CHARTERS:
            conditions = {
                "state banking authorisation verified (re-verify annually)":
                    profile.state_authorization_verified,
                "audited GAAP financial statements obtained":
                    profile.provides_audited_gaap_financials,
                "independent internal control report (SOC 1 or SOC 2) obtained":
                    profile.has_soc2_type2_unqualified,
                "custody agreement prohibits rehypothecation/pledging without consent":
                    profile.custody_agreement_prohibits_rehypothecation,
            }
            unmet = [name for name, met in conditions.items() if not met]
            findings.append(
                "State-chartered trust company: qualification rests on the SEC Division of "
                "Investment Management staff no-action letter of 2025-09-30, which is "
                "conditional and revocable and does NOT hold that state trust companies "
                "satisfy the Advisers Act 'bank' definition."
            )
            if unmet:
                msg = (f"No-action letter conditions unmet for {profile.vendor_name}: "
                       + "; ".join(unmet))
                red_flags.append(f"CRITICAL RED FLAG: {msg}")
                findings.append(msg)
            else:
                score += 60.0
                findings.append("All assessed no-action letter conditions are met.")
        else:
            msg = (f"{profile.vendor_name} charter ({profile.charter_type}) does not map to any "
                   "Rule 206(4)-2(d)(6) qualified custodian category.")
            red_flags.append(f"CRITICAL RED FLAG: {msg}")
            findings.append(msg)

        if profile.is_asset_bankruptcy_remote:
            score += 40.0
            findings.append("Client assets are contractually segregated and bankruptcy-remote.")
        else:
            msg = (f"{profile.vendor_name} client assets are NOT legally bankruptcy-remote; "
                   "assets are exposed to the custodian's creditors on insolvency.")
            red_flags.append(f"CRITICAL RED FLAG: {msg}")
            findings.append(msg)
            actions.append("Obtain legal opinion on bankruptcy-remote segregation.")

        return PillarScore("REGULATORY_LEGAL", score, self.weights["REGULATORY_LEGAL"], findings)

    def _score_security(self, profile: CustodyVendorProfile, assessment_date: date,
                        actions: List[str]) -> PillarScore:
        """SOC 2 Type II (40) + FIPS level >= 3 (35) + annual pen testing (25)."""
        findings: List[str] = []
        score = 0.0

        if profile.has_soc2_type2_unqualified:
            score += 40.0
            findings.append(
                "Unqualified SOC 2 Type II report on file. Confirm the report period covers "
                "the review window and that the Trust Services Criteria in scope include "
                "Security; obtain a bridge letter for any gap period."
            )
        else:
            msg = "No unqualified SOC 2 Type II report on file."
            findings.append(msg)
            actions.append(msg)

        if profile.fips_level >= 3:
            score += 35.0
            findings.append(
                f"Key material protected by FIPS {profile.fips_standard} Level {profile.fips_level} "
                "validated modules."
            )
        else:
            msg = (f"FIPS {profile.fips_standard} Level {profile.fips_level} is below Level 3; "
                   "Levels 3-4 add the tamper-response and identity-based key management "
                   "expected for institutional custody.")
            findings.append(msg)
            actions.append(msg)

        if profile.fips_standard == FIPS_140_2:
            msg = (f"Validation cites FIPS 140-2. NIST CMVP moves all remaining FIPS 140-2 "
                   f"certificates to the Historical List on "
                   f"{FIPS_140_2_HISTORICAL_LIST_DATE.isoformat()}"
                   + (" (already elapsed)." if assessment_date >= FIPS_140_2_HISTORICAL_LIST_DATE
                      else ".")
                   + " Historical status is not revocation, but the certificate should not be "
                     "relied on for new procurement; request a FIPS 140-3 validation roadmap.")
            findings.append(msg)
            actions.append(msg)

        if profile.conducts_annual_pen_tests:
            score += 25.0
            findings.append("Annual independent penetration testing confirmed.")
        else:
            msg = "No annual independent penetration testing evidenced."
            findings.append(msg)
            actions.append(msg)

        return PillarScore("CYBERSECURITY", score, self.weights["CYBERSECURITY"], findings)

    def _score_insurance(self, profile: CustodyVendorProfile,
                         actions: List[str]) -> tuple:
        """Score the insurance limit against assets actually at risk.

        An absolute limit is close to meaningless on its own: a $100M policy is
        strong against $200M of custodied assets and near-irrelevant against $10B.
        When ``assets_under_custody_usd`` is supplied the score is driven by the
        coverage ratio; otherwise it falls back to the absolute threshold and the
        finding says so rather than implying the ratio was checked.
        """
        findings: List[str] = []
        coverage_ratio: Optional[float] = None
        limit = profile.crime_insurance_coverage_usd

        if profile.assets_under_custody_usd is None:
            findings.append(
                "Assets under custody not supplied -- coverage scored against the absolute "
                "limit only. The coverage RATIO was not assessed."
            )
            actions.append(
                "Supply assets_under_custody_usd so insurance adequacy can be assessed as a "
                "ratio rather than an absolute limit."
            )
            score = 100.0 if limit >= self.min_insurance_usd else round(
                (limit / self.min_insurance_usd) * 100.0, 2) if self.min_insurance_usd else 100.0
            if limit < self.min_insurance_usd:
                actions.append(
                    f"Crime/specie limit (${limit:,.2f}) is below the ${self.min_insurance_usd:,.2f} "
                    "policy minimum."
                )
        elif profile.assets_under_custody_usd == 0:
            findings.append("Assets under custody are zero; coverage ratio is not meaningful.")
            score = 100.0 if limit >= self.min_insurance_usd else 0.0
        else:
            coverage_ratio = limit / profile.assets_under_custody_usd
            findings.append(
                f"Crime/specie limit ${limit:,.2f} against ${profile.assets_under_custody_usd:,.2f} "
                f"under custody = {coverage_ratio:.2%} coverage."
            )
            score = min(100.0, round(
                (coverage_ratio / self.min_insurance_coverage_ratio) * 100.0, 2
            )) if self.min_insurance_coverage_ratio else 100.0
            if coverage_ratio < self.min_insurance_coverage_ratio:
                msg = (f"Coverage ratio {coverage_ratio:.2%} is below the "
                       f"{self.min_insurance_coverage_ratio:.2%} policy floor.")
                findings.append(msg)
                actions.append(msg)
            findings.append(
                "Crime/specie policies typically cover named perils (theft, insider fraud, "
                "physical loss of key material) and not market loss or protocol failure. "
                "Read the perils and sub-limits, not just the headline limit."
            )

        return PillarScore("INSURANCE_COVERAGE", score,
                           self.weights["INSURANCE_COVERAGE"], findings), coverage_ratio

    def _score_operations(self, profile: CustodyVendorProfile,
                          actions: List[str]) -> PillarScore:
        """Uptime SLA (60) + Recovery Time Objective (40)."""
        findings: List[str] = []
        score = 0.0

        if profile.uptime_sla_pct >= self.min_uptime_sla_pct:
            score += 60.0
            findings.append(f"Uptime SLA {profile.uptime_sla_pct}% meets the "
                            f"{self.min_uptime_sla_pct}% target.")
        else:
            msg = (f"Uptime SLA {profile.uptime_sla_pct}% is below the "
                   f"{self.min_uptime_sla_pct}% target.")
            findings.append(msg)
            actions.append(msg)

        if profile.rto_hours <= self.max_rto_hours:
            score += 40.0
            findings.append(f"Recovery Time Objective {profile.rto_hours}h meets the "
                            f"{self.max_rto_hours}h target.")
        else:
            msg = (f"Recovery Time Objective {profile.rto_hours}h exceeds the "
                   f"{self.max_rto_hours}h target.")
            findings.append(msg)
            actions.append(msg)

        return PillarScore("OPERATIONAL_RESILIENCE", score,
                           self.weights["OPERATIONAL_RESILIENCE"], findings)

    def _score_governance(self, profile: CustodyVendorProfile,
                          actions: List[str]) -> PillarScore:
        """Segregation of duties (50) + annual penetration testing (50).

        Findings are derived from the profile. The previous implementation emitted
        a fixed "controls audited" finding regardless of input, asserting an audit
        that may never have happened.
        """
        findings: List[str] = []
        score = 0.0

        if profile.has_segregation_of_duties:
            score += 50.0
            findings.append(
                "Segregation of duties evidenced between transaction initiation, approval, "
                "and key custody."
            )
        else:
            msg = ("No evidenced segregation of duties between transaction initiation, "
                   "approval, and key custody.")
            findings.append(msg)
            actions.append(msg)

        if profile.conducts_annual_pen_tests:
            score += 50.0
            findings.append("Annual independent penetration testing confirmed.")
        else:
            findings.append("No annual independent penetration testing evidenced.")

        return PillarScore("GOVERNANCE_CONTROLS", score,
                           self.weights["GOVERNANCE_CONTROLS"], findings)

    # -- entry point --------------------------------------------------------
    def evaluate_custodian(
        self,
        profile: CustodyVendorProfile,
        assessment_date: Optional[date] = None,
    ) -> CustodyVendorDueDiligenceReport:
        """Audit a vendor profile across five weighted risk pillars.

        Pass ``assessment_date`` explicitly for reproducible output; it defaults to
        today, which makes the FIPS 140-2 sunset finding time-dependent.
        """
        if not isinstance(profile, CustodyVendorProfile):
            raise CustodyDueDiligenceError(
                f"profile must be a CustodyVendorProfile, got {type(profile).__name__}"
            )
        self._validate_profile(profile)
        if assessment_date is None:
            assessment_date = date.today()
        elif not isinstance(assessment_date, date):
            raise CustodyDueDiligenceError(
                f"assessment_date must be a datetime.date, got {assessment_date!r}"
            )

        red_flags: List[str] = []
        actions: List[str] = []

        regulatory = self._score_regulatory(profile, red_flags, actions)
        security = self._score_security(profile, assessment_date, actions)
        insurance, coverage_ratio = self._score_insurance(profile, actions)
        operations = self._score_operations(profile, actions)
        governance = self._score_governance(profile, actions)

        pillars = [regulatory, security, insurance, operations, governance]
        composite = round(sum(p.score * p.weight for p in pillars), 2)

        if red_flags:
            decision = DECISION_REJECTED
        elif composite >= self.min_passing_score and not actions:
            decision = DECISION_APPROVED
        else:
            decision = DECISION_CONDITIONAL

        logger.info(
            "CUSTODY DUE DILIGENCE [%s] on %s: score=%.2f/100, red_flags=%d, "
            "action_items=%d -> %s",
            profile.vendor_name, assessment_date.isoformat(), composite,
            len(red_flags), len(actions), decision,
        )

        return CustodyVendorDueDiligenceReport(
            vendor_id=profile.vendor_id,
            vendor_name=profile.vendor_name,
            composite_due_diligence_score=composite,
            decision_status=decision,
            pillar_breakdown=pillars,
            critical_red_flags=red_flags,
            remediation_action_items=actions,
            assessment_date=assessment_date.isoformat(),
            insurance_coverage_ratio=coverage_ratio,
        )

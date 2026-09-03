"""
alternative-data-vendor-due-diligence-checklist:
Compliance evaluation engine for alternative data vendor onboarding.

Emits a versioned, audit-serializable DiligenceRecord that downstream pipelines
(e.g. alternative-data-feature-integration) consume as the gate artifact.
"""
from __future__ import annotations

import dataclasses
import datetime
import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Bump whenever a rule is added, reclassified, or removed so a historical
# DiligenceRecord can be re-validated against the rule set in force at the
# time of the original decision.
RULE_VERSION = "1.3.0"

# A DDQ dated slightly in the future is almost always benign timezone skew
# (the vendor stamped it with a local date ahead of UTC). Anything beyond this
# tolerance is a data-entry or backdating error and must not pass a gate whose
# entire purpose is to evidence that the questionnaire is current.
_FUTURE_DDQ_TOLERANCE_DAYS = 1

# Default re-diligence cadence (in days) per risk tier for an APPROVED vendor.
# Tier-1 (approved with warnings): annual, Tier-2: biennial, Tier-3: triennial.
_REVIEW_DAYS_BY_TIER: dict[str, int] = {
    "Tier-1": 365,
    "Tier-2": 730,
    "Tier-3": 1095,
}


class FlagCode(str, enum.Enum):
    """Machine-parseable compliance flag codes (paired with prose messages)."""

    NO_RESELL_RIGHTS = "NO_RESELL_RIGHTS"
    MNPI = "MNPI"
    CFAA_LOGIN_SCRAPE = "CFAA_LOGIN_SCRAPE"
    LOGIN_SCRAPE_AUTHORIZED = "LOGIN_SCRAPE_AUTHORIZED"
    PII_NO_GDPR = "PII_NO_GDPR"
    PII_NO_ANONYMIZATION = "PII_NO_ANONYMIZATION"
    INCONSISTENT_DDQ = "INCONSISTENT_DDQ"
    STALE_DDQ = "STALE_DDQ"
    FUTURE_DATED_DDQ = "FUTURE_DATED_DDQ"
    TOS_NONCOMPLIANT = "TOS_NONCOMPLIANT"


class Decision(str, enum.Enum):
    """Terminal diligence verdict for a vendor dataset."""

    APPROVED = "APPROVED"
    APPROVED_WITH_WARNINGS = "APPROVED_WITH_WARNINGS"
    REJECTED = "REJECTED"


# Boolean DDQ fields that must be strictly typed to prevent a sloppily-mapped
# string (e.g. has_resell_rights='no', truthy) silently approving a vendor.
_DDQ_BOOL_FIELDS = (
    "has_resell_rights",
    "is_web_scraped",
    "scrapes_behind_login",
    "bypasses_captchas",
    "contains_pii",
    "is_gdpr_ccpa_compliant",
    "has_robust_anonymization",
    "is_material_non_public_information",
    "is_tos_compliant",
    "has_documented_login_authorization",
)


@dataclass(frozen=True)
class VendorDueDiligenceQuestionnaire:
    """A vendor's due-diligence responses. Frozen + post_init validation so a
    truthy non-bool (e.g. ``'no'``) cannot silently masquerade as ``True``."""

    vendor_name: str
    dataset_name: str

    # Legal & Rights
    has_resell_rights: bool

    # Web Scraping Legal Risks
    is_web_scraped: bool
    scrapes_behind_login: bool
    bypasses_captchas: bool

    # Privacy Risks (PII)
    contains_pii: bool
    is_gdpr_ccpa_compliant: bool
    has_robust_anonymization: bool

    # Insider Trading / MNPI Risks
    is_material_non_public_information: bool

    # Terms of Service compliance (post-hiQ: ToS, not just login/CAPTCHA,
    # is the operative legal standard).
    is_tos_compliant: bool = True

    # Freshness of the questionnaire. When missing or stale the evaluator fails
    # closed rather than treating an old DDQ as fresh.
    ddq_as_of_date: Optional[datetime.date] = None

    # Operator-verified: the firm holds a written instrument from the *source
    # operator* (contract, API licence, partner data-sharing agreement)
    # authorising the vendor's authenticated access. Defaults to False so an
    # unmapped DDQ still hard-rejects. This is not a vendor assertion — an
    # authorised login scrape is downgraded to a warning requiring recorded
    # legal review, never to a clean approval. See references/standards.md.
    has_documented_login_authorization: bool = False

    def __post_init__(self) -> None:
        for name in _DDQ_BOOL_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise TypeError(
                    "DDQ field %r must be a strict bool, got %s=%r"
                    % (name, type(value).__name__, value)
                )

        # Cross-field consistency: detect logically impossible DDQ states that
        # would otherwise pass with zero flags. Fail closed by raising.
        if self.bypasses_captchas and not self.is_web_scraped:
            raise ValueError(
                "Inconsistent DDQ: bypasses_captchas=True requires "
                "is_web_scraped=True."
            )
        if self.scrapes_behind_login and not self.is_web_scraped:
            raise ValueError(
                "Inconsistent DDQ: scrapes_behind_login=True requires "
                "is_web_scraped=True."
            )
        if self.has_documented_login_authorization and not self.scrapes_behind_login:
            raise ValueError(
                "Inconsistent DDQ: has_documented_login_authorization=True is "
                "meaningless without scrapes_behind_login=True."
            )


@dataclass(frozen=True)
class DiligenceRecord:
    """Audit-serializable record emitted by the evaluator. Frozen + tuple
    fields so a downstream bug cannot mutate a rejection into an approval
    or erase a flag after the fact."""

    vendor_name: str
    dataset_name: str
    decision: Decision
    rule_version: str
    evaluated_at: str  # ISO-8601 UTC timestamp
    is_approved: bool
    critical_flags: tuple[str, ...]
    critical_codes: tuple[FlagCode, ...]
    warnings: tuple[str, ...]
    warning_codes: tuple[FlagCode, ...]
    risk_tier: Optional[str]
    next_review_date: Optional[str]  # ISO-8601 date, None for rejected vendors
    audit_notes: str


def _utc_now() -> datetime.datetime:
    """Single UTC clock reading for one evaluation (seconds resolution).

    Every date in a DiligenceRecord — the audit timestamp, the DDQ-age
    computation and the derived next_review_date — is taken from this one
    reading. Mixing a local ``date.today()`` with a UTC timestamp put two
    different "today"s into the same audit record and could shift the DDQ-age
    boundary by a day depending on where the evaluation ran.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _compute_risk_tier(
    ddq: VendorDueDiligenceQuestionnaire, warnings: tuple[str, ...]
) -> str:
    """Map a passing vendor to a re-diligence tier.

    Tier-1: an approval carrying a warning (the manual legal review path).
    Tier-2: PII-bearing but fully anonymized/compliant dataset.
    Tier-3: clean, non-PII, non-scraped dataset.
    """
    if warnings:
        return "Tier-1"
    if ddq.contains_pii or ddq.is_web_scraped:
        return "Tier-2"
    return "Tier-3"


def _next_review_date_iso(risk_tier: str, as_of: datetime.date) -> str:
    days = _REVIEW_DAYS_BY_TIER.get(risk_tier, _REVIEW_DAYS_BY_TIER["Tier-1"])
    return (as_of + datetime.timedelta(days=days)).isoformat()


class VendorDueDiligenceEvaluator:
    """Evaluates a vendor DDQ against strict institutional compliance rules.

    The rule set is configurable via the constructor (peer-skill pattern) so a
    caller can tighten the DDQ freshness window without forking the engine.
    """

    def __init__(self, max_ddq_age_days: int = 365):
        if max_ddq_age_days <= 0:
            raise ValueError("max_ddq_age_days must be a positive integer")
        self.max_ddq_age_days = max_ddq_age_days

    def evaluate(self, ddq: VendorDueDiligenceQuestionnaire) -> DiligenceRecord:
        critical_flags: list[str] = []
        critical_codes: list[FlagCode] = []
        warnings: list[str] = []
        warning_codes: list[FlagCode] = []

        def add_critical(code: FlagCode, message: str) -> None:
            critical_flags.append(message)
            critical_codes.append(code)

        def add_warning(code: FlagCode, message: str) -> None:
            warnings.append(message)
            warning_codes.append(code)

        # 1. Legal Rights Check
        if not ddq.has_resell_rights:
            add_critical(
                FlagCode.NO_RESELL_RIGHTS,
                "Vendor does not possess explicit legal rights to resell this data.",
            )

        # 2. MNPI Check
        if ddq.is_material_non_public_information:
            add_critical(
                FlagCode.MNPI,
                "Data is flagged as Material Non-Public Information (MNPI). Strict prohibition.",
            )

        # 3. Web Scraping & CFAA Risks
        if ddq.is_web_scraped:
            if ddq.scrapes_behind_login and not ddq.has_documented_login_authorization:
                add_critical(
                    FlagCode.CFAA_LOGIN_SCRAPE,
                    "Vendor scrapes behind authenticated logins without documented "
                    "authorization from the source operator. High CFAA/legal risk.",
                )
            elif ddq.scrapes_behind_login:
                # Authorization is what the CFAA turns on post-Van Buren, so this
                # is not a hard reject — but it is never a clean approval either.
                # Legal must confirm the instrument actually covers this
                # collection and the firm's downstream use.
                add_warning(
                    FlagCode.LOGIN_SCRAPE_AUTHORIZED,
                    "Vendor scrapes behind an authenticated login under documented "
                    "authorization. Legal must verify the authorization instrument "
                    "covers this collection method and the firm's intended use.",
                )
            if ddq.bypasses_captchas:
                add_warning(
                    FlagCode.TOS_NONCOMPLIANT,
                    "Vendor bypasses CAPTCHAs. Review target site Terms of Service "
                    "for scraping prohibitions.",
                )

        # 4. Terms of Service compliance (post-hiQ: the operative legal standard
        # for public scraping, distinct from the login/CAPTCHA checks above).
        if not ddq.is_tos_compliant:
            add_critical(
                FlagCode.TOS_NONCOMPLIANT,
                "Vendor's collection method is not compliant with the source "
                "Terms of Service.",
            )

        # 5. PII and Privacy Regulation (GDPR / CCPA)
        if ddq.contains_pii:
            if not ddq.is_gdpr_ccpa_compliant:
                add_critical(
                    FlagCode.PII_NO_GDPR,
                    "Data contains PII but vendor lacks GDPR/CCPA compliance.",
                )
            if not ddq.has_robust_anonymization:
                add_critical(
                    FlagCode.PII_NO_ANONYMIZATION,
                    "Data contains PII without robust anonymization protocols.",
                )

        # 6. DDQ freshness — fail closed on an undated, stale, or future-dated
        # questionnaire. All three mean the same thing: the gate cannot evidence
        # that the responses describe the vendor's current practices.
        evaluated_dt = _utc_now()
        evaluation_date = evaluated_dt.date()

        if ddq.ddq_as_of_date is None:
            add_critical(
                FlagCode.STALE_DDQ,
                "DDQ undated — cannot verify freshness.",
            )
        else:
            age_days = (evaluation_date - ddq.ddq_as_of_date).days
            if age_days < -_FUTURE_DDQ_TOLERANCE_DAYS:
                add_critical(
                    FlagCode.FUTURE_DATED_DDQ,
                    "DDQ dated %d days in the future (tolerance %d day(s)) — "
                    "data-entry or backdating error; freshness cannot be relied on."
                    % (-age_days, _FUTURE_DDQ_TOLERANCE_DAYS),
                )
            elif age_days > self.max_ddq_age_days:
                add_critical(
                    FlagCode.STALE_DDQ,
                    "DDQ stale (age %d days > %d-day limit) — cannot verify freshness."
                    % (age_days, self.max_ddq_age_days),
                )

        # 7. Determine the terminal verdict.
        is_approved = len(critical_flags) == 0
        has_warnings = len(warnings) > 0

        if is_approved and has_warnings:
            decision = Decision.APPROVED_WITH_WARNINGS
        elif is_approved:
            decision = Decision.APPROVED
        else:
            decision = Decision.REJECTED

        risk_tier: Optional[str] = None
        next_review_date: Optional[str] = None
        evaluated_at = evaluated_dt.isoformat()

        if is_approved:
            risk_tier = _compute_risk_tier(ddq, tuple(warnings))
            next_review_date = _next_review_date_iso(risk_tier, evaluation_date)

        # Tiered logging by risk, not by approve/reject. Lazy interpolation so
        # error aggregators group by template instead of per-vendor (Pylint W1203).
        if decision == Decision.REJECTED and FlagCode.MNPI in critical_codes:
            logger.critical(
                "Vendor %s (%s) REJECTED — MNPI detected: %s",
                ddq.vendor_name, ddq.dataset_name, critical_flags,
            )
        elif decision == Decision.REJECTED:
            logger.error(
                "Vendor %s (%s) REJECTED — critical compliance flags: %s",
                ddq.vendor_name, ddq.dataset_name, critical_flags,
            )
        elif decision == Decision.APPROVED_WITH_WARNINGS:
            logger.warning(
                "Vendor %s (%s) APPROVED WITH WARNINGS — %s",
                ddq.vendor_name, ddq.dataset_name, warnings,
            )
        else:
            logger.info(
                "Vendor %s (%s) APPROVED for onboarding.",
                ddq.vendor_name, ddq.dataset_name,
            )

        audit_notes = self._build_audit_notes(decision, ddq, critical_flags, warnings)

        return DiligenceRecord(
            vendor_name=ddq.vendor_name,
            dataset_name=ddq.dataset_name,
            decision=decision,
            rule_version=RULE_VERSION,
            evaluated_at=evaluated_at,
            is_approved=is_approved,
            critical_flags=tuple(critical_flags),
            critical_codes=tuple(critical_codes),
            warnings=tuple(warnings),
            warning_codes=tuple(warning_codes),
            risk_tier=risk_tier,
            next_review_date=next_review_date,
            audit_notes=audit_notes,
        )

    @staticmethod
    def _build_audit_notes(
        decision: Decision,
        ddq: VendorDueDiligenceQuestionnaire,
        critical_flags: list[str],
        warnings: list[str],
    ) -> str:
        if decision == Decision.APPROVED:
            return "Clean approval — no critical flags, no warnings."
        if decision == Decision.APPROVED_WITH_WARNINGS:
            return (
                "Approved pending recorded manual legal review of warnings: "
                + "; ".join(warnings)
            )
        return "Rejected — critical flags: " + "; ".join(critical_flags)

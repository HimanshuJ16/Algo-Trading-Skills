"""eu-benchmark-regulation-for-strategies-referencing-indices: scope-first
compliance engine for the EU Benchmarks Regulation (Regulation (EU) 2016/1011,
"BMR") as amended by Regulation (EU) 2025/914, applicable from 1 January 2026.

What this module is and is not
------------------------------
It is a **scope and obligation engine for benchmark USERS**. Given a supervised
entity's use of an index, it decides whether the BMR user obligations bind at
all, and if they do, whether Article 29 permits the reference and whether the
Article 28(2) written-plan limbs are satisfied.

It does **not** query the ESMA register, does not classify a benchmark as
critical/significant/climate/commodity for you, and cannot confirm that a
written plan exists or is adequate. Every input is an operator's assertion
backed by evidence held elsewhere: the ESMA register entry, the Commission's
critical-benchmark implementing act, the administrator's benchmark statement,
and the firm's own plan documents.

The 1 January 2026 scope cut is the whole point
-----------------------------------------------
Before that date the BMR applied to essentially every index used as a benchmark
in the Union, and Article 29(1) barred a supervised entity from using one unless
the administrator was on the ESMA register. Regulation (EU) 2025/914 narrowed
Article 2(1) to four categories only:

  * critical benchmarks (Commission implementing act; currently EURIBOR, EONIA,
    STIBOR, WIBOR, NIBOR);
  * significant benchmarks (>= EUR 50bn EU use over six months, or designated
    by a competent authority, or opted in at >= EUR 20bn);
  * EU Climate Transition Benchmarks (CTBs) and EU Paris-aligned Benchmarks
    (PABs);
  * commodity benchmarks subject to Annex II.

Everything else is out of scope. A pre-2026 engine that flags every unregistered
benchmark as a violation now produces false positives on indices an EU fund is
perfectly entitled to reference, so scope is evaluated *before* any register
test. See ``references/standards.md`` for the sourcing.

Determinism
-----------
``audit_strategy_bmr_compliance`` accepts ``assessment_date``. It falls back to
``date.today()`` only as a convenience; pass it explicitly so a report is
reproducible, and so a historical record is assessed against the regime that was
actually in force on the date in question.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BmrConfigurationError(ValueError):
    """Raised on malformed or internally inconsistent audit input.

    A data-entry error is never reported as a regulatory finding: a mistyped
    benchmark id or an unrecognised category is a defect in the audit input,
    not evidence that the firm has breached the BMR.
    """


# --------------------------------------------------------------------------
# Regime dates
# --------------------------------------------------------------------------

#: Date from which Regulation (EU) 2025/914 applies. Assessments dated before
#: this fall under the wider pre-amendment scope.
BMR_AMENDMENT_APPLICATION_DATE = date(2026, 1, 1)

#: Administrators on the ESMA register at end-2025 retain their status until
#: this date; those whose benchmarks are out of scope are removed from
#: 1 October 2026 (ESMA public statement ESMA81-1841807023-996).
BMR_REGISTER_GRANDFATHER_END_DATE = date(2026, 9, 30)


# --------------------------------------------------------------------------
# Article 3(1)(17) — supervised entities. The BMR user obligations in
# Articles 28(2) and 29 bind supervised entities only.
# --------------------------------------------------------------------------

ENTITY_CREDIT_INSTITUTION = "CREDIT_INSTITUTION"
ENTITY_INVESTMENT_FIRM = "INVESTMENT_FIRM"
ENTITY_INSURANCE_UNDERTAKING = "INSURANCE_UNDERTAKING"
ENTITY_REINSURANCE_UNDERTAKING = "REINSURANCE_UNDERTAKING"
ENTITY_UCITS = "UCITS"
ENTITY_AIFM = "AIFM"
ENTITY_IORP = "IORP"
ENTITY_CONSUMER_CREDIT_CREDITOR = "CONSUMER_CREDIT_CREDITOR"
ENTITY_MORTGAGE_CREDIT_NON_CREDIT_INSTITUTION = "MORTGAGE_CREDIT_NON_CREDIT_INSTITUTION"
ENTITY_MARKET_OPERATOR = "MARKET_OPERATOR"
ENTITY_CCP = "CCP"
ENTITY_TRADE_REPOSITORY = "TRADE_REPOSITORY"
ENTITY_BMR_ADMINISTRATOR = "BMR_ADMINISTRATOR"

#: Sentinel for anything outside Article 3(1)(17) — an unregulated proprietary
#: trading firm, a family office, a non-EU manager without an EU AIFM.
ENTITY_NON_SUPERVISED = "NON_SUPERVISED"

SUPERVISED_ENTITY_TYPES: FrozenSet[str] = frozenset({
    ENTITY_CREDIT_INSTITUTION,
    ENTITY_INVESTMENT_FIRM,
    ENTITY_INSURANCE_UNDERTAKING,
    ENTITY_REINSURANCE_UNDERTAKING,
    ENTITY_UCITS,
    ENTITY_AIFM,
    ENTITY_IORP,
    ENTITY_CONSUMER_CREDIT_CREDITOR,
    ENTITY_MORTGAGE_CREDIT_NON_CREDIT_INSTITUTION,
    ENTITY_MARKET_OPERATOR,
    ENTITY_CCP,
    ENTITY_TRADE_REPOSITORY,
    ENTITY_BMR_ADMINISTRATOR,
})

_VALID_ENTITY_TYPES: FrozenSet[str] = SUPERVISED_ENTITY_TYPES | {ENTITY_NON_SUPERVISED}


# --------------------------------------------------------------------------
# Article 3(1)(7) — "use of a benchmark" is a CLOSED list. Activity outside it
# is not use, however central the index is to the strategy.
# --------------------------------------------------------------------------

USE_ISSUANCE_OF_FINANCIAL_INSTRUMENT = "ISSUANCE_OF_FINANCIAL_INSTRUMENT"
USE_DETERMINATION_OF_AMOUNT_PAYABLE = "DETERMINATION_OF_AMOUNT_PAYABLE"
USE_PARTY_TO_FINANCIAL_CONTRACT = "PARTY_TO_FINANCIAL_CONTRACT"
USE_PROVIDING_BORROWING_RATE = "PROVIDING_BORROWING_RATE"
USE_MEASURING_FUND_PERFORMANCE = "MEASURING_FUND_PERFORMANCE"

#: Sentinel for activity that references an index without being Article 3(1)(7)
#: use — e.g. trading an index future or ETF on a venue for the firm's own book,
#: or using an index purely as a research, hedging or risk input.
USE_NOT_A_BMR_USE = "NOT_A_BMR_USE"

BMR_USE_TYPES: FrozenSet[str] = frozenset({
    USE_ISSUANCE_OF_FINANCIAL_INSTRUMENT,
    USE_DETERMINATION_OF_AMOUNT_PAYABLE,
    USE_PARTY_TO_FINANCIAL_CONTRACT,
    USE_PROVIDING_BORROWING_RATE,
    USE_MEASURING_FUND_PERFORMANCE,
})

_VALID_USE_TYPES: FrozenSet[str] = BMR_USE_TYPES | {USE_NOT_A_BMR_USE}


# --------------------------------------------------------------------------
# Article 2(1) — in-scope benchmark categories from 1 January 2026.
# --------------------------------------------------------------------------

CATEGORY_CRITICAL = "CRITICAL"
CATEGORY_SIGNIFICANT = "SIGNIFICANT"
CATEGORY_EU_CLIMATE = "EU_CLIMATE"                      # CTB or PAB
CATEGORY_COMMODITY_ANNEX_II = "COMMODITY_ANNEX_II"

#: Everything the amended Article 2(1) leaves out, including what the pre-2026
#: regime called a non-significant benchmark.
CATEGORY_OUT_OF_SCOPE = "OUT_OF_SCOPE"

IN_SCOPE_CATEGORIES: FrozenSet[str] = frozenset({
    CATEGORY_CRITICAL,
    CATEGORY_SIGNIFICANT,
    CATEGORY_EU_CLIMATE,
    CATEGORY_COMMODITY_ANNEX_II,
})

#: Categories for which Article 29(1) conditions a NEW reference on the
#: administrator being on the ESMA register. A significant benchmark is not in
#: this set: new references to it are barred only while it is the object of an
#: Article 24a(6) public notice.
REGISTER_GATED_CATEGORIES: FrozenSet[str] = frozenset({
    CATEGORY_CRITICAL,
    CATEGORY_EU_CLIMATE,
    CATEGORY_COMMODITY_ANNEX_II,
})

_VALID_CATEGORIES: FrozenSet[str] = IN_SCOPE_CATEGORIES | {CATEGORY_OUT_OF_SCOPE}


# --------------------------------------------------------------------------
# Article 2(2) — persons, activities and benchmarks the BMR does not apply to.
# --------------------------------------------------------------------------

EXEMPTION_CENTRAL_BANK = "CENTRAL_BANK"
EXEMPTION_PUBLIC_AUTHORITY = "PUBLIC_AUTHORITY"
EXEMPTION_CCP_SETTLEMENT_PRICE = "CCP_SETTLEMENT_PRICE"
EXEMPTION_SINGLE_REFERENCE_PRICE = "SINGLE_REFERENCE_PRICE"
EXEMPTION_PRESS_MEDIA = "PRESS_MEDIA"
EXEMPTION_OWN_BORROWING_RATE = "OWN_BORROWING_RATE"
EXEMPTION_UNAWARE_INDEX_PROVIDER = "UNAWARE_INDEX_PROVIDER"
EXEMPTION_DESIGNATED_SPOT_FX = "DESIGNATED_SPOT_FX"

ARTICLE_2_2_EXEMPTIONS: FrozenSet[str] = frozenset({
    EXEMPTION_CENTRAL_BANK,
    EXEMPTION_PUBLIC_AUTHORITY,
    EXEMPTION_CCP_SETTLEMENT_PRICE,
    EXEMPTION_SINGLE_REFERENCE_PRICE,
    EXEMPTION_PRESS_MEDIA,
    EXEMPTION_OWN_BORROWING_RATE,
    EXEMPTION_UNAWARE_INDEX_PROVIDER,
    EXEMPTION_DESIGNATED_SPOT_FX,
})


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

SEVERITY_VIOLATION = "VIOLATION"
SEVERITY_ACTION_REQUIRED = "ACTION_REQUIRED"
SEVERITY_ADVISORY = "ADVISORY"
SEVERITY_INFO = "INFO"

FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR = (
    "ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR")
FINDING_ADDITION_PROHIBITED_WARNING_NOTICE = "ADDITION_PROHIBITED_WARNING_NOTICE"
FINDING_WARNING_NOTICE_DEROGATION_ACTIVE = "WARNING_NOTICE_DEROGATION_ACTIVE"
FINDING_EXISTING_USE_REPLACEMENT_REQUIRED = "EXISTING_USE_REPLACEMENT_REQUIRED"
FINDING_EXISTING_USE_REPLACEMENT_OVERDUE = "EXISTING_USE_REPLACEMENT_OVERDUE"
FINDING_MISSING_WRITTEN_PLAN = "MISSING_WRITTEN_PLAN"
FINDING_MISSING_FALLBACK_PROVISIONS = "MISSING_FALLBACK_PROVISIONS"
FINDING_NO_ALTERNATIVE_DESIGNATED = "NO_ALTERNATIVE_DESIGNATED"
FINDING_CENTRAL_BANK_PLAN_ADVISORY = "CENTRAL_BANK_PLAN_ADVISORY"
FINDING_REGISTER_CHECK_STALE = "REGISTER_CHECK_STALE"
FINDING_REGISTER_CHECK_PREDATES_AMENDMENT = "REGISTER_CHECK_PREDATES_AMENDMENT"
FINDING_STATUTORY_REPLACEMENT_RELIED_ON = "STATUTORY_REPLACEMENT_RELIED_ON"
FINDING_OUT_OF_SCOPE_NO_USER_OBLIGATIONS = "OUT_OF_SCOPE_NO_USER_OBLIGATIONS"


# --------------------------------------------------------------------------
# Overall status
# --------------------------------------------------------------------------

STATUS_OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY = "OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY"
STATUS_OUT_OF_SCOPE_NOT_A_BMR_USE = "OUT_OF_SCOPE_NOT_A_BMR_USE"
STATUS_OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION = "OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION"
STATUS_OUT_OF_SCOPE_BENCHMARK = "OUT_OF_SCOPE_BENCHMARK"
STATUS_BMR_COMPLIANT = "BMR_COMPLIANT"
STATUS_BMR_ACTION_REQUIRED = "BMR_ACTION_REQUIRED"
STATUS_BMR_VIOLATION = "BMR_VIOLATION"

#: Months allowed by Article 29(1b) to replace a benchmark that becomes the
#: object of a public notice, or to publish a reasoned statement instead.
ARTICLE_29_1B_REPLACEMENT_MONTHS = 6

#: Firm policy default with NO regulatory basis. Article 29 requires supervised
#: entities to consult the ESMA register "regularly" without naming a period.
DEFAULT_REGISTER_CHECK_MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class BmrFinding:
    """A single obligation outcome, traceable to the article that creates it."""

    code: str
    severity: str
    article: str
    message: str


@dataclass
class BenchmarkSpec:
    """A benchmark as classified by the firm, with the evidence date attached.

    ``category`` is the firm's own classification against Article 2(1); this
    module does not derive it. Critical benchmarks come from the Commission
    implementing act, CTB/PAB labelling from the benchmark statement, and
    significance from EU-use data or a competent-authority designation. There is
    no public list of significant benchmarks that are not the object of a
    warning notice — see ``references/standards.md``.
    """

    benchmark_id: str
    benchmark_name: str
    administrator_name: str
    category: str
    administrator_on_esma_register: bool
    #: When the ESMA register was actually consulted for this administrator.
    register_status_verified_on: date
    #: Article 2(2) exemption, if the firm has concluded one applies.
    article_2_2_exemption: Optional[str] = None
    #: Publication date of an Article 24a(6) public notice, if any.
    warning_notice_published_on: Optional[date] = None
    #: End of any Article 29(1) derogation granted to avoid market disruption.
    warning_notice_derogation_until: Optional[date] = None
    #: Replacement designated under Article 23b or 23c, if one exists.
    designated_statutory_replacement: Optional[str] = None
    #: Alternative nominated in the firm's Article 28(2) plan.
    fallback_benchmark_name: Optional[str] = None


@dataclass
class StrategyBenchmarkUsage:
    """One strategy's reference to one benchmark, as the firm characterises it."""

    strategy_id: str
    strategy_name: str
    referenced_benchmark_id: str
    #: Article 3(1)(17) classification of the entity doing the referencing.
    entity_type: str
    #: Article 3(1)(7) characterisation, or ``USE_NOT_A_BMR_USE``.
    use_type: str
    #: True when a NEW reference is being added (Article 29(1) prohibition);
    #: False for an existing reference (Article 29(1b) replacement duty).
    is_new_reference: bool
    #: Article 28(2), first limb.
    has_written_fallback_plan: bool = False
    #: Article 28(2), second limb — an alternative nominated with reasons.
    designates_alternative_benchmark: bool = False
    #: Article 28(2), final limb — the plan reflected in the fallback provisions
    #: of the contract, instrument or fund documentation.
    fallback_reflected_in_contractual_terms: bool = False
    #: Article 29(1b) alternative to replacement: a reasoned statement published
    #: on the firm's website.
    replacement_statement_published: bool = False
    #: Article 29(1a) — the reference is to a designated statutory replacement.
    relies_on_designated_statutory_replacement: bool = False


@dataclass
class EuBmrAuditReport:
    """Structured outcome of one benchmark-use audit."""

    strategy_id: str
    benchmark_id: str
    benchmark_name: str
    administrator_name: str
    assessment_date: date
    compliance_status: str
    in_scope: bool
    scope_basis: str
    findings: List[BmrFinding] = field(default_factory=list)
    #: Article 29(1b) deadline, when a public notice is in force.
    replacement_deadline: Optional[date] = None
    audit_notes: str = ""

    @property
    def is_compliant(self) -> bool:
        """True when nothing above ADVISORY was found.

        Out-of-scope outcomes are compliant: the obligation never attached.
        """
        return self.compliance_status not in (
            STATUS_BMR_VIOLATION, STATUS_BMR_ACTION_REQUIRED)


def _add_months(start: date, months: int) -> date:
    """Add whole months, clamping to the last valid day of the target month."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BmrConfigurationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_plain_date(value: object, field_name: str) -> date:
    """Accept ``datetime.date`` but not ``datetime.datetime``.

    ``datetime`` subclasses ``date``, so an isinstance check alone lets one
    through and it then raises TypeError on the first comparison against a
    plain date. Rejecting it here keeps the failure at the input boundary,
    where the message can say what to do about it.
    """
    if isinstance(value, datetime) or not isinstance(value, date):
        raise BmrConfigurationError(
            f"{field_name} must be a datetime.date (not a datetime.datetime); "
            f"a benchmark obligation runs on calendar days, not instants")
    return value


class EuBmrComplianceEngine:
    """Scope-first auditor for EU BMR user obligations (Articles 28(2), 29).

    Usage::

        engine = EuBmrComplianceEngine()
        engine.register_benchmark(spec)
        report = engine.audit_strategy_bmr_compliance(usage, date(2026, 8, 24))

    The engine holds no global state; each instance owns its own registry.
    """

    def __init__(
        self,
        register_check_max_age_days: int = DEFAULT_REGISTER_CHECK_MAX_AGE_DAYS,
    ) -> None:
        if register_check_max_age_days < 0:
            raise BmrConfigurationError(
                "register_check_max_age_days must not be negative")
        self.register_check_max_age_days = register_check_max_age_days
        self.benchmarks: Dict[str, BenchmarkSpec] = {}

    # -- registry ---------------------------------------------------------

    def register_benchmark(self, spec: BenchmarkSpec) -> None:
        """Validate and store a benchmark classification.

        Raises ``BmrConfigurationError`` on an unrecognised category or
        exemption, on inconsistent warning-notice data, or on a duplicate
        ``benchmark_id`` — a silent overwrite would let two different
        classifications of the same index coexist within one audit run.
        """
        benchmark_id = _require_non_empty(spec.benchmark_id, "benchmark_id")
        _require_non_empty(spec.benchmark_name, "benchmark_name")
        _require_non_empty(spec.administrator_name, "administrator_name")

        if spec.category not in _VALID_CATEGORIES:
            raise BmrConfigurationError(
                f"Unrecognised benchmark category {spec.category!r} for "
                f"{benchmark_id!r}. Expected one of {sorted(_VALID_CATEGORIES)}.")

        if (spec.article_2_2_exemption is not None
                and spec.article_2_2_exemption not in ARTICLE_2_2_EXEMPTIONS):
            raise BmrConfigurationError(
                f"Unrecognised Article 2(2) exemption "
                f"{spec.article_2_2_exemption!r} for {benchmark_id!r}. Expected "
                f"one of {sorted(ARTICLE_2_2_EXEMPTIONS)} or None.")

        _require_plain_date(
            spec.register_status_verified_on,
            f"register_status_verified_on for {benchmark_id!r}")
        for date_field in ("warning_notice_published_on",
                           "warning_notice_derogation_until"):
            value = getattr(spec, date_field)
            if value is not None:
                _require_plain_date(value, f"{date_field} for {benchmark_id!r}")

        if (spec.warning_notice_derogation_until is not None
                and spec.warning_notice_published_on is None):
            raise BmrConfigurationError(
                f"{benchmark_id!r}: a derogation cannot be recorded without the "
                f"publication date of the Article 24a(6) notice it derogates from")

        if (spec.warning_notice_published_on is not None
                and spec.category != CATEGORY_SIGNIFICANT):
            raise BmrConfigurationError(
                f"{benchmark_id!r}: Article 24a(6) public notices apply to "
                f"significant benchmarks; category is {spec.category!r}")

        if benchmark_id in self.benchmarks:
            raise BmrConfigurationError(
                f"Benchmark id {benchmark_id!r} is already registered; "
                f"re-registering would silently replace its classification")

        self.benchmarks[benchmark_id] = spec

    # -- audit ------------------------------------------------------------

    def audit_strategy_bmr_compliance(
        self,
        usage: StrategyBenchmarkUsage,
        assessment_date: Optional[date] = None,
    ) -> EuBmrAuditReport:
        """Audit one strategy's reference to one benchmark.

        ``assessment_date`` fixes the regime: on or after 1 January 2026 the
        narrowed Article 2(1) scope applies; before it, the pre-amendment scope
        does, so a historical record is judged against the rules that were
        actually in force. It defaults to ``date.today()`` for convenience —
        pass it explicitly for reproducible audit evidence.
        """
        if assessment_date is None:
            assessment_date = date.today()
        else:
            assessment_date = _require_plain_date(
                assessment_date, "assessment_date")

        _require_non_empty(usage.strategy_id, "strategy_id")

        if usage.entity_type not in _VALID_ENTITY_TYPES:
            raise BmrConfigurationError(
                f"Unrecognised entity_type {usage.entity_type!r}. Expected one "
                f"of {sorted(_VALID_ENTITY_TYPES)}.")

        if usage.use_type not in _VALID_USE_TYPES:
            raise BmrConfigurationError(
                f"Unrecognised use_type {usage.use_type!r}. Expected one of "
                f"{sorted(_VALID_USE_TYPES)}.")

        spec = self.benchmarks.get(usage.referenced_benchmark_id)
        if spec is None:
            raise BmrConfigurationError(
                f"Benchmark id {usage.referenced_benchmark_id!r} referenced by "
                f"{usage.strategy_id!r} is not registered. Register it before "
                f"auditing; an unknown id is a data error, not a BMR finding.")

        if (usage.relies_on_designated_statutory_replacement
                and spec.designated_statutory_replacement is None):
            raise BmrConfigurationError(
                f"{usage.strategy_id!r} claims reliance on an Article 29(1a) "
                f"designated statutory replacement, but no replacement is "
                f"recorded for {spec.benchmark_id!r}")

        # -- Gate 1: Article 3(1)(17) — is the entity supervised at all?
        if usage.entity_type == ENTITY_NON_SUPERVISED:
            return self._out_of_scope_report(
                usage, spec, assessment_date,
                STATUS_OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY,
                "Entity is outside Article 3(1)(17); the Article 28(2) and 29 "
                "user obligations bind supervised entities only.",
            )

        # -- Gate 2: Article 3(1)(7) — is this activity "use of a benchmark"?
        if usage.use_type == USE_NOT_A_BMR_USE:
            return self._out_of_scope_report(
                usage, spec, assessment_date,
                STATUS_OUT_OF_SCOPE_NOT_A_BMR_USE,
                "Activity is outside the closed Article 3(1)(7) list of uses; "
                "referencing an index is not by itself 'use of a benchmark'.",
            )

        # -- Gate 3: Article 2(2) — exempt person, activity or benchmark.
        if spec.article_2_2_exemption is not None:
            exemption_findings: List[BmrFinding] = []
            if (spec.article_2_2_exemption == EXEMPTION_CENTRAL_BANK
                    and not usage.has_written_fallback_plan):
                exemption_findings.append(BmrFinding(
                    code=FINDING_CENTRAL_BANK_PLAN_ADVISORY,
                    severity=SEVERITY_ADVISORY,
                    article="ESMA Q&A on Article 2(2)(a) / Article 28(2)",
                    message=(
                        f"'{spec.benchmark_name}' is provided by an exempt "
                        f"central bank, so the BMR does not apply to it. ESMA's "
                        f"Q&A nonetheless expects supervised entities using such "
                        f"a benchmark to produce and maintain Article 28(2) "
                        f"written plans; none is recorded."),
                ))
            return self._out_of_scope_report(
                usage, spec, assessment_date,
                STATUS_OUT_OF_SCOPE_ARTICLE_2_2_EXEMPTION,
                f"Article 2(2) exemption {spec.article_2_2_exemption} applies; "
                f"the BMR does not apply to this benchmark.",
                findings=exemption_findings,
            )

        # -- Gate 4: Article 2(1) — is the benchmark in scope on this date?
        post_amendment = assessment_date >= BMR_AMENDMENT_APPLICATION_DATE
        if post_amendment and spec.category not in IN_SCOPE_CATEGORIES:
            return self._out_of_scope_report(
                usage, spec, assessment_date,
                STATUS_OUT_OF_SCOPE_BENCHMARK,
                "Benchmark is outside the Article 2(1) categories that survive "
                "Regulation (EU) 2025/914 (critical, significant, EU CTB/PAB, "
                "Annex II commodity); no Article 28(2) or 29 obligation attaches.",
                findings=[BmrFinding(
                    code=FINDING_OUT_OF_SCOPE_NO_USER_OBLIGATIONS,
                    severity=SEVERITY_INFO,
                    article="Article 2(1) as amended by Regulation (EU) 2025/914",
                    message=(
                        f"'{spec.benchmark_name}' is out of BMR scope from "
                        f"1 January 2026. Absence of its administrator from the "
                        f"ESMA register is not a violation."),
                )],
            )

        # -- In scope: evaluate the user obligations. Every limb is evaluated;
        #    the audit does not stop at the first failure.
        findings: List[BmrFinding] = []
        findings.extend(self._check_register_currency(spec, assessment_date))
        article_29_findings, deadline = self._check_article_29(
            usage, spec, assessment_date, post_amendment)
        findings.extend(article_29_findings)
        findings.extend(self._check_article_28_2(usage, spec))

        status = self._aggregate_status(findings)
        scope_basis = (
            f"In scope: category {spec.category} under Article 2(1)"
            if post_amendment else
            "In scope: assessment predates the 1 January 2026 application of "
            "Regulation (EU) 2025/914, so the wider pre-amendment scope applies")
        notes = self._summarise(usage, spec, status, findings)

        if status == STATUS_BMR_VIOLATION:
            logger.error(notes)
        elif status == STATUS_BMR_ACTION_REQUIRED:
            logger.warning(notes)
        else:
            logger.info(notes)

        return EuBmrAuditReport(
            strategy_id=usage.strategy_id,
            benchmark_id=spec.benchmark_id,
            benchmark_name=spec.benchmark_name,
            administrator_name=spec.administrator_name,
            assessment_date=assessment_date,
            compliance_status=status,
            in_scope=True,
            scope_basis=scope_basis,
            findings=findings,
            replacement_deadline=deadline,
            audit_notes=notes,
        )

    # -- obligation checks ------------------------------------------------

    def _check_register_currency(
        self, spec: BenchmarkSpec, assessment_date: date
    ) -> List[BmrFinding]:
        """Article 29: the register must be consulted regularly, not just once."""
        findings: List[BmrFinding] = []
        if spec.register_status_verified_on > assessment_date:
            raise BmrConfigurationError(
                f"{spec.benchmark_id!r}: register_status_verified_on "
                f"({spec.register_status_verified_on}) is after the assessment "
                f"date ({assessment_date})")

        age_days = (assessment_date - spec.register_status_verified_on).days
        if age_days > self.register_check_max_age_days:
            findings.append(BmrFinding(
                code=FINDING_REGISTER_CHECK_STALE,
                severity=SEVERITY_ADVISORY,
                article="Article 29 (regular consultation of the ESMA register)",
                message=(
                    f"ESMA register status for '{spec.administrator_name}' was "
                    f"last verified {age_days} days ago, beyond the configured "
                    f"{self.register_check_max_age_days}-day policy window."),
            ))

        if (assessment_date >= BMR_AMENDMENT_APPLICATION_DATE
                and spec.register_status_verified_on < BMR_AMENDMENT_APPLICATION_DATE):
            findings.append(BmrFinding(
                code=FINDING_REGISTER_CHECK_PREDATES_AMENDMENT,
                severity=SEVERITY_ADVISORY,
                article="Article 2(1) as amended; ESMA transitional statement",
                message=(
                    f"Register status was verified on "
                    f"{spec.register_status_verified_on}, before the amended BMR "
                    f"applied. The register is being re-cut through "
                    f"{BMR_REGISTER_GRANDFATHER_END_DATE}; re-verify."),
            ))
        return findings

    def _check_article_29(
        self,
        usage: StrategyBenchmarkUsage,
        spec: BenchmarkSpec,
        assessment_date: date,
        post_amendment: bool,
    ) -> Tuple[List[BmrFinding], Optional[date]]:
        """Article 29(1)/(1a)/(1b): may this reference exist, and until when?"""
        findings: List[BmrFinding] = []
        deadline: Optional[date] = None

        notice_date = spec.warning_notice_published_on
        notice_in_force = notice_date is not None and notice_date <= assessment_date
        derogation_active = (
            notice_in_force
            and spec.warning_notice_derogation_until is not None
            and assessment_date <= spec.warning_notice_derogation_until)

        if notice_in_force and notice_date is not None:
            deadline = _add_months(notice_date, ARTICLE_29_1B_REPLACEMENT_MONTHS)

        if usage.relies_on_designated_statutory_replacement:
            findings.append(BmrFinding(
                code=FINDING_STATUTORY_REPLACEMENT_RELIED_ON,
                severity=SEVERITY_INFO,
                article="Article 29(1a)",
                message=(
                    f"Reference permitted as the designated statutory "
                    f"replacement '{spec.designated_statutory_replacement}' "
                    f"under Article 23b/23c."),
            ))
            return findings, deadline

        if usage.is_new_reference:
            if notice_in_force and not derogation_active:
                findings.append(BmrFinding(
                    code=FINDING_ADDITION_PROHIBITED_WARNING_NOTICE,
                    severity=SEVERITY_VIOLATION,
                    article="Article 29(1) with Article 24a(6)",
                    message=(
                        f"'{spec.benchmark_name}' is a significant benchmark "
                        f"that is the object of a public notice published "
                        f"{notice_date}. A supervised entity must not add a new "
                        f"reference to it."),
                ))
            elif derogation_active:
                findings.append(BmrFinding(
                    code=FINDING_WARNING_NOTICE_DEROGATION_ACTIVE,
                    severity=SEVERITY_INFO,
                    article="Article 29(1)",
                    message=(
                        f"Use permitted under a derogation running to "
                        f"{spec.warning_notice_derogation_until} despite the "
                        f"public notice published {notice_date}."),
                ))

            register_required = (
                not post_amendment or spec.category in REGISTER_GATED_CATEGORIES)
            if register_required and not spec.administrator_on_esma_register:
                findings.append(BmrFinding(
                    code=FINDING_ADDITION_PROHIBITED_UNREGISTERED_ADMINISTRATOR,
                    severity=SEVERITY_VIOLATION,
                    article="Article 29(1)",
                    message=(
                        f"Administrator '{spec.administrator_name}' is not on the "
                        f"ESMA register, so a new reference to the "
                        f"{spec.category} benchmark '{spec.benchmark_name}' is "
                        f"prohibited."),
                ))
        elif notice_in_force and not derogation_active:
            # Existing reference caught by a public notice: replace within six
            # months, or publish a reasoned statement on the firm's website.
            if usage.replacement_statement_published:
                findings.append(BmrFinding(
                    code=FINDING_EXISTING_USE_REPLACEMENT_REQUIRED,
                    severity=SEVERITY_ADVISORY,
                    article="Article 29(1b)",
                    message=(
                        f"Existing reference to '{spec.benchmark_name}' remains "
                        f"under the public notice published {notice_date}; a "
                        f"reasoned statement is published, which satisfies the "
                        f"alternative limb. Replacement is still expected."),
                ))
            elif deadline is not None and assessment_date > deadline:
                findings.append(BmrFinding(
                    code=FINDING_EXISTING_USE_REPLACEMENT_OVERDUE,
                    severity=SEVERITY_VIOLATION,
                    article="Article 29(1b)",
                    message=(
                        f"The six-month window to replace '{spec.benchmark_name}' "
                        f"or publish a reasoned statement expired {deadline}."),
                ))
            else:
                findings.append(BmrFinding(
                    code=FINDING_EXISTING_USE_REPLACEMENT_REQUIRED,
                    severity=SEVERITY_ACTION_REQUIRED,
                    article="Article 29(1b)",
                    message=(
                        f"Replace '{spec.benchmark_name}' with an appropriate "
                        f"alternative by {deadline}, or publish a reasoned "
                        f"statement on the firm's website."),
                ))

        return findings, deadline

    def _check_article_28_2(
        self, usage: StrategyBenchmarkUsage, spec: BenchmarkSpec
    ) -> List[BmrFinding]:
        """Article 28(2): plan, nominated alternative, contractual fallbacks."""
        findings: List[BmrFinding] = []

        if not usage.has_written_fallback_plan:
            findings.append(BmrFinding(
                code=FINDING_MISSING_WRITTEN_PLAN,
                severity=SEVERITY_VIOLATION,
                article="Article 28(2)",
                message=(
                    f"No robust written plan is recorded for material change or "
                    f"cessation of '{spec.benchmark_name}'."),
            ))
            return findings

        if not usage.designates_alternative_benchmark or not spec.fallback_benchmark_name:
            findings.append(BmrFinding(
                code=FINDING_NO_ALTERNATIVE_DESIGNATED,
                severity=SEVERITY_ADVISORY,
                article="Article 28(2), second limb",
                message=(
                    f"The plan for '{spec.benchmark_name}' nominates no "
                    f"alternative benchmark. Article 28(2) requires one only "
                    f"'where feasible and appropriate'; record the reasoning for "
                    f"concluding it is not."),
            ))

        if not usage.fallback_reflected_in_contractual_terms:
            findings.append(BmrFinding(
                code=FINDING_MISSING_FALLBACK_PROVISIONS,
                severity=SEVERITY_VIOLATION,
                article="Article 28(2), final limb",
                message=(
                    f"The plan for '{spec.benchmark_name}' is not reflected in "
                    f"the fallback provisions of the financial contracts, "
                    f"financial instruments or fund documentation that use it."),
            ))

        return findings

    # -- assembly ---------------------------------------------------------

    @staticmethod
    def _aggregate_status(findings: List[BmrFinding]) -> str:
        severities = {f.severity for f in findings}
        if SEVERITY_VIOLATION in severities:
            return STATUS_BMR_VIOLATION
        if SEVERITY_ACTION_REQUIRED in severities:
            return STATUS_BMR_ACTION_REQUIRED
        return STATUS_BMR_COMPLIANT

    @staticmethod
    def _summarise(
        usage: StrategyBenchmarkUsage,
        spec: BenchmarkSpec,
        status: str,
        findings: List[BmrFinding],
    ) -> str:
        codes = ", ".join(f.code for f in findings) or "no findings"
        return (f"{status} [{usage.strategy_id}] benchmark "
                f"'{spec.benchmark_name}' ({spec.category}, administrator "
                f"'{spec.administrator_name}'): {codes}")

    def _out_of_scope_report(
        self,
        usage: StrategyBenchmarkUsage,
        spec: BenchmarkSpec,
        assessment_date: date,
        status: str,
        scope_basis: str,
        findings: Optional[List[BmrFinding]] = None,
    ) -> EuBmrAuditReport:
        findings = findings if findings is not None else []
        notes = (f"{status} [{usage.strategy_id}] '{spec.benchmark_name}': "
                 f"{scope_basis}")
        logger.info(notes)
        return EuBmrAuditReport(
            strategy_id=usage.strategy_id,
            benchmark_id=spec.benchmark_id,
            benchmark_name=spec.benchmark_name,
            administrator_name=spec.administrator_name,
            assessment_date=assessment_date,
            compliance_status=status,
            in_scope=False,
            scope_basis=scope_basis,
            findings=findings,
            replacement_deadline=None,
            audit_notes=notes,
        )

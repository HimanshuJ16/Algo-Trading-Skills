"""Annual compliance attestation gate for US registered investment advisers and
broker-dealers.

The engine evaluates ONE legal entity's evidence against four US obligations and
maps every block onto the provision that requires it:

* ``17 CFR 275.206(4)-7(b)`` -- an adviser must "review, no less frequently than
  annually, the adequacy of the policies and procedures ... and the effectiveness
  of their implementation".  The rule text carries **no** writing mandate: the
  2023 amendment that would have required the review to be documented in writing
  was vacated with the rest of the Private Fund Adviser Rules (Nat'l Ass'n of
  Private Fund Managers v. SEC, 5th Cir., 5 Jun 2024).  The separate
  documentation gate here is anchored on ``17 CFR 275.204-2(a)(17)(ii)``, the
  books-and-records provision covering "any records documenting the investment
  adviser's annual review of those policies and procedures conducted pursuant to
  Sec. 275.206(4)-7(b)".
* ``FINRA Rule 3130(b)`` / ``(c)(2)`` -- the CEO certifies annually, and has
  "conducted one or more meetings with the chief compliance officer(s) in the
  preceding 12 months".  Footnote 1 to paragraph (b) requires that "each ensuing
  annual certification is effected no later than on the anniversary date of the
  previous year's certification" -- a constraint on the **certification**, not on
  the meeting.
* ``FINRA Rule 3130(c)(3)`` -- the final report evidencing the processes is
  submitted to the board and audit committee "at the earlier of their next
  scheduled meetings or within 45 days of the date of execution of this
  certification".  (This is the certification text itself.  Supplementary
  Material .04 is "Content of Meetings Between Chief Executive Officer and Chief
  Compliance Officer" and says nothing about board submission.)
* ``FINRA Rule 3120(a)`` -- designated principals test and verify the WSPs and
  submit a report to senior management "no less than annually".
* ``17 CFR 240.15c3-5(e)(1)`` -- a broker-dealer **with market access** reviews
  its market-access business activity and the effectiveness of its controls no
  less frequently than annually; ``(e)(2)`` -- the CEO certifies annually that
  those controls comply with (b) and (c).  Paragraph (d)(2) is the
  allocation-non-relief clause and is not the source of either obligation.

Three conventions are applied uniformly:

* **Deadlines compare calendar dates, not timestamps.**  Every deadline above is
  expressed in days, months or anniversaries, so the time of day carries no
  regulatory meaning.  Comparing full datetimes would block a certification
  executed at 16:30 on its anniversary because last year's was signed at 16:00.
  Evidence is stored as ``datetime`` for the audit trail; the window checks read
  ``.date()``.  For tz-aware evidence this is the recorded local date of each
  event.
* **Boundary dates are inclusive.**  A meeting falling exactly 12 months before
  the execution date is "in the preceding 12 months"; a certification effected
  exactly on the prior anniversary is "no later than" it; a board submission
  exactly 45 days after execution is within 45 days.
* **The verdict never reads the wall clock.**  Every window is anchored on the
  certification execution date, or on an explicit ``as_of`` passed to
  :meth:`AnnualComplianceAttestationEngine.evaluate`.  With no anchor available
  the engine blocks rather than guessing, so the same checklist always produces
  the same verdict and an archived report can be recomputed years later.

Scope: United States only, and a periodic governance gate rather than a live
control.  A ``True`` verdict is necessary but not sufficient -- see the nine
review dimensions in ``references/standards.md``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "AnnualComplianceChecklist",
    "AttestationReport",
    "AnnualComplianceAttestationEngine",
]


# Stable, machine-readable requirement codes (parallel to human-readable messages).
# Each code names the provision that the block traces to.
CODE_SEC_206_4_7_POLICY_REVIEW = "REQ_SEC_206_4_7_POLICY_REVIEW"
CODE_SEC_204_2_REVIEW_RECORD = "REQ_SEC_204_2_A17_ANNUAL_REVIEW_RECORD"
CODE_SEC_15C3_5_ANNUAL_REVIEW = "REQ_SEC_15C3_5_ANNUAL_REVIEW"
CODE_SEC_15C3_5_CEO_CERT = "REQ_SEC_15C3_5_CEO_CERT"
CODE_FINRA_3130_CEO_CCO_MEETING = "REQ_FINRA_3130_CEO_CCO_MEETING"
CODE_FINRA_3130_CEO_CERT = "REQ_FINRA_3130_CEO_CERT"
CODE_FINRA_3130_CERT_ANNIVERSARY = "REQ_FINRA_3130_CERT_ANNIVERSARY"
CODE_FINRA_3130_MEETING_ORDER = "REQ_FINRA_3130_MEETING_PRECEDES_CERT"
CODE_FINRA_3130_BOARD_SUBMISSION = "REQ_FINRA_3130_C3_BOARD_SUBMISSION"
CODE_FINRA_3120_REPORT = "REQ_FINRA_3120_REPORT"
CODE_QUANT_ALGO_CODE_REVIEW = "REQ_QUANT_ALGO_CODE_INTEGRITY_REVIEW"
CODE_QUANT_TRADE_SURVEILLANCE = "REQ_QUANT_TRADE_SURVEILLANCE_TEST"

# FINRA Rule 3130(c)(3): the final report goes to the board of directors and audit
# committee at the earlier of their next scheduled meetings OR within 45 days of the
# date of execution of the certification. The engine models only the 45-day limb --
# it has no board calendar -- so passing this check is an upper bound, not proof of
# compliance. See references/standards.md.
BOARD_SUBMISSION_MAX_DAYS = 45

# Every date field, in declaration order.
_DATE_FIELDS = (
    "annual_policy_review_date",
    "annual_review_documentation_date",
    "ceo_cco_meeting_date",
    "ceo_certification_signed_date",
    "prior_certification_date",
    "certification_signing_date",
    "board_submission_date",
    "audit_committee_acknowledgment_date",
    "rule_3120_report_date",
    "rule_15c3_5_annual_review_date",
    "rule_15c3_5_ceo_certification_date",
    "algo_code_integrity_review_date",
    "trade_surveillance_test_date",
)


def _shift_years(moment: datetime, years: int) -> datetime:
    """Return ``moment`` shifted by whole calendar years.

    Calendar arithmetic, not ``timedelta(days=365)``: the anniversary of
    2023-03-01 is 2024-03-01, whereas 365 days later lands on 2024-02-29 because
    2024 carries a leap day. A one-day error in either direction on a regulatory
    deadline is the difference between a pass and a violation. 29 February is
    mapped to 28 February in a non-leap target year, the convention applied to
    anniversary dates.
    """
    try:
        return moment.replace(year=moment.year + years)
    except ValueError:
        return moment.replace(year=moment.year + years, month=2, day=28)


@dataclasses.dataclass(frozen=True)
class AnnualComplianceChecklist:
    """Audit evidence for ONE legal entity's annual compliance attestation.

    A single instance corresponds to one legal entity (the RIA and its BD
    affiliate have separate certifications, anniversaries and obligations). A
    typical quant fund structure (RIA + BD affiliate + offshore feeder) requires
    one checklist per entity.

    Every supplied date must be a :class:`datetime.datetime` sourced from a
    tamper-evident recordkeeping system. All supplied datetimes must agree on
    timezone awareness (all naive or all aware); mixing them is rejected at
    construction rather than raising ``TypeError`` mid-evaluation.
    """

    reporting_year: int
    is_broker_dealer: bool
    legal_entity_id: str

    # SEC Rule 206(4)-7(b) -- annual REVIEW of the adequacy of written policies
    # and procedures and the effectiveness of their implementation. The rule
    # itself mandates no writing (see module docstring); the record of the review
    # is required by Rule 204-2(a)(17)(ii).
    annual_policy_review_date: Optional[datetime] = None
    annual_review_documentation_date: Optional[datetime] = None

    # FINRA Rule 3130 (broker-dealers only).
    # ``certification_signing_date`` is the date of EXECUTION of the certification
    # and anchors every rolling window below. ``ceo_certification_signed_date`` is
    # the recorded evidence that the CEO signed. They describe the same event and
    # should carry the same value; if they differ the execution date governs and
    # the engine logs a warning.
    ceo_cco_meeting_date: Optional[datetime] = None
    ceo_certification_signed_date: Optional[datetime] = None
    prior_certification_date: Optional[datetime] = None
    certification_signing_date: Optional[datetime] = None
    # FINRA Rule 3130(c)(3) -- board / audit-committee submission of the final report.
    board_submission_date: Optional[datetime] = None
    audit_committee_acknowledgment_date: Optional[datetime] = None

    # FINRA Rule 3120(a) -- supervisory-controls report to senior management.
    rule_3120_report_date: Optional[datetime] = None

    # SEC Rule 15c3-5(e)(1) annual review + (e)(2) CEO certification of the
    # market-access risk controls. Applies only to a BD with market access.
    rule_15c3_5_annual_review_date: Optional[datetime] = None
    rule_15c3_5_ceo_certification_date: Optional[datetime] = None

    # Quantitative fund specifics (SEC/FINRA exam expectations, not a named rule).
    algo_code_integrity_review_date: Optional[datetime] = None
    trade_surveillance_test_date: Optional[datetime] = None

    # Scope qualifier for the SEC Rule 15c3-5 group above. Rule 15c3-5(b) binds a
    # broker-dealer "with market access, or that provides a customer or any other
    # person with access to an exchange or alternative trading system through use
    # of its market participant identifier or otherwise". A BD without market
    # access has no 15c3-5 obligation and must not be blocked on one. Declared
    # last so existing positional construction is unaffected.
    has_market_access: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.reporting_year, int) or isinstance(self.reporting_year, bool):
            raise ValueError(
                f"reporting_year must be an int in [2000, 2100]; got {self.reporting_year!r}"
            )
        if self.reporting_year < 2000 or self.reporting_year > 2100:
            raise ValueError(
                f"reporting_year must be in [2000, 2100]; got {self.reporting_year}"
            )
        if not isinstance(self.legal_entity_id, str) or not self.legal_entity_id.strip():
            raise ValueError("legal_entity_id must be a non-empty string")
        for flag in ("is_broker_dealer", "has_market_access"):
            if not isinstance(getattr(self, flag), bool):
                raise ValueError(f"{flag} must be a bool; got {getattr(self, flag)!r}")

        # Reject non-datetime evidence up front. ``datetime.date`` exposes ``.year``
        # and would silently satisfy the calendar-year checks, then raise TypeError
        # on the first ordering comparison.
        awareness: Optional[bool] = None
        for name in _DATE_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, datetime):
                raise ValueError(
                    f"{name} must be a datetime.datetime or None; got {value!r}"
                )
            is_aware = value.tzinfo is not None and value.utcoffset() is not None
            if awareness is None:
                awareness = is_aware
            elif awareness != is_aware:
                raise ValueError(
                    "all checklist datetimes must be consistently timezone-aware or "
                    f"consistently naive; {name} disagrees with the earlier fields"
                )

        # A "prior" certification that post-dates this cycle's certification is
        # transposed evidence, not a rule breach: the anniversary deadline it
        # implies would sit in the future and every gate would pass silently.
        # Reject it at construction rather than issue a meaningless seal.
        if self.prior_certification_date is not None:
            for name in ("certification_signing_date", "ceo_certification_signed_date"):
                current = getattr(self, name)
                if current is not None and self.prior_certification_date.date() >= current.date():
                    raise ValueError(
                        f"prior_certification_date must precede {name}; got "
                        f"{self.prior_certification_date.isoformat()} >= {current.isoformat()}"
                    )

    @property
    def is_timezone_aware(self) -> bool:
        """True when the supplied datetimes carry a UTC offset."""
        for name in _DATE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                return value.tzinfo is not None and value.utcoffset() is not None
        return False


@dataclasses.dataclass(frozen=True)
class AttestationReport:
    """Sealed verdict over an evaluated checklist.

    ``content_hash`` binds the full evidence set, the verdict and
    ``generated_at``. It is *tamper-evident*, not tamper-proof: the two list
    fields remain mutable, so re-verify an archived report with
    :meth:`AnnualComplianceAttestationEngine.verify_report` before relying on it.
    """

    is_ready_for_attestation: bool
    missing_requirements: List[str]
    missing_requirement_codes: List[str]
    generated_at: datetime
    content_hash: str


class AnnualComplianceAttestationEngine:
    """Verifies that one legal entity has met the prerequisites to sign its
    annual compliance attestation.

    The engine is stateless and deterministic: the same checklist and ``as_of``
    always produce the same verdict and codes.
    """

    def evaluate(
        self,
        checklist: AnnualComplianceChecklist,
        as_of: Optional[datetime] = None,
    ) -> AttestationReport:
        """Evaluate ``checklist`` and return a sealed :class:`AttestationReport`.

        ``as_of`` is a fallback anchor for the rolling windows, used only when
        the checklist carries neither ``certification_signing_date`` nor
        ``ceo_certification_signed_date``. It must match the checklist's timezone
        awareness. Omit it and an unanchored broker-dealer checklist blocks --
        the engine will not substitute the wall clock for evidence.
        """
        if as_of is not None:
            if not isinstance(as_of, datetime):
                raise ValueError(f"as_of must be a datetime.datetime or None; got {as_of!r}")
            as_of_aware = as_of.tzinfo is not None and as_of.utcoffset() is not None
            if as_of_aware != checklist.is_timezone_aware:
                raise ValueError(
                    "as_of timezone awareness must match the checklist's datetimes"
                )

        missing: List[str] = []
        codes: List[str] = []
        year = checklist.reporting_year

        def add(code: str, message: str) -> None:
            codes.append(code)
            missing.append(message)

        # 1. SEC Rule 206(4)-7(b) -- applies to every registered investment adviser.
        if (
            checklist.annual_policy_review_date is None
            or checklist.annual_policy_review_date.year != year
        ):
            add(
                CODE_SEC_206_4_7_POLICY_REVIEW,
                "SEC Rule 206(4)-7(b): Annual review of the adequacy of written "
                "policies and procedures was not completed this year.",
            )

        # Distinct code: the record of the review is a Rule 204-2(a)(17)(ii)
        # books-and-records obligation, not a 206(4)-7 writing mandate (that
        # amendment was vacated in 2024). A separate code lets downstream routing
        # tell "the review never happened" from "the review is undocumented".
        if (
            checklist.annual_review_documentation_date is None
            or checklist.annual_review_documentation_date.year != year
        ):
            add(
                CODE_SEC_204_2_REVIEW_RECORD,
                "SEC Rule 204-2(a)(17)(ii): No record documenting this year's "
                "SEC Rule 206(4)-7(b) annual review was produced.",
            )

        # 2. Quant-specific controls (SEC/FINRA exam expectations).
        if (
            checklist.algo_code_integrity_review_date is None
            or checklist.algo_code_integrity_review_date.year != year
        ):
            add(
                CODE_QUANT_ALGO_CODE_REVIEW,
                "Quant Control: Annual algorithmic code integrity risk review is missing.",
            )

        if (
            checklist.trade_surveillance_test_date is None
            or checklist.trade_surveillance_test_date.year != year
        ):
            add(
                CODE_QUANT_TRADE_SURVEILLANCE,
                "Quant Control: Annual trade surveillance systems test is missing.",
            )

        # 3. Broker-dealer gates.
        if checklist.is_broker_dealer:
            anchor = self._execution_anchor(checklist, as_of)
            self._evaluate_finra_3130(checklist, year, anchor, add)
            self._evaluate_finra_3120(checklist, anchor, add)
            if checklist.has_market_access:
                self._evaluate_sec_15c3_5(checklist, year, add)

        is_ready = len(missing) == 0
        generated_at = datetime.now(timezone.utc)
        content_hash = self._compute_content_hash(
            checklist, is_ready, missing, codes, generated_at
        )

        if is_ready:
            logger.info(
                "Entity %s year %s attestation ready. All regulatory controls passed.",
                checklist.legal_entity_id,
                year,
            )
        else:
            logger.warning(
                "Entity %s year %s attestation blocked. Missing requirements: %s",
                checklist.legal_entity_id,
                year,
                missing,
            )
        logger.info("Attestation content_hash: %s", content_hash)

        return AttestationReport(
            is_ready_for_attestation=is_ready,
            missing_requirements=missing,
            missing_requirement_codes=codes,
            generated_at=generated_at,
            content_hash=content_hash,
        )

    # --- anchoring ----------------------------------------------------------

    @staticmethod
    def _execution_anchor(
        checklist: AnnualComplianceChecklist,
        as_of: Optional[datetime],
    ) -> Optional[datetime]:
        """Resolve the certification execution date that anchors every window.

        Precedence: ``certification_signing_date`` (the date of execution named
        by FINRA Rule 3130(c)(3)), then ``ceo_certification_signed_date``, then
        the caller-supplied ``as_of``. Returns ``None`` when none is available,
        which the callers treat as "unevaluable, therefore blocked".
        """
        signing = checklist.certification_signing_date
        signed = checklist.ceo_certification_signed_date
        if signing is not None and signed is not None and signing != signed:
            logger.warning(
                "Entity %s: certification_signing_date (%s) and "
                "ceo_certification_signed_date (%s) disagree; the execution date "
                "governs the rolling windows.",
                checklist.legal_entity_id,
                signing.isoformat(),
                signed.isoformat(),
            )
        if signing is not None:
            return signing
        if signed is not None:
            return signed
        return as_of

    # --- BD-gated rule helpers ---------------------------------------------

    def _evaluate_finra_3130(
        self,
        checklist: AnnualComplianceChecklist,
        year: int,
        anchor: Optional[datetime],
        add: Callable[[str, str], None],
    ) -> None:
        # FINRA Rule 3130(c)(2): the CEO has "conducted one or more meetings with
        # the chief compliance officer(s) in the preceding 12 months" -- measured
        # back from the date the certification is executed. Inclusive at both ends.
        if anchor is None:
            add(
                CODE_FINRA_3130_CEO_CCO_MEETING,
                "FINRA Rule 3130(c)(2): The CEO-CCO meeting window cannot be "
                "evaluated -- no certification execution date was supplied.",
            )
        elif checklist.ceo_cco_meeting_date is None:
            add(
                CODE_FINRA_3130_CEO_CCO_MEETING,
                "FINRA Rule 3130(c)(2): No CEO-CCO compliance meeting is recorded "
                "in the 12 months preceding the certification execution date.",
            )
        else:
            meeting = checklist.ceo_cco_meeting_date
            window_start = _shift_years(anchor, -1)
            if meeting.date() < window_start.date() or meeting.date() > anchor.date():
                add(
                    CODE_FINRA_3130_CEO_CCO_MEETING,
                    "FINRA Rule 3130(c)(2): CEO-CCO compliance meeting did not occur "
                    "within the 12 months preceding the certification execution date.",
                )

        # FINRA Rule 3130(b), footnote 1: "Members must ensure that each ensuing
        # annual certification is effected no later than on the anniversary date
        # of the previous year's certification." This constrains the CERTIFICATION,
        # not the meeting -- a compliant meeting inside a late certification cycle
        # is not itself the violation.
        if checklist.prior_certification_date is not None and anchor is not None:
            deadline = _shift_years(checklist.prior_certification_date, 1)
            if anchor.date() > deadline.date():
                add(
                    CODE_FINRA_3130_CERT_ANNIVERSARY,
                    "FINRA Rule 3130(b) fn.1: Certification was executed after the "
                    "anniversary of the prior year's certification.",
                )

        # The certification must be signed and dated in the reporting year.
        if (
            checklist.ceo_certification_signed_date is None
            or checklist.ceo_certification_signed_date.year != year
        ):
            add(
                CODE_FINRA_3130_CEO_CERT,
                "FINRA Rule 3130(b): CEO has not signed the annual compliance "
                "certification this year.",
            )

        # Temporal ordering: the meeting attested to in 3130(c)(2) must have
        # happened before the CEO attests to it (guards against rubber-stamping).
        if (
            checklist.ceo_cco_meeting_date is not None
            and checklist.ceo_certification_signed_date is not None
            and checklist.ceo_cco_meeting_date.date()
            > checklist.ceo_certification_signed_date.date()
        ):
            add(
                CODE_FINRA_3130_MEETING_ORDER,
                "FINRA Rule 3130(c)(2): CEO-CCO meeting post-dates the CEO "
                "certification signature (rubber-stamping risk).",
            )

        # FINRA Rule 3130(c)(3): the final report goes to the board and audit
        # committee at the earlier of their next scheduled meetings or within 45
        # days of execution. Only the 45-day limb is modelled. Submission BEFORE
        # execution is expressly permitted ("has been submitted ... or will be
        # submitted"), so no lower bound is imposed.
        if anchor is not None:
            deadline = anchor + timedelta(days=BOARD_SUBMISSION_MAX_DAYS)
            if checklist.board_submission_date is None:
                add(
                    CODE_FINRA_3130_BOARD_SUBMISSION,
                    "FINRA Rule 3130(c)(3): Final report was not submitted to the "
                    "board/audit committee within 45 days of certification execution.",
                )
            elif checklist.board_submission_date.date() > deadline.date():
                add(
                    CODE_FINRA_3130_BOARD_SUBMISSION,
                    "FINRA Rule 3130(c)(3): Board/audit-committee submission exceeded "
                    "the 45-day deadline after certification execution.",
                )

    def _evaluate_finra_3120(
        self,
        checklist: AnnualComplianceChecklist,
        anchor: Optional[datetime],
        add: Callable[[str, str], None],
    ) -> None:
        # FINRA Rule 3120(a): designated principals test and verify the WSPs and
        # submit a report to senior management "no less than annually". The rule
        # sets no relationship to the 3130 cycle; anchoring the 3120 window on the
        # certification execution date is this engine's tightening, so that the
        # 3130 certification rests on 3120 testing no older than 12 months.
        if anchor is None:
            add(
                CODE_FINRA_3120_REPORT,
                "FINRA Rule 3120(a): The supervisory-controls report window cannot "
                "be evaluated -- no certification execution date was supplied.",
            )
            return
        window_start = _shift_years(anchor, -1)
        if (
            checklist.rule_3120_report_date is None
            or checklist.rule_3120_report_date.date() < window_start.date()
            or checklist.rule_3120_report_date.date() > anchor.date()
        ):
            add(
                CODE_FINRA_3120_REPORT,
                "FINRA Rule 3120(a): Supervisory-controls report to senior management "
                "was not produced within the 12 months preceding certification.",
            )

    def _evaluate_sec_15c3_5(
        self,
        checklist: AnnualComplianceChecklist,
        year: int,
        add: Callable[[str, str], None],
    ) -> None:
        # SEC Rule 15c3-5(e)(1): review the market-access business activity and the
        # effectiveness of the controls no less frequently than annually.
        if (
            checklist.rule_15c3_5_annual_review_date is None
            or checklist.rule_15c3_5_annual_review_date.year != year
        ):
            add(
                CODE_SEC_15C3_5_ANNUAL_REVIEW,
                "SEC Rule 15c3-5(e)(1): Annual review of market-access risk controls "
                "was not completed this year.",
            )
        # SEC Rule 15c3-5(e)(2): the CEO certifies annually that the controls comply
        # with paragraphs (b) and (c) -- a separate act from the (e)(1) review.
        if (
            checklist.rule_15c3_5_ceo_certification_date is None
            or checklist.rule_15c3_5_ceo_certification_date.year != year
        ):
            add(
                CODE_SEC_15C3_5_CEO_CERT,
                "SEC Rule 15c3-5(e)(2): CEO certification of market-access risk-control "
                "compliance was not signed this year.",
            )

    # --- Sealing ------------------------------------------------------------

    @staticmethod
    def _serialize(
        checklist: AnnualComplianceChecklist,
        is_ready: bool,
        missing: List[str],
        codes: List[str],
        generated_at: datetime,
    ) -> str:
        """Canonical JSON binding the FULL evidence set to the verdict.

        Every checklist field is included. Hashing only the verdict would let the
        underlying dates be swapped while the seal still verified -- exactly the
        substitution this skill exists to make detectable.
        """
        evidence: Dict[str, Any] = {}
        for field in dataclasses.fields(checklist):
            value = getattr(checklist, field.name)
            evidence[field.name] = value.isoformat() if isinstance(value, datetime) else value
        payload = {
            "evidence": evidence,
            "is_ready_for_attestation": is_ready,
            "missing_requirements": list(missing),
            "missing_requirement_codes": list(codes),
            "generated_at": generated_at.isoformat(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _compute_content_hash(
        cls,
        checklist: AnnualComplianceChecklist,
        is_ready: bool,
        missing: List[str],
        codes: List[str],
        generated_at: datetime,
    ) -> str:
        serialized = cls._serialize(checklist, is_ready, missing, codes, generated_at)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def verify_report(
        cls,
        checklist: AnnualComplianceChecklist,
        report: AttestationReport,
    ) -> bool:
        """Recompute the seal and report whether ``report`` still matches ``checklist``.

        Returns ``False`` if any evidence date, the verdict, either list, or
        ``generated_at`` has been altered since the report was issued. Run this
        against an archived report before relying on it in an examination.
        """
        recomputed = cls._compute_content_hash(
            checklist,
            report.is_ready_for_attestation,
            report.missing_requirements,
            report.missing_requirement_codes,
            report.generated_at,
        )
        if recomputed != report.content_hash:
            logger.warning(
                "Attestation seal MISMATCH for entity %s: stored=%s recomputed=%s",
                checklist.legal_entity_id,
                report.content_hash,
                recomputed,
            )
            return False
        return True

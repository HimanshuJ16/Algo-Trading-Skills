"""Trade record-keeping compliance engine for US federal tax audit defense.

Jurisdiction
------------
This module encodes **US federal income tax** record-keeping rules only. It is
scoped to IRC Sec. 6001 / Treas. Reg. Sec. 1.6001-1 (duty to keep records),
IRC Sec. 1222 (short- vs long-term holding period), IRC Sec. 1091 (wash sales),
Treas. Reg. Sec. 1.1012-1(c) (adequate identification of lots) and
IRC Sec. 475(f) (trader mark-to-market election). It does NOT encode HMRC, CRA
or any other national tax regime, and it is not a substitute for professional
tax advice.

Retention model (important)
---------------------------
There is no blanket "7-year IRS record retention rule". The IRS assessment
period is generally **3 years** from filing (IRC Sec. 6501(a)); **6 years**
where more than 25% of gross income is omitted (Sec. 6501(e)); **unlimited**
where no return or a fraudulent return is filed (Sec. 6501(c)). **7 years**
applies specifically where a loss from worthless securities or a bad-debt
deduction is claimed (see IRC Sec. 6511(d)(1)).

Critically, for property (including securities) the IRS instruction is to keep
records "until the period of limitations expires for the year in which you
dispose of the property". The retention clock therefore starts at **disposal**,
not at acquisition. An acquisition record supporting the basis of a still-open
position is never purgeable, however old it is. This engine refuses to compute
a purge date for any record whose disposal date is unknown.

The ``retention_years`` default of 7 in this module is a conservative firm
*policy* default that covers the worthless-securities case; it is not asserted
here as a statutory minimum.

SEC Rule 17a-4 (3- or 6-year preservation periods) is cited in some retention
policies but binds **broker-dealers registered under the Exchange Act**, not
taxpayers generally. Do not apply it to an entity that is not a broker-dealer.

Sources (verified 2026-08):
  - IRS, "How long should I keep records?" (irs.gov)
  - IRS Publication 550, Holding Period / Wash Sales
  - IRS Tax Topic 429, Traders in Securities (Sec. 475(f) mark-to-market)
  - IRS Rev. Proc. 98-25 (machine-sensible ADP records, IRC Sec. 6001)
  - Treas. Reg. Sec. 1.1012-1(c); SEC Rule 15c6-1 (T+1 since 2024-05-28)
"""

from __future__ import annotations

import datetime
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Union

logger = logging.getLogger(__name__)

DateLike = Union[str, datetime.date, None]

#: Fields every trade record must carry to substantiate a return position.
MANDATORY_FIELDS: Sequence[str] = (
    "trade_id", "symbol", "side", "quantity", "price", "trade_date", "cost_basis_usd",
)

#: Conservative firm policy default (covers the worthless-securities 7-year case).
#: NOT a statutory minimum -- see module docstring.
DEFAULT_RETENTION_YEARS = 7

#: IRC Sec. 1091 replacement window on either side of the loss sale.
WASH_SALE_WINDOW_DAYS = 30

#: Treas. Reg. Sec. 1.1012-1(c)(1): adequate identification must be made no later
#: than the earlier of the settlement date or the Rule 15c6-1 settlement time,
#: which has been T+1 for most US securities since 2024-05-28.
DEFAULT_SPECIFIC_ID_DEADLINE_BUSINESS_DAYS = 1

VALID_SIDES = ("BUY", "SELL")
VALID_LOT_METHODS = ("FIFO", "LIFO", "SPECIFIC_ID", "AVERAGE_COST")

STATUS_COMPLIANT = "AUDIT_COMPLIANT"
STATUS_ADVISORY_ONLY = "AUDIT_ADVISORY_ONLY"
STATUS_ISSUES_FOUND = "AUDIT_ISSUES_FOUND"


class _StrEnum(str, Enum):
    """String enum whose members render as their bare value.

    Without this, ``f"{IssueType.MISSING_FIELD}"`` renders as
    ``IssueType.MISSING_FIELD`` on some Python versions, which would corrupt
    any compliance report built by interpolating an issue type. Equality with
    the plain string is preserved either way.
    """

    __str__ = str.__str__
    __format__ = str.__format__


class RecordKeepingError(ValueError):
    """Raised on engine misconfiguration (not on defective trade records)."""


class AccountingMethod(_StrEnum):
    """Tax accounting method governing which checks apply to a record."""

    #: Ordinary capital account: holding period and wash sale rules apply.
    CAPITAL = "CAPITAL"
    #: Valid IRC Sec. 475(f) trader mark-to-market election. Per IRS Topic 429 the
    #: wash sale rules and the capital short/long distinction do not apply.
    MTM_475F = "MTM_475F"


class HoldingPeriod(_StrEnum):
    SHORT_TERM = "SHORT_TERM"
    LONG_TERM = "LONG_TERM"
    #: Day count alone cannot decide -- see ``classify_holding_period``.
    AMBIGUOUS = "AMBIGUOUS"
    #: Neither dates nor a day count were supplied. Note that securities under a
    #: Sec. 475(f) election are never classified at all: their gains and losses
    #: are ordinary, so the engine skips this step rather than reporting a value.
    UNKNOWN = "UNKNOWN"


class Severity(_StrEnum):
    #: The record is defective and will not stand up in an examination.
    DEFECT = "DEFECT"
    #: The record is not defective but a determination is not yet final.
    ADVISORY = "ADVISORY"


class IssueType(_StrEnum):
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD = "INVALID_FIELD"
    DUPLICATE_TRADE_ID = "DUPLICATE_TRADE_ID"
    WASH_SALE_UNSET = "WASH_SALE_UNSET"
    WASH_SALE_WINDOW_OPEN = "WASH_SALE_WINDOW_OPEN"
    HOLDING_PERIOD_AMBIGUOUS = "HOLDING_PERIOD_AMBIGUOUS"
    LOT_ID_UNSUBSTANTIATED = "LOT_ID_UNSUBSTANTIATED"
    MTM_IDENTIFICATION_MISSING = "MTM_IDENTIFICATION_MISSING"
    RETENTION_INDETERMINATE = "RETENTION_INDETERMINATE"
    RETENTION_AT_RISK = "RETENTION_AT_RISK"


@dataclass
class Record:
    """Legacy value record retained for backward compatibility."""

    id: str
    value: float


@dataclass
class TradeRecord:
    """A single trade record as it would be produced for an audit response."""

    trade_id: str
    symbol: str
    side: str                                   # 'BUY' or 'SELL'
    quantity: float
    price: float
    trade_date: DateLike                        # ISO string or datetime.date
    cost_basis_usd: Optional[float] = None
    proceeds_usd: Optional[float] = None
    holding_period_days: Optional[int] = None
    wash_sale_flag: Optional[bool] = None
    lot_method: str = "FIFO"

    # --- fields added to make the documented checks actually decidable ---

    #: Acquisition date of the lot being disposed of. Preferred over
    #: ``holding_period_days``: the IRC Sec. 1222 test is calendar-based.
    acquisition_date: DateLike = None
    #: Date the position was disposed of. Starts the retention clock. For a SELL
    #: this defaults to ``trade_date``; for a BUY it must be supplied once the
    #: lot is closed, otherwise the record is not purgeable.
    disposal_date: DateLike = None
    #: Date the specific lot was identified to the broker. Required to sustain a
    #: ``SPECIFIC_ID`` lot method (Treas. Reg. Sec. 1.1012-1(c)(1)).
    lot_identification_date: DateLike = None
    #: Per-record override of the engine-level accounting method.
    accounting_method: Optional[AccountingMethod] = None
    #: True where the security is held for investment rather than in the trading
    #: business. Under Sec. 475(f) these must be identified in the records on the
    #: day acquired (IRS Topic 429).
    held_for_investment: bool = False
    #: Date the investment-security identification was recorded.
    investment_identification_date: DateLike = None
    #: True where a purge/deletion of this record has been proposed.
    purge_pending: bool = False
    #: True where an examination, litigation or preservation hold is in force.
    #: A held record is never purge-eligible regardless of age.
    legal_hold: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Full record export. Dates are normalised to ISO strings where parseable."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "trade_date": _iso(self.trade_date),
            "cost_basis_usd": self.cost_basis_usd,
            "proceeds_usd": self.proceeds_usd,
            "holding_period_days": self.holding_period_days,
            "wash_sale_flag": self.wash_sale_flag,
            "lot_method": self.lot_method,
            "acquisition_date": _iso(self.acquisition_date),
            "disposal_date": _iso(self.disposal_date),
            "lot_identification_date": _iso(self.lot_identification_date),
            "held_for_investment": self.held_for_investment,
            "investment_identification_date": _iso(self.investment_identification_date),
            "purge_pending": self.purge_pending,
            "legal_hold": self.legal_hold,
        }


@dataclass
class RecordIssue:
    trade_id: str
    issue_type: str
    detail: str
    severity: Severity = Severity.DEFECT


@dataclass
class RetentionAssessment:
    """Per-record purge-eligibility assessment."""

    trade_id: str
    #: None where the disposal date is unknown -- the clock has not started.
    earliest_purge_date: Optional[datetime.date]
    purge_eligible: bool
    rationale: str


@dataclass
class TaxAuditComplianceReport:
    total_records: int
    complete_records: int
    incomplete_records: int
    short_term_trades: int
    long_term_trades: int
    wash_sale_flagged: int
    issues: List[RecordIssue]
    status: str
    audit_notes: str
    # Fields appended (all defaulted) so existing positional construction still works.
    as_of: Optional[datetime.date] = None
    ambiguous_holding_period_trades: int = 0
    mtm_trades: int = 0
    retention: List[RetentionAssessment] = field(default_factory=list)
    purge_eligible_records: int = 0
    retention_indeterminate_records: int = 0
    defect_count: int = 0
    advisory_count: int = 0


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def _iso(value: DateLike) -> Optional[str]:
    """Best-effort ISO rendering; unparseable input is echoed unchanged."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def parse_date(value: DateLike) -> Optional[datetime.date]:
    """Parse an ISO-8601 date. Returns ``None`` for missing or malformed input.

    Malformed dates are surfaced as ``INVALID_FIELD`` issues by the auditor
    rather than raised, so that one bad record cannot abort an audit run.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def one_year_anniversary(acquired: datetime.date) -> datetime.date:
    """Date exactly one year after acquisition for IRC Sec. 1222 purposes.

    Per IRS Publication 550 the holding period begins the day *after*
    acquisition and includes the disposal day, so a position is held for
    "more than 1 year" only when disposal falls strictly after this date.

    A 29 February acquisition maps to 28 February of the following year: the
    holding period starts 1 March, so a full year runs through 28 February and
    1 March is the first long-term day.
    """
    try:
        return acquired.replace(year=acquired.year + 1)
    except ValueError:                      # 29 February -> non-leap year
        return datetime.date(acquired.year + 1, 2, 28)


def classify_holding_period(
    acquisition_date: Optional[datetime.date],
    disposal_date: Optional[datetime.date],
    holding_period_days: Optional[int] = None,
) -> HoldingPeriod:
    """Classify a disposal as short- or long-term under IRC Sec. 1222.

    Dates are authoritative when both are present: long-term requires disposal
    strictly after the one-year anniversary (IRS Pub. 550 -- bought 2012-02-06
    and sold 2013-02-06 is *short*-term even though 366 days elapsed).

    A raw day count cannot always decide, because one calendar year is 365 days
    normally and 366 across a leap day:
      * <= 365 days  -> always short-term (never exceeds either anniversary)
      * >= 367 days  -> always long-term  (exceeds either anniversary)
      * == 366 days  -> ambiguous; exactly one year across a leap span
                        (short-term) but more than one year otherwise.
    """
    if acquisition_date is not None and disposal_date is not None:
        if disposal_date <= acquisition_date:
            return HoldingPeriod.UNKNOWN
        if disposal_date > one_year_anniversary(acquisition_date):
            return HoldingPeriod.LONG_TERM
        return HoldingPeriod.SHORT_TERM

    if holding_period_days is None:
        return HoldingPeriod.UNKNOWN
    if holding_period_days <= 365:
        return HoldingPeriod.SHORT_TERM
    if holding_period_days >= 367:
        return HoldingPeriod.LONG_TERM
    return HoldingPeriod.AMBIGUOUS


def add_business_days(start: datetime.date, days: int) -> datetime.date:
    """Advance ``start`` by ``days`` weekdays.

    Exchange and banking holidays are NOT modelled -- no holiday calendar is
    bundled. The resulting deadline can therefore be one or more days earlier
    than the true settlement date around a holiday, which biases the
    ``SPECIFIC_ID`` check toward flagging for review rather than toward silence.
    """
    current = start
    remaining = days
    while remaining > 0:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class RecordKeepingRequirementsForTaxAuditDefenseEngine:
    """Audits trade records for US federal tax audit defensibility.

    The engine is a *record completeness and defensibility* checker. It does not
    compute tax, match wash sales across trades (see ``wash-sale-rule-tracking-us``)
    or decide lot assignment; it verifies that the records needed to defend those
    positions exist, are internally valid, and are not purged prematurely.
    """

    def __init__(
        self,
        retention_years: int = DEFAULT_RETENTION_YEARS,
        mandatory_fields: Optional[Iterable[str]] = None,
        accounting_method: AccountingMethod = AccountingMethod.CAPITAL,
        specific_id_deadline_business_days: int = DEFAULT_SPECIFIC_ID_DEADLINE_BUSINESS_DAYS,
    ) -> None:
        if not isinstance(retention_years, int) or isinstance(retention_years, bool) \
                or retention_years <= 0:
            raise RecordKeepingError("retention_years must be a positive integer.")
        if specific_id_deadline_business_days < 0:
            raise RecordKeepingError("specific_id_deadline_business_days must be >= 0.")
        self.retention_years = retention_years
        self.mandatory_fields: Sequence[str] = tuple(
            MANDATORY_FIELDS if mandatory_fields is None else mandatory_fields)
        if not self.mandatory_fields:
            raise RecordKeepingError("mandatory_fields must not be empty.")
        self.accounting_method = AccountingMethod(accounting_method)
        self.specific_id_deadline_business_days = specific_id_deadline_business_days
        self.records: List[Record] = []
        self.trade_records: List[TradeRecord] = []

    # -- legacy API ------------------------------------------------------- #

    def add_record(self, record: Record) -> None:
        """Legacy method retained for backward compatibility."""
        self.records.append(record)

    def process(self) -> float:
        """Legacy method retained for backward compatibility."""
        return sum(r.value for r in self.records)

    # -- record intake ---------------------------------------------------- #

    def add_trade_record(self, trade: TradeRecord) -> None:
        self.trade_records.append(trade)

    def elected_method(self, trade: TradeRecord) -> AccountingMethod:
        """The election in force for the entity holding this record.

        This is what determines whether the Sec. 475(f) segregation duty applies,
        independently of how any individual security is then treated.
        """
        return trade.accounting_method or self.accounting_method

    def method_for(self, trade: TradeRecord) -> AccountingMethod:
        """Effective tax treatment of a record.

        A security explicitly held for investment stays in the capital account --
        and so remains subject to Sec. 1091 and the Sec. 1222 holding period --
        even where the trading business has a Sec. 475(f) election in force.
        """
        if trade.held_for_investment:
            return AccountingMethod.CAPITAL
        return self.elected_method(trade)

    # -- audit ------------------------------------------------------------ #

    def audit_records(self, as_of: Optional[datetime.date] = None) -> TaxAuditComplianceReport:
        """Audit every trade record and return an audit-ready compliance report.

        Args:
            as_of: Evaluation date. Pass it explicitly for reproducible output;
                it defaults to today's date, which makes results vary by run.
        """
        if as_of is None:
            as_of = datetime.date.today()
            logger.debug("audit_records called without as_of; defaulting to %s", as_of)

        issues: List[RecordIssue] = []
        retention: List[RetentionAssessment] = []
        complete = incomplete = 0
        short_term = long_term = ambiguous = mtm = 0
        wash_flagged = 0

        duplicate_ids = {
            tid for tid, count in Counter(t.trade_id for t in self.trade_records).items()
            if count > 1
        }
        reported_duplicates: Set[str] = set()

        for tr in self.trade_records:
            if tr.trade_id in duplicate_ids and tr.trade_id not in reported_duplicates:
                reported_duplicates.add(tr.trade_id)
                issues.append(RecordIssue(
                    trade_id=tr.trade_id,
                    issue_type=IssueType.DUPLICATE_TRADE_ID,
                    detail="trade_id is not unique across the record set; the audit "
                           "trail cannot attribute this entry to a single execution.",
                ))

            rec = tr.to_dict()
            missing = [f for f in self.mandatory_fields if rec.get(f) is None]
            if missing:
                incomplete += 1
                issues.append(RecordIssue(
                    trade_id=tr.trade_id,
                    issue_type=IssueType.MISSING_FIELD,
                    detail=f"Missing mandatory fields: {', '.join(missing)}",
                ))
            else:
                complete += 1

            issues.extend(self._validate_fields(tr))

            trade_date = parse_date(tr.trade_date)
            acquisition = parse_date(tr.acquisition_date)
            disposal = parse_date(tr.disposal_date)
            if disposal is None and _side_of(tr) == "SELL":
                disposal = trade_date

            # The segregation duty follows the entity's election, so it applies to
            # investment securities too -- those are precisely the ones that must
            # be identified on the day acquired.
            if self.elected_method(tr) is AccountingMethod.MTM_475F:
                issues.extend(self._check_mtm_identification(tr, trade_date))

            method = self.method_for(tr)
            if method is AccountingMethod.MTM_475F:
                mtm += 1
            else:
                classification = classify_holding_period(
                    acquisition, disposal, tr.holding_period_days
                )
                if classification is HoldingPeriod.SHORT_TERM:
                    short_term += 1
                elif classification is HoldingPeriod.LONG_TERM:
                    long_term += 1
                elif classification is HoldingPeriod.AMBIGUOUS:
                    ambiguous += 1
                    issues.append(RecordIssue(
                        trade_id=tr.trade_id,
                        issue_type=IssueType.HOLDING_PERIOD_AMBIGUOUS,
                        detail="holding_period_days == 366 cannot decide IRC Sec. 1222 "
                               "treatment across a leap-day span; supply "
                               "acquisition_date and disposal_date.",
                    ))
                issues.extend(self._check_wash_sale(tr, trade_date, as_of))

            if tr.wash_sale_flag is True:
                wash_flagged += 1

            issues.extend(self._check_lot_identification(tr, trade_date))

            assessment = self._assess_retention(tr, disposal, as_of)
            retention.append(assessment)
            # An open position is the normal state, so retention is only reported
            # as an issue where a purge has actually been proposed. The per-record
            # assessment is always returned in ``report.retention`` regardless.
            if tr.purge_pending and not assessment.purge_eligible:
                indeterminate_clock = assessment.earliest_purge_date is None
                issues.append(RecordIssue(
                    trade_id=tr.trade_id,
                    issue_type=(IssueType.RETENTION_INDETERMINATE if indeterminate_clock
                                else IssueType.RETENTION_AT_RISK),
                    detail=f"Purge is pending but the record is not purge-eligible: "
                           f"{assessment.rationale}",
                ))

        total = len(self.trade_records)
        defects = sum(1 for i in issues if i.severity is Severity.DEFECT)
        advisories = len(issues) - defects
        purge_eligible = sum(1 for a in retention if a.purge_eligible)
        indeterminate = sum(1 for a in retention if a.earliest_purge_date is None)

        if defects:
            status = STATUS_ISSUES_FOUND
        elif advisories:
            status = STATUS_ADVISORY_ONLY
        else:
            status = STATUS_COMPLIANT

        notes = (
            f"TAX AUDIT COMPLIANCE [{status}] as of {as_of.isoformat()}: "
            f"Total = {total}, Complete = {complete}, Incomplete = {incomplete}, "
            f"Short-Term = {short_term}, Long-Term = {long_term}, "
            f"Ambiguous = {ambiguous}, MTM = {mtm}, "
            f"Wash Sale Flagged = {wash_flagged}, "
            f"Purge-Eligible = {purge_eligible}, Retention Indeterminate = {indeterminate}, "
            f"Defects = {defects}, Advisories = {advisories}."
        )
        if defects:
            logger.warning(notes)
        else:
            logger.info(notes)

        return TaxAuditComplianceReport(
            total_records=total,
            complete_records=complete,
            incomplete_records=incomplete,
            short_term_trades=short_term,
            long_term_trades=long_term,
            wash_sale_flagged=wash_flagged,
            issues=issues,
            status=status,
            audit_notes=notes,
            as_of=as_of,
            ambiguous_holding_period_trades=ambiguous,
            mtm_trades=mtm,
            retention=retention,
            purge_eligible_records=purge_eligible,
            retention_indeterminate_records=indeterminate,
            defect_count=defects,
            advisory_count=advisories,
        )

    # -- individual checks ------------------------------------------------ #

    def _validate_fields(self, tr: TradeRecord) -> List[RecordIssue]:
        """Structural validation. Never raises: a bad record is data, not a crash."""
        problems: List[str] = []

        if not isinstance(tr.trade_id, str) or not tr.trade_id.strip():
            problems.append("trade_id must be a non-empty string")
        if not isinstance(tr.symbol, str) or not tr.symbol.strip():
            problems.append("symbol must be a non-empty string")
        if _side_of(tr) not in VALID_SIDES:
            problems.append(f"side must be one of {VALID_SIDES}, got {tr.side!r}")
        if not _is_number(tr.quantity) or tr.quantity <= 0:
            problems.append(f"quantity must be a positive number, got {tr.quantity!r}")
        if not _is_number(tr.price) or tr.price < 0:
            problems.append(f"price must be a non-negative number, got {tr.price!r}")
        if tr.holding_period_days is not None and (
            not isinstance(tr.holding_period_days, int)
            or isinstance(tr.holding_period_days, bool)
            or tr.holding_period_days < 0
        ):
            problems.append(
                f"holding_period_days must be a non-negative int, "
                f"got {tr.holding_period_days!r}")
        if tr.lot_method not in VALID_LOT_METHODS:
            problems.append(
                f"lot_method must be one of {VALID_LOT_METHODS}, got {tr.lot_method!r}")

        for name in ("trade_date", "acquisition_date", "disposal_date",
                     "lot_identification_date", "investment_identification_date"):
            raw = getattr(tr, name)
            if raw is not None and parse_date(raw) is None:
                problems.append(f"{name} is not a parseable ISO-8601 date: {raw!r}")

        trade_date = parse_date(tr.trade_date)
        acquisition = parse_date(tr.acquisition_date)
        disposal = parse_date(tr.disposal_date)
        if acquisition and disposal and disposal < acquisition:
            problems.append("disposal_date precedes acquisition_date")
        if trade_date and acquisition and _side_of(tr) == "SELL" and trade_date < acquisition:
            problems.append("SELL trade_date precedes acquisition_date")

        return [
            RecordIssue(trade_id=tr.trade_id, issue_type=IssueType.INVALID_FIELD, detail=p)
            for p in problems
        ]

    def _check_wash_sale(
        self,
        tr: TradeRecord,
        trade_date: Optional[datetime.date],
        as_of: datetime.date,
    ) -> List[RecordIssue]:
        """Wash sale determination completeness for capital-account disposals.

        The IRC Sec. 1091 window runs 30 days *after* the sale as well as before,
        so a determination recorded before that window closes is provisional: a
        replacement purchase can still occur. Such records are reported as
        advisories, not defects -- flagging them as defects would mark every
        recent sale non-compliant and train reviewers to ignore the status.
        """
        if _side_of(tr) != "SELL":
            return []

        window_open = (
            trade_date is not None
            and as_of < trade_date + datetime.timedelta(days=WASH_SALE_WINDOW_DAYS)
        )
        if window_open:
            closes = trade_date + datetime.timedelta(days=WASH_SALE_WINDOW_DAYS)
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.WASH_SALE_WINDOW_OPEN,
                detail=f"IRC Sec. 1091 replacement window remains open until "
                       f"{closes.isoformat()}; any wash_sale_flag recorded now "
                       f"is provisional.",
                severity=Severity.ADVISORY,
            )]
        if tr.wash_sale_flag is None:
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.WASH_SALE_UNSET,
                detail="Sell transaction missing wash_sale_flag; the Sec. 1091 "
                       "determination must be recorded, including a negative one.",
            )]
        return []

    def _check_lot_identification(
        self, tr: TradeRecord, trade_date: Optional[datetime.date]
    ) -> List[RecordIssue]:
        """Substantiation for a SPECIFIC_ID lot method.

        Under Treas. Reg. Sec. 1.1012-1(c)(1) an identification is adequate only
        if made no later than the earlier of the settlement date or the Rule
        15c6-1 settlement time. Absent that, basis falls back to FIFO, so an
        unsubstantiated SPECIFIC_ID claim is a live audit exposure.
        """
        if tr.lot_method != "SPECIFIC_ID" or _side_of(tr) != "SELL":
            return []
        if trade_date is None:
            return []

        deadline = add_business_days(trade_date, self.specific_id_deadline_business_days)
        identified = parse_date(tr.lot_identification_date)
        if identified is None:
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.LOT_ID_UNSUBSTANTIATED,
                detail="lot_method is SPECIFIC_ID but no lot_identification_date is "
                       "recorded; basis defaults to FIFO under Treas. Reg. "
                       "Sec. 1.1012-1(c)(1).",
            )]
        if identified > deadline:
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.LOT_ID_UNSUBSTANTIATED,
                detail=f"SPECIFIC_ID identified {identified.isoformat()}, after the "
                       f"{deadline.isoformat()} settlement deadline; the "
                       f"identification is not adequate.",
            )]
        return []

    def _check_mtm_identification(
        self, tr: TradeRecord, trade_date: Optional[datetime.date]
    ) -> List[RecordIssue]:
        """Sec. 475(f) segregation evidence.

        IRS Topic 429: a trader must keep records distinguishing investment
        securities from trading-business securities, and investment securities
        must be identified as such **on the day acquired**.
        """
        if not tr.held_for_investment:
            return []
        identified = parse_date(tr.investment_identification_date)
        if identified is None:
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.MTM_IDENTIFICATION_MISSING,
                detail="Investment security under a Sec. 475(f) election has no "
                       "investment_identification_date; IRS Topic 429 requires "
                       "identification in the records on the day of acquisition.",
            )]
        if trade_date is not None and identified > trade_date:
            return [RecordIssue(
                trade_id=tr.trade_id,
                issue_type=IssueType.MTM_IDENTIFICATION_MISSING,
                detail=f"Investment identification recorded {identified.isoformat()}, "
                       f"after the {trade_date.isoformat()} acquisition date; "
                       f"Sec. 475(f) requires same-day identification.",
            )]
        return []

    def _assess_retention(
        self,
        tr: TradeRecord,
        disposal: Optional[datetime.date],
        as_of: datetime.date,
    ) -> RetentionAssessment:
        """Purge eligibility. The clock starts at disposal, never at acquisition."""
        if tr.legal_hold:
            return RetentionAssessment(
                trade_id=tr.trade_id,
                earliest_purge_date=None,
                purge_eligible=False,
                rationale="Record is under legal/examination hold and is never "
                          "purge-eligible.",
            )
        if disposal is None:
            return RetentionAssessment(
                trade_id=tr.trade_id,
                earliest_purge_date=None,
                purge_eligible=False,
                rationale="No disposal_date: the position may still be open, so the "
                          "retention clock has not started. Basis records for open "
                          "positions must be kept until the limitations period for "
                          "the disposal year expires.",
            )
        earliest = _add_years(disposal, self.retention_years)
        eligible = as_of >= earliest
        return RetentionAssessment(
            trade_id=tr.trade_id,
            earliest_purge_date=earliest,
            purge_eligible=eligible,
            rationale=(
                f"Disposed {disposal.isoformat()}; {self.retention_years}-year policy "
                f"retention runs to {earliest.isoformat()} "
                f"({'elapsed' if eligible else 'not yet elapsed'})."
            ),
        )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _side_of(tr: TradeRecord) -> Optional[str]:
    return tr.side.upper().strip() if isinstance(tr.side, str) else None


def _is_number(value: Any) -> bool:
    """True for a real, finite number. NaN/Inf are rejected: a non-finite
    quantity or price would otherwise pass every comparison silently."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _add_years(start: datetime.date, years: int) -> datetime.date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:                      # 29 February -> non-leap year
        return datetime.date(start.year + years, 2, 28)

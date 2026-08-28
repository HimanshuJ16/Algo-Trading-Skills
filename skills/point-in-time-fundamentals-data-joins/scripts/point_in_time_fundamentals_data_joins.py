"""
point-in-time-fundamentals-data-joins: as-of join engine for SEC-filed fundamental
metrics, restatement isolator, and naive-join lookahead auditor.

Purpose
-------
Answer one question for a backtest bar dated ``T``: *what did the market actually
know about this company's fundamentals at the close of T?* A fundamentals table
keyed on ``period_end_date`` answers a different and much more flattering
question -- what the numbers turned out to be -- and the difference is the single
most common source of lookahead bias in factor research. This engine resolves the
metric that was publicly filed and available on or before ``T``, keeps historical
as-reported values intact when they are later restated, and reports what a naive
period-end join *would* have returned so the bias is quantified rather than
assumed away.

Availability, not filing date
-----------------------------
A record's availability date is ``filing_date + availability_lag_days``, not
``filing_date``, and the default lag is **1 day**. This is deliberate:

Under Regulation S-T Rule 13(a)(2) (17 CFR 232.13(a)(2)) an electronic submission
transmitted before 5:30 p.m. Eastern time carries that business day as its filing
date; one transmitted after 5:30 p.m. is deemed filed the next business day.
A 10-K accepted at 4:45 p.m. ET therefore carries filing date ``D`` while
becoming public 45 minutes *after* the 4:00 p.m. ET regular-session close. Joining
on ``filing_date <= as_of_date`` lets a strategy trade the D close on a document
that did not exist at that close. The one-day default removes that window.

The lag is applied in **calendar** days, which is safe for ``<=`` comparison
against trading dates: a Friday filing becomes available Saturday, which is still
``<=`` the following Monday. Set ``availability_lag_days=0`` only if the caller
has already resolved intraday availability itself (for example by using the EDGAR
submission header's ``ACCEPTANCE-DATETIME``, which records when EDGAR actually
accepted the submission, rather than the assigned ``FILED AS OF DATE``).

Record selection
----------------
Among available records the engine selects by ``period_end_date`` **first**, then
``filing_date``, then ``revision_number``. Ordering by filing date first is wrong
and is the defect this ordering exists to prevent: an amended 10-K/A restating
FY2022, filed in August 2023, has a later filing date than the original Q1-2023
10-Q filed in April 2023, and a filing-date-first sort therefore answers "latest
known EPS" with a *stale fiscal period*. Latest period, as most recently restated
within what was public at T, is the correct as-of semantics.

Restatements are isolated, non-reliance is flagged
--------------------------------------------------
A restated value filed after ``T`` is never returned for a query at ``T`` -- the
original as-reported number is what the market traded on. The reverse case is
also modelled: Form 8-K Item 4.02 requires a registrant to disclose publicly when
previously issued financial statements should no longer be relied upon, and that
disclosure normally lands *before* the corrected figures are filed. In that
interval the market knows the number is wrong but has no replacement. The engine
keeps returning the as-reported value (deleting it would substitute a number
nobody had) and sets ``is_non_reliance_flagged`` so the caller can decide to
suppress the signal instead of trading a discredited input.

Limitations (documented, deliberate)
------------------------------------
- **Date granularity only.** Availability is a date, not a timestamp. Sub-daily
  filing time is approximated by ``availability_lag_days``; a strategy trading
  intraday on filings needs acceptance timestamps, not this engine.
- **No business-day calendar.** The lag is calendar days. This is conservative
  for ``<=`` date comparison but is not a settlement- or trading-day calculation.
- **Filing date is the vendor's, not verified against EDGAR.** The engine
  rejects impossible dates (``filing_date < period_end_date``) but cannot detect
  a vendor that back-stamped a plausible-but-wrong filing date. Cross-check
  against EDGAR full-index data.
- **US SEC framing.** The 5:30 p.m. ET rule and the 10-K/10-Q deadlines are US
  Exchange Act reporting. Other jurisdictions have different regimes; the join
  logic is generic but the default lag rationale is not.
- **No survivorship or symbol-change handling.** Tickers are matched literally.
  Delisted issuers and ticker reuse belong to
  ``survivorship-bias-free-universe-construction`` and
  ``reference-data-symbol-mapping-across-vendors``.
"""
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Strict ISO-8601 calendar-date pattern. Dates are compared as ``datetime.date``
#: after parsing, but the pattern is enforced first so behaviour does not drift
#: with the Python version: ``date.fromisoformat`` accepts additional layouts
#: (e.g. ``'20230215'``) from Python 3.11 onward.
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Default availability lag in calendar days. See the module docstring: EDGAR
#: assigns filing date ``D`` to anything accepted before 5:30 p.m. ET, which is
#: after the 4:00 p.m. ET regular-session close, so same-day use is lookahead.
DEFAULT_AVAILABILITY_LAG_DAYS = 1


class LeakageType(str, Enum):
    """How a naive ``period_end_date`` join would have differed from the PIT join."""

    NONE = "NONE"
    #: The naive join lands on a fiscal period whose filing was not yet public.
    UNRELEASED_FILING = "UNRELEASED_FILING"
    #: The naive join overwrites the as-reported value with a later restatement.
    RESTATEMENT = "RESTATEMENT"
    UNRELEASED_AND_RESTATEMENT = "UNRELEASED_AND_RESTATEMENT"


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parses a strict ``YYYY-MM-DD`` string, raising ``ValueError`` otherwise."""
    if not isinstance(value, str) or not _ISO_DATE_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must be a 'YYYY-MM-DD' ISO-8601 date string, got {value!r}. "
            "Unpadded or locale-formatted dates compare incorrectly and silently "
            "reintroduce lookahead bias."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid calendar date: {value!r}") from exc


@dataclass
class Config:
    """Engine configuration.

    ``default_availability_lag_days`` is the calendar-day delay added to
    ``filing_date`` to obtain the date from which a record may be used. It
    defaults to 1; see the module docstring for why 0 is unsafe against
    SEC-filed data.
    """

    name: str = "PIT_Engine"
    default_availability_lag_days: int = DEFAULT_AVAILABILITY_LAG_DAYS

    def __post_init__(self) -> None:
        if isinstance(self.default_availability_lag_days, bool) or not isinstance(
            self.default_availability_lag_days, int
        ):
            raise ValueError("default_availability_lag_days must be an int")
        if self.default_availability_lag_days < 0:
            raise ValueError(
                "default_availability_lag_days must be >= 0; a negative lag would "
                "make a record usable before it was filed."
            )


@dataclass
class FundamentalFilingRecord:
    """One as-reported metric observation from a single filing.

    A restatement is a **new record**, not an edit of the original: same
    ``period_end_date``, later ``filing_date``, higher ``revision_number``.
    """

    ticker: str
    metric_name: str
    value: float
    period_end_date: str                  # fiscal period end, e.g. '2022-12-31'
    filing_date: str                      # EDGAR assigned filing date, e.g. '2023-02-15'
    revision_number: int = 0              # 0 = original as-reported, 1+ = restatement
    #: Date an Item 4.02 Form 8-K (or equivalent) publicly disclosed that this
    #: value should no longer be relied upon. Optional.
    non_reliance_date: Optional[str] = None

    def validate(self) -> None:
        """Raises ``ValueError`` if the record cannot support a PIT join."""
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if not isinstance(self.metric_name, str) or not self.metric_name.strip():
            raise ValueError("metric_name must be a non-empty string")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError(f"value must be a real number, got {self.value!r}")
        if not math.isfinite(float(self.value)):
            raise ValueError(
                f"value must be finite, got {self.value!r}. A NaN propagates silently "
                "through every downstream factor calculation."
            )
        if isinstance(self.revision_number, bool) or not isinstance(self.revision_number, int):
            raise ValueError("revision_number must be an int")
        if self.revision_number < 0:
            raise ValueError("revision_number must be >= 0")

        period_end = _parse_iso_date(self.period_end_date, "period_end_date")
        filing = _parse_iso_date(self.filing_date, "filing_date")
        if filing < period_end:
            raise ValueError(
                f"filing_date {self.filing_date} precedes period_end_date "
                f"{self.period_end_date} for {self.ticker}/{self.metric_name}. A filing "
                "cannot report a period that has not ended; this is corrupt vendor data "
                "and accepting it reintroduces exactly the lookahead this engine removes."
            )
        if self.non_reliance_date is not None:
            non_reliance = _parse_iso_date(self.non_reliance_date, "non_reliance_date")
            if non_reliance < filing:
                raise ValueError(
                    f"non_reliance_date {self.non_reliance_date} precedes filing_date "
                    f"{self.filing_date}; a value cannot be disclaimed before it is filed."
                )

    # -- internal helpers -------------------------------------------------
    def _period_end(self) -> date:
        return date.fromisoformat(self.period_end_date)

    def _filing(self) -> date:
        return date.fromisoformat(self.filing_date)

    def available_from(self, availability_lag_days: int) -> date:
        """Date from which this record may be used by a strategy.

        Raises ``ValueError`` rather than letting ``OverflowError`` escape when
        the lag pushes the date past ``date.max``; callers of this module should
        only ever have to catch ``ValueError``.
        """
        try:
            return self._filing() + timedelta(days=availability_lag_days)
        except OverflowError as exc:
            raise ValueError(
                f"filing_date {self.filing_date} plus an availability lag of "
                f"{availability_lag_days} day(s) exceeds the representable date range."
            ) from exc

    def _sort_key(self) -> Tuple[date, date, int, float]:
        """Total, insertion-order-independent ordering for deterministic selection."""
        return (self._period_end(), self._filing(), self.revision_number, float(self.value))


@dataclass
class PITQuery:
    """An as-of request. ``as_of_date`` means *as known at the close of that date*."""

    ticker: str
    metric_name: str
    as_of_date: str                       # e.g. '2023-03-01'
    #: Restrict the answer to one fiscal period. ``None`` (default) resolves the
    #: latest fiscal period that was public as of ``as_of_date``.
    period_end_date: Optional[str] = None

    def validate(self) -> None:
        """Raises ``ValueError`` if the query cannot be answered deterministically."""
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if not isinstance(self.metric_name, str) or not self.metric_name.strip():
            raise ValueError("metric_name must be a non-empty string")
        _parse_iso_date(self.as_of_date, "as_of_date")
        if self.period_end_date is not None:
            _parse_iso_date(self.period_end_date, "period_end_date")


@dataclass
class PITFundamentalsReport:
    """Result of one as-of join, including the audit of the naive alternative."""

    ticker: str
    metric_name: str
    as_of_date: str
    matched_record_value: Optional[float]
    matched_filing_date: Optional[str]
    matched_period_end_date: Optional[str]
    revision_number: Optional[int]
    #: True only when a *restatement* of the matched period was withheld. It is
    #: NOT set merely because some unreleased filing exists -- see
    #: ``unreleased_filing_blocked``.
    restatement_leakage_blocked: bool
    status: str                           # 'RECORD_FOUND_PIT_VALID' | 'NO_DATA_AVAILABLE_AS_OF_DATE'
    audit_notes: str
    #: True when the naive join would have used a fiscal period that was not yet public.
    unreleased_filing_blocked: bool = False
    leakage_type: LeakageType = LeakageType.NONE
    #: What a naive ``period_end_date <= as_of_date`` join would have returned.
    naive_join_value: Optional[float] = None
    naive_join_filing_date: Optional[str] = None
    naive_join_period_end_date: Optional[str] = None
    #: First date the matched record could legitimately be used (filing + lag).
    matched_available_from: Optional[str] = None
    availability_lag_days: int = DEFAULT_AVAILABILITY_LAG_DAYS
    #: True when an Item 4.02 non-reliance disclosure was already public at
    #: ``as_of_date`` for the matched record. The value is still returned.
    is_non_reliance_flagged: bool = False
    #: Number of *distinct values* tying on (period_end, filing_date, revision).
    #: 1 is the normal case (byte-identical duplicate rows included); >1 means
    #: corrupt input, resolved deterministically rather than silently.
    ambiguous_candidate_count: int = 0


class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def process(self, data: Any) -> Any:
        return data


class PointInTimeFundamentalsDataJoinsEngine:
    """
    Point-In-Time (PIT) fundamentals data join engine enforcing SEC EDGAR filing
    date timestamps plus an explicit availability lag, preventing restatement
    lookahead bias and unreleased-earnings leakage in backtests.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.filing_database: List[FundamentalFilingRecord] = []

    @property
    def availability_lag_days(self) -> int:
        """Calendar days added to ``filing_date`` to obtain the availability date."""
        return self.config.default_availability_lag_days

    def insert_filings(self, records: List[FundamentalFilingRecord]) -> None:
        """Validates and stores fundamental filing records.

        Validation is total: if any record in the batch is rejected, none are
        stored, so a partially ingested batch can never be queried as if complete.
        """
        validated: List[FundamentalFilingRecord] = []
        for record in records:
            if not isinstance(record, FundamentalFilingRecord):
                raise ValueError(
                    f"expected FundamentalFilingRecord, got {type(record).__name__}"
                )
            record.validate()
            validated.append(record)
        self.filing_database.extend(validated)

    def _matching(
        self, query: PITQuery, ticker_upper: str, metric_lower: str
    ) -> List[FundamentalFilingRecord]:
        """Records for the queried ticker/metric (and period, when constrained)."""
        return [
            r for r in self.filing_database
            if r.ticker.strip().upper() == ticker_upper
            and r.metric_name.strip().lower() == metric_lower
            and (query.period_end_date is None or r.period_end_date == query.period_end_date)
        ]

    @staticmethod
    def _select(
        candidates: List[FundamentalFilingRecord],
    ) -> Optional[FundamentalFilingRecord]:
        """Latest fiscal period, then latest filing, then highest revision.

        Period end is the primary key on purpose: sorting by ``filing_date``
        first lets an amendment to an older period outrank the original filing of
        a newer one, answering "latest known metric" with a stale fiscal period.
        """
        if not candidates:
            return None
        return max(candidates, key=FundamentalFilingRecord._sort_key)

    def execute_pit_join(self, query: PITQuery) -> PITFundamentalsReport:
        """
        Executes an as-of join returning the fundamental metric that was publicly
        available on or before ``as_of_date``, and audits the naive alternative.
        """
        if not isinstance(query, PITQuery):
            raise ValueError(f"expected PITQuery, got {type(query).__name__}")
        query.validate()

        lag = self.availability_lag_days
        as_of = _parse_iso_date(query.as_of_date, "as_of_date")
        ticker_upper = query.ticker.strip().upper()
        metric_lower = query.metric_name.strip().lower()

        matching = self._matching(query, ticker_upper, metric_lower)

        # PIT set: the period has ended AND the filing was available at as_of.
        valid_candidates = [
            r for r in matching
            if r._period_end() <= as_of and r.available_from(lag) <= as_of
        ]
        # Naive set: period end only -- the ordinary `merge on (ticker, period)`.
        naive_candidates = [r for r in matching if r._period_end() <= as_of]

        best_record = self._select(valid_candidates)
        naive_record = self._select(naive_candidates)

        # -- leakage classification ---------------------------------------
        unreleased_blocked = naive_record is not None and (
            best_record is None
            or naive_record._period_end() > best_record._period_end()
        )
        restatement_blocked = best_record is not None and any(
            r._period_end() == best_record._period_end()
            and r._sort_key() > best_record._sort_key()
            for r in naive_candidates
        )
        if unreleased_blocked and restatement_blocked:
            leakage_type = LeakageType.UNRELEASED_AND_RESTATEMENT
        elif unreleased_blocked:
            leakage_type = LeakageType.UNRELEASED_FILING
        elif restatement_blocked:
            leakage_type = LeakageType.RESTATEMENT
        else:
            leakage_type = LeakageType.NONE

        naive_value = naive_record.value if naive_record else None
        naive_filing = naive_record.filing_date if naive_record else None
        naive_period = naive_record.period_end_date if naive_record else None

        if best_record is None:
            notes = (
                f"NO PIT FUNDAMENTALS FOUND [{ticker_upper} '{metric_lower}'] as of "
                f"{query.as_of_date} (availability lag {lag}d). "
                f"{len(naive_candidates)} record(s) had a completed period but were not "
                f"yet public; leakage_type={leakage_type.value}."
            )
            if naive_record is not None:
                notes += (
                    f" A naive period-end join would have used {naive_value} "
                    f"(period end {naive_period}, filed {naive_filing})."
                )
            logger.info(notes)
            return PITFundamentalsReport(
                ticker=ticker_upper,
                metric_name=query.metric_name,
                as_of_date=query.as_of_date,
                matched_record_value=None,
                matched_filing_date=None,
                matched_period_end_date=None,
                revision_number=None,
                restatement_leakage_blocked=restatement_blocked,
                status="NO_DATA_AVAILABLE_AS_OF_DATE",
                audit_notes=notes,
                unreleased_filing_blocked=unreleased_blocked,
                leakage_type=leakage_type,
                naive_join_value=naive_value,
                naive_join_filing_date=naive_filing,
                naive_join_period_end_date=naive_period,
                availability_lag_days=lag,
            )

        best_key = best_record._sort_key()
        # Only a *value* conflict is a data-quality problem. Byte-identical
        # duplicate rows are common and benign; warning on them trains the
        # reader to ignore the warning that matters.
        tied_values = {
            float(r.value) for r in valid_candidates
            if (r._period_end(), r._filing(), r.revision_number) == best_key[:3]
        }
        ambiguous = len(tied_values)
        non_reliance_flagged = (
            best_record.non_reliance_date is not None
            and _parse_iso_date(best_record.non_reliance_date, "non_reliance_date") <= as_of
        )
        available_from = best_record.available_from(lag).isoformat()

        notes = (
            f"PIT FUNDAMENTALS MATCHED [{ticker_upper} '{metric_lower}' - "
            f"RECORD_FOUND_PIT_VALID]: Value = {best_record.value} "
            f"(Period End = {best_record.period_end_date}, Filing Date = "
            f"{best_record.filing_date}, Available From = {available_from}, "
            f"Rev = {best_record.revision_number}). Query As-Of = {query.as_of_date}. "
            f"leakage_type={leakage_type.value}."
        )
        if leakage_type is not LeakageType.NONE:
            notes += (
                f" A naive period-end join would have used {naive_value} "
                f"(period end {naive_period}, filed {naive_filing})."
            )
        if non_reliance_flagged:
            notes += (
                f" NON-RELIANCE: an Item 4.02 disclosure dated "
                f"{best_record.non_reliance_date} was already public; the as-reported "
                "value is returned but should not be traded as a valid input."
            )
        if ambiguous > 1:
            notes += (
                f" WARNING: {ambiguous} conflicting values tie on (period_end, "
                "filing_date, revision_number); resolved deterministically by value. "
                "Reconcile the source data."
            )
        logger.info(notes)

        return PITFundamentalsReport(
            ticker=ticker_upper,
            metric_name=query.metric_name,
            as_of_date=query.as_of_date,
            matched_record_value=best_record.value,
            matched_filing_date=best_record.filing_date,
            matched_period_end_date=best_record.period_end_date,
            revision_number=best_record.revision_number,
            restatement_leakage_blocked=restatement_blocked,
            status="RECORD_FOUND_PIT_VALID",
            audit_notes=notes,
            unreleased_filing_blocked=unreleased_blocked,
            leakage_type=leakage_type,
            naive_join_value=naive_value,
            naive_join_filing_date=naive_filing,
            naive_join_period_end_date=naive_period,
            matched_available_from=available_from,
            availability_lag_days=lag,
            is_non_reliance_flagged=non_reliance_flagged,
            ambiguous_candidate_count=ambiguous,
        )

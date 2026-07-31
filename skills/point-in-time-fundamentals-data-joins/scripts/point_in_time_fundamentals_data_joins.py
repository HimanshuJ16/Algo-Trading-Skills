import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str = "PIT_Engine"
    default_availability_lag_days: int = 0

@dataclass
class FundamentalFilingRecord:
    ticker: str
    metric_name: str
    value: float
    period_end_date: str                 # e.g. '2022-12-31'
    filing_date: str                     # e.g. '2023-02-15' (Public release date)
    revision_number: int = 0             # 0 for original, 1+ for restatements

@dataclass
class PITQuery:
    ticker: str
    metric_name: str
    as_of_date: str                      # e.g. '2023-03-01'

@dataclass
class PITFundamentalsReport:
    ticker: str
    metric_name: str
    as_of_date: str
    matched_record_value: Optional[float]
    matched_filing_date: Optional[str]
    matched_period_end_date: Optional[str]
    revision_number: Optional[int]
    restatement_leakage_blocked: bool
    status: str                          # 'RECORD_FOUND_PIT_VALID', 'NO_DATA_AVAILABLE_AS_OF_DATE'
    audit_notes: str

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
    Point-In-Time (PIT) fundamentals data join engine enforcing SEC EDGAR filing date timestamps,
    preventing restatement lookahead bias and unreleased earnings leakage in backtests.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.filing_database: List[FundamentalFilingRecord] = []

    def insert_filings(self, records: List[FundamentalFilingRecord]) -> None:
        """Stores fundamental filing records in database."""
        self.filing_database.extend(records)

    def execute_pit_join(self, query: PITQuery) -> PITFundamentalsReport:
        """
        Executes as-of join querying fundamental metrics available on or before as_of_date.
        """
        ticker_upper = query.ticker.upper()
        metric_lower = query.metric_name.lower()

        # Candidates where filing_date <= as_of_date AND period_end_date <= as_of_date
        valid_candidates = [
            r for r in self.filing_database
            if r.ticker.upper() == ticker_upper
            and r.metric_name.lower() == metric_lower
            and r.filing_date <= query.as_of_date
            and r.period_end_date <= query.as_of_date
        ]

        # Naive candidates (period_end_date <= as_of_date, ignoring filing_date)
        naive_candidates = [
            r for r in self.filing_database
            if r.ticker.upper() == ticker_upper
            and r.metric_name.lower() == metric_lower
            and r.period_end_date <= query.as_of_date
        ]

        restatement_blocked = len(naive_candidates) > len(valid_candidates)

        if not valid_candidates:
            notes = (
                f"NO PIT FUNDAMENTALS FOUND [{ticker_upper} '{query.metric_name}'] as of {query.as_of_date}. "
                f"({len(naive_candidates)} unreleased/future-filed records blocked)."
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
                audit_notes=notes
            )

        # Pick most recent filing by filing_date, then revision_number
        best_record = max(
            valid_candidates,
            key=lambda r: (r.filing_date, r.period_end_date, r.revision_number)
        )

        notes = (
            f"PIT FUNDAMENTALS MATCHED [{ticker_upper} '{query.metric_name}' - RECORD_FOUND_PIT_VALID]: "
            f"Value = {best_record.value} (Period End = {best_record.period_end_date}, "
            f"Filing Date = {best_record.filing_date}, Rev = {best_record.revision_number}). "
            f"Query As-Of = {query.as_of_date}."
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
            audit_notes=notes
        )

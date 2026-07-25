"""
backtest-database-schema-for-point-in-time-queries: Point-in-time (PIT)
data store with as-of temporal query semantics.
"""
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PITRecord:
    symbol: str
    field: str
    value: float
    known_at: str  # ISO date when data became publicly known
    valid_from: str


@dataclass
class PITQueryResult:
    symbol: str
    field: str
    as_of_date: str
    value: Optional[float]
    known_at: Optional[str]
    has_future_leakage: bool


class PointInTimeStore:
    """In-memory PIT data store with as-of query semantics."""

    def __init__(self):
        self._records: List[PITRecord] = []

    def insert(self, record: PITRecord) -> None:
        self._records.append(record)

    def query_as_of(self, symbol: str, field: str, as_of_date: str) -> PITQueryResult:
        """Returns the most recent record known on or before as_of_date."""
        candidates = [
            r for r in self._records
            if r.symbol == symbol and r.field == field and r.known_at <= as_of_date
        ]

        if not candidates:
            return PITQueryResult(symbol, field, as_of_date, None, None, False)

        best = max(candidates, key=lambda r: r.known_at)
        return PITQueryResult(symbol, field, as_of_date, best.value, best.known_at, False)

    def audit_leakage(self, symbol: str, field: str, backtest_date: str) -> PITQueryResult:
        """Checks if any record with known_at > backtest_date would be returned by a naive query."""
        future = [
            r for r in self._records
            if r.symbol == symbol and r.field == field and r.known_at > backtest_date
        ]
        pit_result = self.query_as_of(symbol, field, backtest_date)

        if future:
            logger.warning(
                f"LOOKAHEAD LEAKAGE: {len(future)} records for {symbol}/{field} "
                f"have known_at > {backtest_date}. PIT query correctly excluded them."
            )
            return PITQueryResult(
                symbol, field, backtest_date,
                pit_result.value, pit_result.known_at,
                has_future_leakage=True,
            )
        return pit_result

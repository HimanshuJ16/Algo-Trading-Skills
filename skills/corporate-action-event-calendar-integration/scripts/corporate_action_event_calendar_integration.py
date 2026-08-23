"""Corporate action event calendar integration engine.

Tracks the 4-date corporate action lifecycle (declaration -> ex-date ->
record date -> payment date), queries forward risk windows, computes cash
dividend entitlement receivables, and reconciles two vendor feeds.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_TYPES = frozenset({"CASH_DIVIDEND", "STOCK_SPLIT", "RIGHTS_OFFERING", "SPIN_OFF"})


def _require_date(value: object, name: str) -> date:
    """
    Rejects non-dates and datetime.datetime values. datetime subclasses date,
    so it would slip through isinstance checks and then never compare equal to
    a plain date (silent false mismatches in reconciliation, skewed ordering).
    Callers with datetimes must pass .date().
    """
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.date, got {type(value).__name__}")
    return value


@dataclass
class CorporateActionEvent:
    event_id: str
    symbol: str
    event_type: str                     # one of EVENT_TYPES
    declaration_date: date
    ex_date: date
    record_date: date
    payment_date: date
    value: float                        # e.g. $1.50 per share or 2.0 split ratio

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError(f"symbol must be a non-empty string (event {self.event_id})")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"event_type {self.event_type!r} (event {self.event_id}) not in "
                f"{sorted(EVENT_TYPES)}"
            )
        for field_name in ("declaration_date", "ex_date", "record_date", "payment_date"):
            _require_date(getattr(self, field_name), f"{field_name} (event {self.event_id})")
        if not (
            self.declaration_date <= self.ex_date
            and self.ex_date <= self.record_date
            and self.record_date <= self.payment_date
        ):
            raise ValueError(
                f"Lifecycle dates out of order for event {self.event_id}: require "
                f"declaration {self.declaration_date} <= ex {self.ex_date} <= record "
                f"{self.record_date} <= payment {self.payment_date}. Note: ex == record "
                "is the normal convention under T+1 settlement (US since 2024-05-28); "
                "under T+2 the ex-date is typically one business day before the record date."
            )
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise TypeError(
                f"value must be a number (event {self.event_id}), got {type(self.value).__name__}"
            )
        if not math.isfinite(self.value) or self.value <= 0:
            raise ValueError(
                f"value must be a positive finite number (event {self.event_id}), "
                f"got {self.value!r}"
            )


@dataclass
class DividendEntitlement:
    symbol: str
    shares_held: float
    dividend_per_share: float
    gross_receivable_amount: float
    record_date: date
    payment_date: date
    status: str                         # 'PENDING_PAYMENT', 'PAID'


class CorporateActionEventCalendarEngine:
    """
    Manages corporate action lifecycle events (Declaration -> Ex-Date -> Record Date -> Payment Date),
    queries forward risk windows, and computes position dividend entitlements.
    """
    def __init__(self, events: Optional[List[CorporateActionEvent]] = None):
        self.events_db: Dict[str, List[CorporateActionEvent]] = {}
        self._registered_ids: set = set()
        for event in (events or []):
            self.register_event(event)

    def register_event(self, event: CorporateActionEvent) -> bool:
        """
        Registers a corporate action event. Idempotent on event_id: vendor
        re-broadcasts of an already-registered event are dropped with a warning
        instead of double-counted. Returns True if registered, False if a
        duplicate was ignored.
        """
        if event.event_id in self._registered_ids:
            logger.warning(
                f"Ignoring duplicate Corporate Action event_id {event.event_id} "
                f"({event.event_type}) for {event.symbol} - already registered."
            )
            return False
        if event.symbol not in self.events_db:
            self.events_db[event.symbol] = []
        self.events_db[event.symbol].append(event)
        self._registered_ids.add(event.event_id)
        logger.info(
            f"Registered Corporate Action {event.event_id} ({event.event_type}) "
            f"for {event.symbol} - Ex-Date: {event.ex_date}"
        )
        return True

    def query_upcoming_events(
        self, current_date: date, lookahead_days: int = 5
    ) -> List[CorporateActionEvent]:
        """
        Returns all corporate actions with ex-date falling in range
        [current_date, current_date + lookahead_days], sorted by ex-date.

        The window is measured in calendar days and includes weekends and
        holidays; this module carries no exchange holiday calendar. Scale the
        window accordingly (e.g. a 5-day window spanning a weekend covers at
        most 3 trading days).
        """
        if lookahead_days < 0:
            raise ValueError(f"lookahead_days must be >= 0, got {lookahead_days}")
        _require_date(current_date, "current_date")
        end_date = current_date + timedelta(days=lookahead_days)
        upcoming: List[CorporateActionEvent] = []

        for events in self.events_db.values():
            for ev in events:
                if current_date <= ev.ex_date <= end_date:
                    upcoming.append(ev)

        return sorted(upcoming, key=lambda e: e.ex_date)

    def calculate_dividend_entitlement(
        self,
        symbol: str,
        shares_held_on_record_date: float,
        current_date: date
    ) -> Optional[DividendEntitlement]:
        """
        Calculates the expected cash dividend receivable for the most recent
        cash dividend of `symbol` whose record date has passed
        (current_date >= record_date). Returns None when no cash dividend has
        reached its record date yet.

        `shares_held_on_record_date` must be the position as of the close
        immediately PRECEDING the ex-date: buying on or after the ex-date does
        not create entitlement, and shares sold on or after the ex-date still
        leave the seller entitled. Under US T+1 settlement (since 2024-05-28)
        the ex-date generally coincides with the record date, so the pre-ex
        close position equals the record-date position when no trades occur in
        between.

        Negative positions are rejected: a short seller owes the dividend (a
        payable), which is out of scope for this receivable engine.
        """
        if (
            not isinstance(shares_held_on_record_date, (int, float))
            or isinstance(shares_held_on_record_date, bool)
            or not math.isfinite(shares_held_on_record_date)
            or shares_held_on_record_date < 0
        ):
            raise ValueError(
                "shares_held_on_record_date must be a non-negative finite number, "
                f"got {shares_held_on_record_date!r}; dividends owed on short "
                "positions are a payable, out of scope for this engine"
            )
        _require_date(current_date, "current_date")

        candidates = [
            ev for ev in self.events_db.get(symbol, [])
            if ev.event_type == "CASH_DIVIDEND" and current_date >= ev.record_date
        ]
        if not candidates:
            return None

        ev = max(candidates, key=lambda e: (e.record_date, e.payment_date))
        gross_amount = round(shares_held_on_record_date * ev.value, 2)
        status = "PAID" if current_date >= ev.payment_date else "PENDING_PAYMENT"

        return DividendEntitlement(
            symbol=symbol,
            shares_held=shares_held_on_record_date,
            dividend_per_share=ev.value,
            gross_receivable_amount=gross_amount,
            record_date=ev.record_date,
            payment_date=ev.payment_date,
            status=status
        )

    def reconcile_vendor_feeds(
        self,
        vendor_a_events: List[CorporateActionEvent],
        vendor_b_events: List[CorporateActionEvent]
    ) -> List[str]:
        """
        Reconciles two vendor event feeds and returns a list of discrepancy
        alerts. Checks both directions (events only in Vendor A and only in
        Vendor B), duplicate event_ids within a feed, and mismatches in
        ex_date, record_date, payment_date and value - the entitlement-affecting
        fields. declaration_date differences are deliberately not flagged:
        they typically reflect vendor dissemination lag rather than entitlement
        risk.
        """
        discrepancies: List[str] = []

        def index_by_id(
            events: List[CorporateActionEvent], vendor: str
        ) -> Dict[str, CorporateActionEvent]:
            index: Dict[str, CorporateActionEvent] = {}
            for e in events:
                if e.event_id in index:
                    discrepancies.append(f"Duplicate event_id {e.event_id} in {vendor} feed.")
                else:
                    index[e.event_id] = e
            return index

        map_a = index_by_id(vendor_a_events, "Vendor A")
        map_b = index_by_id(vendor_b_events, "Vendor B")

        for ev_id, ev_a in map_a.items():
            if ev_id not in map_b:
                discrepancies.append(f"Event {ev_id} present in Vendor A but missing in Vendor B.")
                continue
            ev_b = map_b[ev_id]
            for field_name, label in (
                ("ex_date", "Ex-Date"),
                ("record_date", "Record Date"),
                ("payment_date", "Payment Date"),
            ):
                if getattr(ev_a, field_name) != getattr(ev_b, field_name):
                    discrepancies.append(
                        f"{label} mismatch for {ev_id}: Vendor A "
                        f"({getattr(ev_a, field_name)}) vs Vendor B ({getattr(ev_b, field_name)})."
                    )
            if ev_a.value != ev_b.value:
                discrepancies.append(
                    f"Value mismatch for {ev_id}: Vendor A ({ev_a.value}) vs "
                    f"Vendor B ({ev_b.value})."
                )

        for ev_id in map_b:
            if ev_id not in map_a:
                discrepancies.append(f"Event {ev_id} present in Vendor B but missing in Vendor A.")

        return discrepancies

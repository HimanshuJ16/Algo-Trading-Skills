"""Corporate action event calendar integration engine.

Tracks the 4-date corporate action lifecycle (declaration -> ex-date ->
record date -> payment date), queries forward risk windows, computes cash
dividend entitlement receivables, and reconciles two vendor feeds.

Ex-date placement follows FINRA Rule 11140(b): distributions below 25% of the
security's value go ex on the record date (PRE_RECORD), while distributions of
25% or more go ex on the first business day after the payable date
(POST_PAYABLE). Both orderings are valid and must be declared per event.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_TYPES = frozenset({"CASH_DIVIDEND", "STOCK_SPLIT", "RIGHTS_OFFERING", "SPIN_OFF"})

# Which side of the lifecycle the ex-date falls on. Under FINRA Rule 11140(b)
# (as amended by SR-FINRA-2023-017, operative 2024-05-28):
#   PRE_RECORD   - (b)(1) distributions worth less than 25% of the security:
#                  ex-date is the record date (or the preceding business day),
#                  so declaration <= ex <= record <= payment.
#   POST_PAYABLE - (b)(2) distributions of 25% or more (most forward splits,
#                  large special dividends, many spin-offs and rights issues):
#                  ex-date is the FIRST BUSINESS DAY FOLLOWING the payable date,
#                  so declaration <= record <= payment <= ex.
# The convention is a property of the distribution's size relative to the
# security price, which this module cannot observe, so it must be supplied by
# the feed rather than inferred from event_type or value.
EX_DATE_CONVENTIONS = frozenset({"PRE_RECORD", "POST_PAYABLE"})


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
    ex_date_convention: str = "PRE_RECORD"   # one of EX_DATE_CONVENTIONS

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
        if self.ex_date_convention not in EX_DATE_CONVENTIONS:
            raise ValueError(
                f"ex_date_convention {self.ex_date_convention!r} (event {self.event_id}) "
                f"not in {sorted(EX_DATE_CONVENTIONS)}"
            )
        for field_name in ("declaration_date", "ex_date", "record_date", "payment_date"):
            _require_date(getattr(self, field_name), f"{field_name} (event {self.event_id})")
        if self.record_date > self.payment_date:
            raise ValueError(
                f"Lifecycle dates out of order for event {self.event_id}: record "
                f"{self.record_date} must not fall after payment {self.payment_date}."
            )
        if self.declaration_date > min(self.ex_date, self.record_date):
            raise ValueError(
                f"Lifecycle dates out of order for event {self.event_id}: declaration "
                f"{self.declaration_date} must not fall after ex {self.ex_date} or "
                f"record {self.record_date}."
            )
        if self.ex_date_convention == "PRE_RECORD":
            if self.ex_date > self.record_date:
                raise ValueError(
                    f"Lifecycle dates out of order for event {self.event_id}: under the "
                    f"PRE_RECORD convention require ex {self.ex_date} <= record "
                    f"{self.record_date} (ex == record is normal under T+1 settlement, US "
                    "since 2024-05-28; under T+2 the ex-date is typically one business day "
                    "earlier). A distribution of 25% or more of the security's value has "
                    "its ex-date after the payable date instead - declare it with "
                    "ex_date_convention='POST_PAYABLE' (FINRA Rule 11140(b)(2))."
                )
        elif self.payment_date > self.ex_date:
            raise ValueError(
                f"Lifecycle dates out of order for event {self.event_id}: under the "
                f"POST_PAYABLE convention (FINRA Rule 11140(b)(2)) require payment "
                f"{self.payment_date} <= ex {self.ex_date}; the ex-date is the first "
                "business day following the payable date."
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


# Fields whose disagreement between two records of the same event_id changes
# entitlement or ex-date price risk. declaration_date is deliberately excluded:
# it reflects vendor dissemination lag, not entitlement risk.
MATERIAL_FIELDS = (
    ("symbol", "Symbol"),
    ("event_type", "Event Type"),
    ("ex_date_convention", "Ex-Date Convention"),
    ("ex_date", "Ex-Date"),
    ("record_date", "Record Date"),
    ("payment_date", "Payment Date"),
    ("value", "Value"),
)


def _material_differences(a: "CorporateActionEvent", b: "CorporateActionEvent") -> List[str]:
    """Returns 'Label (a_value vs b_value)' for each materially differing field."""
    return [
        f"{label} ({getattr(a, name)} vs {getattr(b, name)})"
        for name, label in MATERIAL_FIELDS
        if getattr(a, name) != getattr(b, name)
    ]


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
        self._events_by_id: Dict[str, CorporateActionEvent] = {}
        for event in (events or []):
            self.register_event(event)

    def register_event(self, event: CorporateActionEvent) -> bool:
        """
        Registers a corporate action event. Idempotent on event_id: vendor
        re-broadcasts of an already-registered event are dropped with a warning
        instead of double-counted. Returns True if registered, False if a
        duplicate was ignored.

        A re-broadcast that differs materially from the stored event (symbol,
        event type, ex-date convention, any of the three lifecycle dates, or
        value) is an AMENDMENT, not a duplicate - ISO 15022 carries these as an
        MT 564 with function REPL. It is still dropped, because blindly
        overwriting a registered event would silently move an ex-date that
        downstream sizing has already acted on, but it is logged at ERROR with
        the differing fields so it can be resolved against the golden source
        rather than disappearing behind a routine duplicate warning.
        """
        if event.event_id in self._registered_ids:
            existing = self._events_by_id[event.event_id]
            differences = _material_differences(existing, event)
            if differences:
                logger.error(
                    f"Corporate Action event_id {event.event_id} re-broadcast for "
                    f"{existing.symbol} DIFFERS from the registered event and was NOT "
                    f"applied - possible amendment (ISO 15022 MT 564 REPL). Resolve "
                    f"against the golden source before the ex-date. Differences: "
                    f"{'; '.join(differences)}."
                )
            else:
                logger.warning(
                    f"Ignoring duplicate Corporate Action event_id {event.event_id} "
                    f"({event.event_type}) for {event.symbol} - already registered."
                )
            return False
        if event.symbol not in self.events_db:
            self.events_db[event.symbol] = []
        self.events_db[event.symbol].append(event)
        self._registered_ids.add(event.event_id)
        self._events_by_id[event.event_id] = event
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

        Events are matched on ex-date, i.e. on when the price adjusts. For a
        POST_PAYABLE event (a distribution of 25% or more) that ex-date falls
        AFTER the payable date, so an alert here does not mean the entitlement
        cut-off is still ahead - that was the record date, already past.
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

        Only ONE dividend is returned - the most recent one past its record
        date. Older receivables that are still unpaid are not accumulated, and
        `gross_receivable_amount` is rounded to 2 decimal places, i.e. it
        assumes a 2-minor-unit currency and carries no currency tag or FX
        conversion. Callers needing a full multi-currency receivable ledger
        must accumulate per event themselves.

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

        latest_key = max((e.record_date, e.payment_date) for e in candidates)
        tied = [e for e in candidates if (e.record_date, e.payment_date) == latest_key]
        if len(tied) > 1:
            # A special dividend paid alongside the regular one shares both dates.
            # Only one is returned, so the other would silently vanish from the
            # receivable; surface it rather than under-accruing.
            logger.warning(
                f"{len(tied)} cash dividends for {symbol} share record date "
                f"{latest_key[0]} and payment date {latest_key[1]} "
                f"({', '.join(sorted(e.event_id for e in tied))}); only the "
                "lowest event_id is returned. A regular plus a special dividend on "
                "the same dates must be accrued separately."
            )
        ev = min(tied, key=lambda e: e.event_id)
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
        Vendor B), duplicate event_ids within a feed, and mismatches in every
        field of MATERIAL_FIELDS - symbol, event type, ex-date convention,
        ex_date, record_date, payment_date and value. Two feeds agreeing on an
        event_id while disagreeing on the symbol or event type is a mapping
        failure that would route an entitlement to the wrong position, so it
        must not pass as agreement. declaration_date differences are
        deliberately not flagged: they typically reflect vendor dissemination
        lag rather than entitlement risk.
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
            for field_name, label in MATERIAL_FIELDS:
                if getattr(ev_a, field_name) != getattr(ev_b, field_name):
                    discrepancies.append(
                        f"{label} mismatch for {ev_id}: Vendor A "
                        f"({getattr(ev_a, field_name)}) vs Vendor B ({getattr(ev_b, field_name)})."
                    )

        for ev_id in map_b:
            if ev_id not in map_a:
                discrepancies.append(f"Event {ev_id} present in Vendor B but missing in Vendor A.")

        return discrepancies

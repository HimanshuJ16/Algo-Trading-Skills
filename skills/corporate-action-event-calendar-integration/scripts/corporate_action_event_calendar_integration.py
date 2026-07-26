import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class CorporateActionEvent:
    event_id: str
    symbol: str
    event_type: str                     # 'CASH_DIVIDEND', 'STOCK_SPLIT', 'RIGHTS_OFFERING', 'SPIN_OFF'
    declaration_date: date
    ex_date: date
    record_date: date
    payment_date: date
    value: float                        # e.g. $1.50 per share or 2.0 split ratio

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
    def __init__(self, events: List[CorporateActionEvent] = None):
        self.events_db: Dict[str, List[CorporateActionEvent]] = {}
        for event in (events or []):
            self.register_event(event)

    def register_event(self, event: CorporateActionEvent):
        if event.symbol not in self.events_db:
            self.events_db[event.symbol] = []
        self.events_db[event.symbol].append(event)
        logger.info(f"Registered Corporate Action {event.event_id} ({event.event_type}) for {event.symbol} - Ex-Date: {event.ex_date}")

    def query_upcoming_events(self, current_date: date, lookahead_days: int = 5) -> List[CorporateActionEvent]:
        """
        Returns all corporate actions with ex-date falling in range [current_date, current_date + lookahead_days].
        """
        end_date = current_date + timedelta(days=lookahead_days)
        upcoming: List[CorporateActionEvent] = []

        for symbol, events in self.events_db.items():
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
        Calculates expected cash dividend receivable for shares held at the close of Record Date.
        """
        if symbol not in self.events_db:
            return None

        for ev in self.events_db[symbol]:
            if ev.event_type == 'CASH_DIVIDEND' and current_date >= ev.record_date:
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

        return None

    def reconcile_vendor_feeds(
        self, 
        vendor_a_events: List[CorporateActionEvent], 
        vendor_b_events: List[CorporateActionEvent]
    ) -> List[str]:
        """
        Reconciles two vendor event feeds and returns a list of discrepancy alerts.
        """
        discrepancies: List[str] = []
        map_a = {e.event_id: e for e in vendor_a_events}
        map_b = {e.event_id: e for e in vendor_b_events}

        for ev_id, ev_a in map_a.items():
            if ev_id not in map_b:
                discrepancies.append(f"Event {ev_id} present in Vendor A but missing in Vendor B.")
            else:
                ev_b = map_b[ev_id]
                if ev_a.ex_date != ev_b.ex_date:
                    discrepancies.append(f"Ex-Date mismatch for {ev_id}: Vendor A ({ev_a.ex_date}) vs Vendor B ({ev_b.ex_date}).")
                if ev_a.value != ev_b.value:
                    discrepancies.append(f"Value mismatch for {ev_id}: Vendor A ({ev_a.value}) vs Vendor B ({ev_b.value}).")

        return discrepancies

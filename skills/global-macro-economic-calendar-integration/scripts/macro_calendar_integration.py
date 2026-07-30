import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MacroEconomicEvent:
    event_id: str
    event_name: str                     # e.g. 'FOMC_RATE_DECISION', 'US_CPI_YOY', 'NFP_JOBS'
    currency: str                       # e.g. 'USD', 'EUR', 'GBP'
    release_timestamp_utc: float        # Epoch seconds
    impact_severity: str                # 'HIGH_IMPACT', 'MEDIUM_IMPACT', 'LOW_IMPACT'
    consensus_forecast: Optional[float] = None
    forecast_std_dev: Optional[float] = None
    actual_release: Optional[float] = None

@dataclass
class MacroCalendarAuditReport:
    current_timestamp_utc: float
    is_trading_permitted: bool
    is_blackout_active: bool
    active_blackout_event: Optional[MacroEconomicEvent]
    should_cancel_open_limit_orders: bool
    macro_surprise_index: Optional[float] # (Actual - Forecast) / StdDev
    status: str                         # 'MACRO_TRADING_PERMITTED', 'MACRO_BLACKOUT_ACTIVE'
    audit_notes: str

class GlobalMacroCalendarEngine:
    """
    Quantitative macro engine for integrating global economic calendars (FOMC, CPI, NFP),
    calculating surprise metrics, and enforcing automated pre/post-event trading blackout windows.
    """
    def __init__(
        self,
        pre_event_buffer_sec: float = 900.0,   # 15 minutes before event
        post_event_buffer_sec: float = 900.0   # 15 minutes after event
    ):
        self.pre_event_buffer_sec = pre_event_buffer_sec
        self.post_event_buffer_sec = post_event_buffer_sec
        self.scheduled_events: List[MacroEconomicEvent] = []

    def add_event(self, event: MacroEconomicEvent):
        self.scheduled_events.append(event)
        self.scheduled_events.sort(key=lambda e: e.release_timestamp_utc)

    def calculate_surprise_index(self, event: MacroEconomicEvent) -> Optional[float]:
        """
        Calculates Macro Surprise Deviation Index: S = (Actual - Forecast) / ForecastStdDev
        """
        if event.actual_release is None or event.consensus_forecast is None:
            return None

        diff = event.actual_release - event.consensus_forecast
        std_dev = event.forecast_std_dev if (event.forecast_std_dev and event.forecast_std_dev > 0) else 1.0
        return round(diff / std_dev, 4)

    def audit_macro_trading_status(self, current_time_utc: float) -> MacroCalendarAuditReport:
        """
        Audits current timestamp against scheduled macro events to enforce blackout rules.
        """
        active_blackout_event: Optional[MacroEconomicEvent] = None

        for event in self.scheduled_events:
            # Evaluate only HIGH_IMPACT and MEDIUM_IMPACT events
            if event.impact_severity not in ("HIGH_IMPACT", "MEDIUM_IMPACT"):
                continue

            blackout_start = event.release_timestamp_utc - self.pre_event_buffer_sec
            blackout_end = event.release_timestamp_utc + self.post_event_buffer_sec

            if blackout_start <= current_time_utc <= blackout_end:
                active_blackout_event = event
                break

        if active_blackout_event:
            surprise = self.calculate_surprise_index(active_blackout_event)
            status = "MACRO_BLACKOUT_ACTIVE"
            notes = (
                f"MACRO RISK BLACKOUT ACTIVE [{active_blackout_event.event_name} - {active_blackout_event.currency}]: "
                f"Release Time = {active_blackout_event.release_timestamp_utc}. Trading PAUSED. "
                f"Mass Cancel Limit Orders TRIGGERED."
            )
            logger.warning(notes)
            return MacroCalendarAuditReport(
                current_timestamp_utc=current_time_utc,
                is_trading_permitted=False,
                is_blackout_active=True,
                active_blackout_event=active_blackout_event,
                should_cancel_open_limit_orders=True,
                macro_surprise_index=surprise,
                status=status,
                audit_notes=notes
            )

        status = "MACRO_TRADING_PERMITTED"
        notes = f"MACRO CALENDAR CLEAR: Current time {current_time_utc} is outside all macro blackout windows."
        logger.info(notes)
        return MacroCalendarAuditReport(
            current_timestamp_utc=current_time_utc,
            is_trading_permitted=True,
            is_blackout_active=False,
            active_blackout_event=None,
            should_cancel_open_limit_orders=False,
            macro_surprise_index=None,
            status=status,
            audit_notes=notes
        )

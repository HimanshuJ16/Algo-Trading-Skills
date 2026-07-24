"""
corporate-action-adjusted-backtesting: Production-grade corporate action processor,
backward split/dividend price & volume adjuster, and ex-date dividend cash credit calculator.
"""
from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CorporateActionError(ValueError):
    """Raised when corporate action adjustments fail or invalid event parameters exist."""
    pass


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"


@dataclass
class CorporateActionEvent:
    symbol: str
    ex_date: datetime.date
    action_type: ActionType
    ratio: float = 1.0          # e.g. 2.0 for 2-for-1 split; 0.2 for 1-for-5 reverse split
    cash_amount: float = 0.0    # Cash dividend per share


@dataclass
class RawBar:
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class AdjustedBar:
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float
    price_factor: float
    volume_factor: float


class CorporateActionAdjuster:
    """
    Applies backward ratio adjustments to historical price and volume series for stock splits,
    reverse splits, and cash dividends, and calculates dividend cash flow credits.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.events: List[CorporateActionEvent] = []

    def add_event(self, event: CorporateActionEvent):
        """Registers a corporate action event."""
        if event.symbol.upper() != self.symbol:
            raise CorporateActionError(f"Event symbol '{event.symbol}' does not match adjuster symbol '{self.symbol}'.")
        self.events.append(event)
        # Keep events sorted by ex_date
        self.events.sort(key=lambda e: e.ex_date)

    def adjust_series(self, raw_bars: List[RawBar]) -> List[AdjustedBar]:
        """
        Applies backward adjustment factors to raw historical OHLCV bars.
        Bars are assumed to be sorted in chronological order (oldest to newest).
        Adjustment factors apply strictly to bars BEFORE the ex-date.
        """
        if not raw_bars:
            return []

        sorted_bars = sorted(raw_bars, key=lambda b: b.date)

        cum_price_factor = 1.0
        cum_vol_factor = 1.0

        # Create lookup for events by ex_date
        event_dict: Dict[datetime.date, List[CorporateActionEvent]] = {}
        for ev in self.events:
            if ev.ex_date not in event_dict:
                event_dict[ev.ex_date] = []
            event_dict[ev.ex_date].append(ev)

        temp_adjusted: List[AdjustedBar] = []

        # Process in reverse chronological order
        for bar in reversed(sorted_bars):
            # 1. Apply current cumulative adjustment factors to current bar (on or after ex-date)
            adj_open = round(bar.open * cum_price_factor, 4)
            adj_high = round(bar.high * cum_price_factor, 4)
            adj_low = round(bar.low * cum_price_factor, 4)
            adj_close = round(bar.close * cum_price_factor, 4)
            adj_volume = round(bar.volume * cum_vol_factor, 2)

            temp_adjusted.append(
                AdjustedBar(
                    date=bar.date,
                    open=adj_open,
                    high=adj_high,
                    low=adj_low,
                    close=adj_close,
                    volume=adj_volume,
                    price_factor=cum_price_factor,
                    volume_factor=cum_vol_factor,
                )
            )

            # 2. Update cumulative factor for prior bars if current bar is ex-date
            if bar.date in event_dict:
                for ev in event_dict[bar.date]:
                    if ev.action_type in (ActionType.SPLIT, ActionType.REVERSE_SPLIT):
                        split_ratio = max(ev.ratio, 1e-5)
                        cum_price_factor /= split_ratio
                        cum_vol_factor *= split_ratio
                    elif ev.action_type == ActionType.CASH_DIVIDEND:
                        if bar.close > 0 and ev.cash_amount > 0:
                            div_factor = 1.0 - (ev.cash_amount / bar.close)
                            cum_price_factor *= max(div_factor, 0.001)

        # Reverse back to chronological order
        adjusted_bars = list(reversed(temp_adjusted))
        logger.info(f"Adjusted {len(adjusted_bars)} historical bars for '{self.symbol}'. Final price factor: {cum_price_factor:.4f}")
        return adjusted_bars

    def calculate_dividend_cash_credit(self, ex_date: datetime.date, position_qty: float) -> float:
        """Calculates cash dividend payout on ex-dividend date for open position."""
        if position_qty <= 0:
            return 0.0

        cash_credit = 0.0
        for ev in self.events:
            if ev.ex_date == ex_date and ev.action_type == ActionType.CASH_DIVIDEND:
                cash_credit += position_qty * ev.cash_amount

        if cash_credit > 0:
            logger.info(f"Dividend cash credit on {ex_date} for '{self.symbol}': ${cash_credit:,.2f} ({position_qty} shares @ ${ev.cash_amount}/sh)")
        return cash_credit

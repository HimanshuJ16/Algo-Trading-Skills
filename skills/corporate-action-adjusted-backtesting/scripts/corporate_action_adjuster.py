import logging
from dataclasses import dataclass
from datetime import date
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CorporateActionEvent:
    ex_date: date
    event_type: str                     # 'SPLIT', 'REVERSE_SPLIT', 'DIVIDEND'
    value: float                        # Ratio (e.g. 2.0 for 2-for-1 split) or Cash Dividend Amount ($1.50)

@dataclass
class BarData:
    dt: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    raw_volume: float

@dataclass
class AdjustedBarData(BarData):
    caf: float                          # Cumulative Adjustment Factor
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    adj_volume: float

class CorporateActionAdjuster:
    """
    Computes Cumulative Adjustment Factors (CAF) and generates backward-adjusted
    price/volume series for backtesting while preserving raw prices for execution.
    """
    def __init__(self, events: List[CorporateActionEvent] = None):
        self.events = sorted(events or [], key=lambda e: e.ex_date)

    def compute_caf_series(self, bars: List[BarData]) -> Dict[date, float]:
        """
        Computes Cumulative Adjustment Factor (CAF) for each bar date.
        CAF is normalized to 1.0 on the most recent date and ex-date.
        Pre-ex-date historical prices are adjusted.
        """
        if not bars:
            return {}

        sorted_bars = sorted(bars, key=lambda b: b.dt)
        caf_map: Dict[date, float] = {}
        
        # Build single-event adjustment factor lookup on ex-date
        event_factor_map: Dict[date, float] = {}
        for bar in sorted_bars:
            dt = bar.dt
            factor = 1.0
            for event in self.events:
                if event.ex_date == dt:
                    if event.event_type == 'SPLIT':
                        # 2-for-1 split: prices before ex-date multiplied by 0.5 (1 / 2.0)
                        factor *= (1.0 / event.value)
                    elif event.event_type == 'REVERSE_SPLIT':
                        # 1-for-5 reverse split: prices before ex-date multiplied by 5.0
                        factor *= event.value
                    elif event.event_type == 'DIVIDEND':
                        # Cash dividend factor: (Close - Div) / Close
                        if bar.raw_close > 0:
                            factor *= (1.0 - (event.value / bar.raw_close))
            event_factor_map[dt] = factor

        # Compute cumulative factor moving backward from latest date
        # Note: Event on ex-date applies to all dates BEFORE ex-date.
        cum_factor = 1.0
        for bar in reversed(sorted_bars):
            caf_map[bar.dt] = round(cum_factor, 6)
            cum_factor *= event_factor_map[bar.dt]

        return caf_map

    def adjust_bars(self, bars: List[BarData]) -> List[AdjustedBarData]:
        """
        Returns a list of AdjustedBarData with both raw and adjusted OHLCV fields.
        """
        caf_map = self.compute_caf_series(bars)
        adjusted_list: List[AdjustedBarData] = []

        for bar in bars:
            caf = caf_map.get(bar.dt, 1.0)
            adj_bar = AdjustedBarData(
                dt=bar.dt,
                raw_open=bar.raw_open,
                raw_high=bar.raw_high,
                raw_low=bar.raw_low,
                raw_close=bar.raw_close,
                raw_volume=bar.raw_volume,
                caf=caf,
                adj_open=round(bar.raw_open * caf, 4),
                adj_high=round(bar.raw_high * caf, 4),
                adj_low=round(bar.raw_low * caf, 4),
                adj_close=round(bar.raw_close * caf, 4),
                adj_volume=round(bar.raw_volume / caf, 2) if caf > 0 else bar.raw_volume
            )
            adjusted_list.append(adj_bar)

        return adjusted_list

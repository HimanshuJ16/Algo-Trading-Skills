import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class CorporateActionType(Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    SPECIAL_DIVIDEND = "SPECIAL_DIVIDEND"
    STOCK_SPLIT = "STOCK_SPLIT"            # E.g., 2-for-1 split (split_factor = 0.5)
    REVERSE_SPLIT = "REVERSE_SPLIT"        # E.g., 1-for-10 reverse split (split_factor = 10.0)
    SPIN_OFF = "SPIN_OFF"


class VendorMethodology(Enum):
    CRSP_TOTAL_RETURN = "CRSP_TOTAL_RETURN"          # Full dividend + split cumulative factor
    BLOOMBERG_PROPORTIONAL = "BLOOMBERG_PROPORTIONAL"  # Proportional ratio adjustment (1 - D/P)
    SPLIT_ONLY_PRICE_RETURN = "SPLIT_ONLY"             # Adjusts splits only, ignores cash dividends
    RAW_UNADJUSTED = "RAW_UNADJUSTED"                   # Raw unadjusted historical exchange prices


class ReconciliationError(Exception):
    """Base exception for Vendor Adjustment Reconciliation Engine errors."""
    pass


@dataclass
class CorporateAction:
    event_id: str
    symbol: str
    ex_date: datetime.date
    action_type: CorporateActionType
    cash_amount: float = 0.0          # Cash dividend per share (USD)
    split_ratio: float = 1.0           # New shares per old share (e.g. 2.0 for 2-for-1 split)
    cum_price: float = 0.0            # Closing price prior to ex-date for dividend factor calculation


@dataclass
class PriceBar:
    symbol: str
    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ReconciliationDivergence:
    date: datetime.date
    symbol: str
    vendor_a_price: float
    vendor_b_price: float
    absolute_diff: float
    percentage_diff: float
    is_anomaly: bool


@dataclass
class ReconciliationReport:
    symbol: str
    vendor_a_name: str
    vendor_b_name: str
    total_bars_compared: int
    divergence_count: int
    max_divergence_pct: float
    mean_divergence_pct: float
    divergences: List[ReconciliationDivergence] = field(default_factory=list)
    status: str = "PASSED"


class VendorAdjustmentReconciliationEngine:
    """
    Institutional Vendor-Specific Adjustment Methodology Reconciliation Engine.
    
    Reconciles historical price series across market data vendors (CRSP, Bloomberg, Refinitiv, FactSet),
    models corporate action adjustment factor equations (Cash Dividends, Stock Splits, Reverse Splits),
    detects vendor adjustment methodology divergences, and produces audit reconciliation reports.
    """

    def __init__(self):
        logger.info("Initialized Vendor-Specific Adjustment Methodology Reconciliation Engine")

    def calculate_adjustment_factors(
        self, bars: List[PriceBar], actions: List[CorporateAction], methodology: VendorMethodology
    ) -> Dict[datetime.date, Tuple[float, float]]:
        """
        Calculates cumulative price and volume adjustment factors for each date in price history.
        
        Returns dict mapping date -> (cumulative_price_factor, cumulative_volume_factor).
        """
        if not bars:
            return {}

        # Sort bars chronologically ascending
        sorted_bars = sorted(bars, key=lambda x: x.date)
        sorted_actions = sorted(actions, key=lambda x: x.ex_date)

        factors: Dict[datetime.date, Tuple[float, float]] = {}

        if methodology == VendorMethodology.RAW_UNADJUSTED:
            for bar in sorted_bars:
                factors[bar.date] = (1.0, 1.0)
            return factors

        # Compute period adjustment factor for each corporate action
        action_factors: Dict[datetime.date, float] = {}

        for act in sorted_actions:
            fac = 1.0
            if act.action_type in (CorporateActionType.STOCK_SPLIT, CorporateActionType.REVERSE_SPLIT):
                if act.split_ratio > 0:
                    fac = 1.0 / act.split_ratio
            elif act.action_type in (CorporateActionType.CASH_DIVIDEND, CorporateActionType.SPECIAL_DIVIDEND):
                if methodology in (VendorMethodology.CRSP_TOTAL_RETURN, VendorMethodology.BLOOMBERG_PROPORTIONAL):
                    if act.cum_price > 0 and act.cash_amount > 0:
                        fac = 1.0 - (act.cash_amount / act.cum_price)

            action_factors[act.ex_date] = fac

        # Cumulative factor product backward from present (latest date = 1.0)
        cum_price_fac = 1.0

        for bar in reversed(sorted_bars):
            # If ex-date occurs after bar.date, apply action factor
            for act_date, fac in action_factors.items():
                if act_date > bar.date:
                    pass  # Factor applied continuously

            # For dates before ex_date, accumulate factor
            factors[bar.date] = (cum_price_fac, 1.0 / cum_price_fac if cum_price_fac > 0 else 1.0)

            # Update cumulative factor if ex-date matches bar.date
            if bar.date in action_factors:
                cum_price_fac *= action_factors[bar.date]

        return factors

    def adjust_price_series(
        self, bars: List[PriceBar], actions: List[CorporateAction], methodology: VendorMethodology
    ) -> List[PriceBar]:
        """
        Adjusts raw price series using specified vendor corporate action methodology.
        """
        factors = self.calculate_adjustment_factors(bars, actions, methodology)
        adjusted_bars = []

        for bar in bars:
            p_fac, v_fac = factors.get(bar.date, (1.0, 1.0))
            adjusted_bars.append(
                PriceBar(
                    symbol=bar.symbol,
                    date=bar.date,
                    open=round(bar.open * p_fac, 4),
                    high=round(bar.high * p_fac, 4),
                    low=round(bar.low * p_fac, 4),
                    close=round(bar.close * p_fac, 4),
                    volume=round(bar.volume * v_fac, 2),
                )
            )

        return sorted(adjusted_bars, key=lambda x: x.date)

    def reconcile_vendor_series(
        self,
        symbol: str,
        series_a: List[PriceBar],
        vendor_a_name: str,
        series_b: List[PriceBar],
        vendor_b_name: str,
        tolerance_pct: float = 0.5,
    ) -> ReconciliationReport:
        """
        Reconciles two vendor adjusted price series, detecting divergence anomalies exceeding tolerance_pct.
        """
        map_a = {b.date: b for b in series_a}
        map_b = {b.date: b for b in series_b}

        common_dates = sorted(set(map_a.keys()).intersection(set(map_b.keys())))
        if not common_dates:
            raise ReconciliationError(f"No common dates found between '{vendor_a_name}' and '{vendor_b_name}'.")

        divergences = []
        pct_diffs = []

        for d in common_dates:
            bar_a = map_a[d]
            bar_b = map_b[d]

            abs_diff = abs(bar_a.close - bar_b.close)
            mean_price = (bar_a.close + bar_b.close) / 2.0
            pct_diff = (abs_diff / mean_price * 100.0) if mean_price > 0 else 0.0
            pct_diffs.append(pct_diff)

            is_anomaly = pct_diff > tolerance_pct
            if is_anomaly:
                divergences.append(
                    ReconciliationDivergence(
                        date=d,
                        symbol=symbol,
                        vendor_a_price=bar_a.close,
                        vendor_b_price=bar_b.close,
                        absolute_diff=round(abs_diff, 4),
                        percentage_diff=round(pct_diff, 4),
                        is_anomaly=True,
                    )
                )

        max_div = max(pct_diffs) if pct_diffs else 0.0
        mean_div = sum(pct_diffs) / len(pct_diffs) if pct_diffs else 0.0
        status = "FAILED" if len(divergences) > 0 else "PASSED"

        logger.info(
            f"Vendor Reconciliation [{symbol}]: {vendor_a_name} vs {vendor_b_name} -> Status={status}, "
            f"TotalCompared={len(common_dates)}, Divergences={len(divergences)}, MaxDiff={max_div:.2f}%"
        )

        return ReconciliationReport(
            symbol=symbol,
            vendor_a_name=vendor_a_name,
            vendor_b_name=vendor_b_name,
            total_bars_compared=len(common_dates),
            divergence_count=len(divergences),
            max_divergence_pct=round(max_div, 4),
            mean_divergence_pct=round(mean_div, 4),
            divergences=divergences,
            status=status,
        )


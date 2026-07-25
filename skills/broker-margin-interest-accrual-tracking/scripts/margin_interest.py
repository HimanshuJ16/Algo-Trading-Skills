"""
broker-margin-interest-accrual-tracking: Tiered margin interest rate calculator,
weekend compounding engine, and net P&L haircut deduction module.
"""
from dataclasses import dataclass, field
import datetime
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MarginRateTier:
    min_balance_usd: float
    max_balance_usd: float
    apr: float  # e.g., 0.065 for 6.5% APR


@dataclass
class DailyAccrualRecord:
    date: datetime.date
    debit_balance_usd: float
    is_weekend: bool
    days_accrued: int  # 1 for weekday, 3 for Friday
    daily_interest_usd: float


@dataclass
class MarginInterestSummary:
    total_days_held: int
    total_interest_accrued_usd: float
    gross_pnl_usd: float
    adjusted_net_pnl_usd: float
    daily_records: List[DailyAccrualRecord]


DEFAULT_USD_TIERS: List[MarginRateTier] = [
    MarginRateTier(0.0, 100000.0, 0.0650),        # 0 - 100k @ 6.50%
    MarginRateTier(100000.0, 1000000.0, 0.0580),   # 100k - 1M @ 5.80%
    MarginRateTier(1000000.0, float("inf"), 0.0520) # > 1M @ 5.20%
]


class MarginInterestTracker:
    """
    Calculates exact daily and weekend margin interest accruals using tiered APR schedules,
    and deducts interest expenses from strategy P&L.
    """

    def __init__(
        self,
        rate_tiers: Optional[List[MarginRateTier]] = None,
        day_count_convention: int = 365,
    ):
        self.rate_tiers = rate_tiers or DEFAULT_USD_TIERS
        self.day_count_convention = day_count_convention

    def get_effective_apr(self, debit_balance_usd: float) -> float:
        """Determines effective APR for a given debit balance across rate tiers."""
        if debit_balance_usd <= 0:
            return 0.0

        for tier in self.rate_tiers:
            if tier.min_balance_usd <= debit_balance_usd < tier.max_balance_usd:
                return tier.apr
        return self.rate_tiers[-1].apr

    def calculate_interest_accrual(
        self,
        start_date: datetime.date,
        holding_days: int,
        daily_debit_balance_usd: float,
        gross_pnl_usd: float = 0.0,
    ) -> MarginInterestSummary:
        """
        Simulates daily margin interest accrual over holding_days, correctly accounting
        for weekend 3-day accruals on Fridays.
        """
        if daily_debit_balance_usd <= 0 or holding_days <= 0:
            return MarginInterestSummary(
                total_days_held=holding_days,
                total_interest_accrued_usd=0.0,
                gross_pnl_usd=gross_pnl_usd,
                adjusted_net_pnl_usd=gross_pnl_usd,
                daily_records=[],
            )

        apr = self.get_effective_apr(daily_debit_balance_usd)
        daily_rate = apr / float(self.day_count_convention)

        records: List[DailyAccrualRecord] = []
        total_interest = 0.0
        curr_date = start_date

        day_count = 0
        while day_count < holding_days:
            # Friday check: if day is Friday (weekday() == 4), charge 3 days (Fri, Sat, Sun)
            is_friday = (curr_date.weekday() == 4)
            days_to_charge = 3 if is_friday else 1

            # Adjust if days_to_charge exceeds remaining holding_days
            days_actual = min(days_to_charge, holding_days - day_count)

            day_interest = daily_debit_balance_usd * daily_rate * days_actual
            total_interest += day_interest

            records.append(DailyAccrualRecord(
                date=curr_date,
                debit_balance_usd=daily_debit_balance_usd,
                is_weekend=is_friday,
                days_accrued=days_actual,
                daily_interest_usd=day_interest,
            ))

            day_count += days_actual
            curr_date += datetime.timedelta(days=days_actual)

        adjusted_pnl = gross_pnl_usd - total_interest
        logger.info(
            f"Margin Interest Accrued: ${total_interest:.2f} over {holding_days} days "
            f"(Balance: ${daily_debit_balance_usd:,.2f} @ {apr:.2%} APR). Adjusted Net PnL: ${adjusted_pnl:.2f}"
        )

        return MarginInterestSummary(
            total_days_held=holding_days,
            total_interest_accrued_usd=total_interest,
            gross_pnl_usd=gross_pnl_usd,
            adjusted_net_pnl_usd=adjusted_pnl,
            daily_records=records,
        )

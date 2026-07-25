import datetime
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MarginRateTier:
    """Represents a bracket for tiered margin interest rates."""
    min_balance_usd: float
    max_balance_usd: float
    apr: float  # e.g., 0.065 for 6.5% APR


@dataclass
class DailyAccrualRecord:
    """Record of interest accrual for a specific day."""
    date: datetime.date
    debit_balance_usd: float
    short_market_value_usd: float
    is_weekend: bool
    is_holiday: bool
    days_accrued: int  # e.g., 3 for Friday across weekend
    margin_interest_usd: float
    short_borrow_fee_usd: float
    total_daily_cost_usd: float


@dataclass
class MarginInterestSummary:
    """Summary of margin interest and borrow fees over a holding period."""
    total_days_held: int
    total_margin_interest_usd: float
    total_borrow_fees_usd: float
    gross_pnl_usd: float
    adjusted_net_pnl_usd: float
    daily_records: List[DailyAccrualRecord]


# IBKR-style blended tiers (progressive)
DEFAULT_BLENDED_TIERS: List[MarginRateTier] = [
    MarginRateTier(0.0, 100000.0, 0.0683),          # First 100k @ 6.83%
    MarginRateTier(100000.0, 1000000.0, 0.0633),    # Next 900k @ 6.33%
    MarginRateTier(1000000.0, 50000000.0, 0.0603),  # Next 49M @ 6.03%
    MarginRateTier(50000000.0, float("inf"), 0.0583) # Above 50M @ 5.83%
]

class MarginInterestTracker:
    """
    Calculates exact daily and weekend margin interest accruals using tiered APR schedules.
    Supports blended progressive tiers (e.g., IBKR) and tracks both margin debt and short borrow fees.
    """

    def __init__(
        self,
        rate_tiers: Optional[List[MarginRateTier]] = None,
        day_count_convention_margin: int = 360, # US brokers typically use 360 for margin interest
        day_count_convention_borrow: int = 360, # Standard for US equities hard-to-borrow fees
        is_blended_rate: bool = True
    ):
        self.rate_tiers = rate_tiers or DEFAULT_BLENDED_TIERS
        # Sort tiers to ensure correct progressive calculation
        self.rate_tiers.sort(key=lambda x: x.min_balance_usd)
        self.day_count_convention_margin = day_count_convention_margin
        self.day_count_convention_borrow = day_count_convention_borrow
        self.is_blended_rate = is_blended_rate
        self.holidays: set[datetime.date] = set()

    def add_holidays(self, holidays: List[datetime.date]):
        """Adds a list of non-trading dates that require special accrual handling if needed."""
        self.holidays.update(holidays)

    def get_effective_apr(self, debit_balance_usd: float) -> float:
        """Determines effective APR for a given debit balance across rate tiers."""
        if debit_balance_usd <= 0:
            return 0.0

        if not self.is_blended_rate:
            for tier in self.rate_tiers:
                if tier.min_balance_usd <= debit_balance_usd < tier.max_balance_usd:
                    return tier.apr
            return self.rate_tiers[-1].apr

        # Blended rate (Progressive tiers)
        total_annual_interest = 0.0
        remaining_balance = debit_balance_usd

        for tier in self.rate_tiers:
            if remaining_balance <= 0:
                break
            tier_width = tier.max_balance_usd - tier.min_balance_usd
            applicable_balance_in_tier = min(remaining_balance, tier_width)
            total_annual_interest += applicable_balance_in_tier * tier.apr
            remaining_balance -= applicable_balance_in_tier

        return total_annual_interest / debit_balance_usd

    def calculate_interest_accrual(
        self,
        start_date: datetime.date,
        holding_days: int,
        daily_debit_balance_usd: float,
        daily_short_mv_usd: float = 0.0,
        short_borrow_fee_apr: float = 0.0,
        gross_pnl_usd: float = 0.0,
    ) -> MarginInterestSummary:
        """
        Simulates daily margin interest accrual over holding_days, correctly accounting
        for weekend (T+N settlement) interest constraints.
        """
        if (daily_debit_balance_usd <= 0 and daily_short_mv_usd <= 0) or holding_days <= 0:
            return MarginInterestSummary(
                total_days_held=holding_days,
                total_margin_interest_usd=0.0,
                total_borrow_fees_usd=0.0,
                gross_pnl_usd=gross_pnl_usd,
                adjusted_net_pnl_usd=gross_pnl_usd,
                daily_records=[],
            )

        effective_margin_apr = self.get_effective_apr(daily_debit_balance_usd)
        daily_margin_rate = effective_margin_apr / float(self.day_count_convention_margin)
        daily_borrow_rate = short_borrow_fee_apr / float(self.day_count_convention_borrow)

        records: List[DailyAccrualRecord] = []
        total_margin_interest = 0.0
        total_borrow_fees = 0.0
        curr_date = start_date

        day_count = 0
        while day_count < holding_days:
            # Weekend accrual: positions held over Friday night accrue 3 days of interest
            # Note: actual broker mechanics depend on settlement (T+1), but standard accrual assumes
            # if you hold end-of-day Friday, you pay for Fri, Sat, Sun.
            is_friday = (curr_date.weekday() == 4)
            is_holiday = curr_date in self.holidays
            
            # Basic model: charge 3 days on Friday. If a holiday falls on Monday, sometimes it's 4 days.
            # For this standard implementation, we assume 1 day normally, 3 on Friday.
            days_to_charge = 3 if is_friday else 1

            # Adjust if days_to_charge exceeds remaining holding_days (e.g. closing on Saturday, which is non-standard)
            days_actual = min(days_to_charge, holding_days - day_count)

            day_margin_interest = daily_debit_balance_usd * daily_margin_rate * days_actual
            day_borrow_fee = daily_short_mv_usd * daily_borrow_rate * days_actual
            
            total_margin_interest += day_margin_interest
            total_borrow_fees += day_borrow_fee

            records.append(DailyAccrualRecord(
                date=curr_date,
                debit_balance_usd=daily_debit_balance_usd,
                short_market_value_usd=daily_short_mv_usd,
                is_weekend=is_friday,
                is_holiday=is_holiday,
                days_accrued=days_actual,
                margin_interest_usd=day_margin_interest,
                short_borrow_fee_usd=day_borrow_fee,
                total_daily_cost_usd=day_margin_interest + day_borrow_fee
            ))

            day_count += days_actual
            curr_date += datetime.timedelta(days=days_actual)

            # Skip Saturday and Sunday in dates if Friday jumped 3 days
            if is_friday and days_actual == 3:
                pass # curr_date is now Monday

        total_cost = total_margin_interest + total_borrow_fees
        adjusted_pnl = gross_pnl_usd - total_cost

        logger.info(
            f"Margin Accrued: ${total_margin_interest:.2f} | Borrow Fees: ${total_borrow_fees:.2f} "
            f"over {holding_days} days. Adjusted Net PnL: ${adjusted_pnl:.2f}"
        )

        return MarginInterestSummary(
            total_days_held=holding_days,
            total_margin_interest_usd=total_margin_interest,
            total_borrow_fees_usd=total_borrow_fees,
            gross_pnl_usd=gross_pnl_usd,
            adjusted_net_pnl_usd=adjusted_pnl,
            daily_records=records,
        )

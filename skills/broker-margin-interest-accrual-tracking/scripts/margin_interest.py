"""
broker-margin-interest-accrual-tracking:
Margin loan interest and short-borrow fee accrual for leveraged and short
strategies, and deduction of that financing cost from gross P&L.

The one invariant that matters
------------------------------
**Financing accrues on calendar days, not trading days.** A debit balance
carried from Friday's close to Monday's close costs three days of interest,
not one — not because settlement is slow, but because the balance still
exists on Saturday and Sunday and interest is computed on the daily balance.
The same is true across holidays.

This has a blunt consequence for how you call this module: the total cost over
a window depends **only on the number of calendar days in the window**. Batching
Saturday and Sunday into Friday's ledger row changes the granularity of the
ledger, never the total. Anyone who "adds a weekend multiplier" on top of a
calendar-day count is double-charging; anyone who feeds this module a count of
*trading* days is under-charging by roughly 2/7 (~40% too little for a
multi-week hold).

``holding_days`` in :meth:`MarginInterestTracker.calculate_interest_accrual` is
therefore a **calendar**-day count. If you have two dates, prefer
:meth:`MarginInterestTracker.accrue_daily_balances`, which derives the day count
from the dates themselves and cannot be miscounted.

Rate schedules drift
--------------------
``DEFAULT_BLENDED_TIERS`` is a dated illustration, not a live schedule. IBKR
publishes margin rates as a spread over a benchmark (Fed Funds for USD), so the
absolute APRs move with monetary policy — the tier-1 USD rate was near 6.8% in
the 2023-24 environment and near 5.1% in 2026. Pull your broker's current table
and build tiers with :func:`tiers_from_benchmark` or explicit
:class:`MarginRateTier` values. Hard-coding last year's APRs is the most common
way this calculation goes quietly wrong.

Scope limits the caller must respect
------------------------------------
  - **Simple accrual, no compounding.** Brokers accrue daily and post the
    accrued balance monthly (IBKR: the third business day of the following
    month), so interest begins earning interest only after it is posted. This
    module sums daily charges over the window without folding them back into
    the debit balance. Over a few weeks the difference is negligible; over
    multi-year holds at high rates, model the monthly posting yourself.
  - **Short borrow fees are charged on collateral, not on market value.** At
    IBKR the collateral is 102% of the prior day's settlement price rounded up
    to the next whole dollar, times shares — a convention that follows the 102%
    initial collateral standard for US securities loans. Set
    ``short_collateral_markup=1.02``, or pass an exactly computed
    ``short_collateral_usd`` per day, to match a broker statement. The default
    of 1.0 charges on raw market value and therefore **understates** the fee.
  - **Interest earned on short sale proceeds is not modelled.** The economic
    cost of a short is the borrow fee *net* of the rebate on the proceeds. For
    general-collateral names that rebate is material; this module reports the
    gross borrow fee only, so treat its output as an upper bound on short
    financing cost.
  - **The borrow rate is an input, not a forecast.** Hard-to-borrow rates are
    re-struck daily and can move by hundreds of basis points overnight.

See ``references/standards.md`` for the sourced day-count, collateral and
settlement conventions behind these choices.
"""
import datetime
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_WEEKEND_DAYS = (5, 6)  # Saturday, Sunday


class RateScheduleError(ValueError):
    """
    Raised when a tier schedule cannot price a balance unambiguously.

    Kept distinct from :class:`FinancingDataError` so a caller can tell
    "the rate table I configured is malformed" (a deployment/config bug, fix it
    once) from "today's balance feed is unusable" (an operational incident).
    """


class FinancingDataError(ValueError):
    """
    Raised when a balance, rate or date input is unusable.

    NaN is the specific hazard. It propagates silently through multiplication,
    so a single NaN balance turns adjusted net P&L into NaN without any error —
    and a NaN cost compares False against every threshold downstream. Financing
    inputs therefore fail loudly rather than defaulting to zero, because a
    silently-zero financing cost is indistinguishable from an unlevered
    strategy and flatters every performance metric derived from it.
    """


def _require_finite(name: str, value: float, *, allow_negative: bool = True) -> float:
    """Validate a numeric financing input, rejecting NaN, infinity and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FinancingDataError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise FinancingDataError(
            f"{name} must be finite, got {value!r}. Refusing to accrue financing on an "
            f"unusable value; treat this as a data outage, not a zero-cost day."
        )
    if not allow_negative and value < 0:
        raise FinancingDataError(f"{name} must be >= 0, got {value!r}")
    return float(value)


@dataclass
class MarginRateTier:
    """
    One bracket of a tiered margin interest schedule.

    Bounds are half-open: ``[min_balance_usd, max_balance_usd)``. The top tier
    must be open-ended (``float("inf")``) so that no part of a balance can go
    unpriced.
    """
    min_balance_usd: float
    max_balance_usd: float
    apr: float  # e.g., 0.065 for 6.5% APR


@dataclass
class DailyAccrualRecord:
    """
    One ledger row: the financing charged for an accrual block starting on
    ``date`` and covering ``days_accrued`` calendar days.

    A block spans from one end-of-day observation to the next, so a Friday row
    normally covers Friday, Saturday and Sunday. ``is_weekend`` and
    ``is_holiday`` describe the *block*, not the start date: they are True when
    the block covers at least one weekend day / configured holiday.
    """
    date: datetime.date
    debit_balance_usd: float
    short_market_value_usd: float
    is_weekend: bool
    is_holiday: bool
    days_accrued: int
    margin_interest_usd: float
    short_borrow_fee_usd: float
    total_daily_cost_usd: float
    effective_margin_apr: float = 0.0
    short_collateral_usd: float = 0.0


@dataclass
class MarginInterestSummary:
    """Financing cost over a holding period and its effect on P&L."""
    total_days_held: int
    total_margin_interest_usd: float
    total_borrow_fees_usd: float
    gross_pnl_usd: float
    adjusted_net_pnl_usd: float
    daily_records: List[DailyAccrualRecord]


@dataclass
class EodBalance:
    """
    An end-of-day snapshot of the balances subject to financing.

    Only end-of-day balances accrue overnight financing; intraday leverage that
    is closed before the close does not appear here at all.

    ``short_collateral_usd`` overrides the markup-derived collateral when you
    have computed the broker's exact figure (e.g. IBKR's 102% of the prior
    settlement price rounded up to the next whole dollar, times shares).
    """
    date: datetime.date
    debit_balance_usd: float = 0.0
    short_market_value_usd: float = 0.0
    short_borrow_fee_apr: float = 0.0
    short_collateral_usd: Optional[float] = None


# Illustrative IBKR Pro USD blended tiers from the 2023-24 rate environment.
# NOT a live schedule -- IBKR quotes these as a spread over Fed Funds, so the
# absolute rates move with policy. Replace them with your broker's current
# published table before trusting any number this module produces.
DEFAULT_BLENDED_TIERS: List[MarginRateTier] = [
    MarginRateTier(0.0, 100000.0, 0.0683),           # First 100k @ 6.83%
    MarginRateTier(100000.0, 1000000.0, 0.0633),     # Next 900k @ 6.33%
    MarginRateTier(1000000.0, 50000000.0, 0.0603),   # Next 49M @ 6.03%
    MarginRateTier(50000000.0, float("inf"), 0.0583) # Above 50M @ 5.83%
]


def tiers_from_benchmark(
    benchmark_apr: float,
    spreads: Sequence[Tuple[float, float]],
) -> List[MarginRateTier]:
    """
    Build a tier schedule from a benchmark rate plus published tier spreads.

    Brokers quote margin rates as ``benchmark + spread`` (IBKR uses Fed Funds
    effective for USD), which is why hard-coded absolute APRs go stale every
    time policy moves. Passing today's benchmark keeps the schedule current
    while the spreads — which change rarely — stay in config.

    Args:
        benchmark_apr: Today's benchmark, as a decimal (0.0433 for 4.33%).
        spreads: ``(upper_bound_usd, spread_apr)`` pairs in ascending bound
            order, e.g. ``[(100_000, 0.015), (float("inf"), 0.005)]``. The
            final bound must be ``float("inf")``.

    Returns:
        A validated, contiguous tier schedule starting at 0.

    Raises:
        RateScheduleError: if the bounds are not ascending, do not end at
            infinity, or produce a negative rate.
    """
    _require_finite("benchmark_apr", benchmark_apr)
    if not spreads:
        raise RateScheduleError("spreads must not be empty")

    tiers: List[MarginRateTier] = []
    lower = 0.0
    for index, (upper, spread) in enumerate(spreads):
        _require_finite(f"spreads[{index}] spread", spread)
        if not (isinstance(upper, (int, float)) and not isinstance(upper, bool)):
            raise RateScheduleError(f"spreads[{index}] bound must be a number, got {upper!r}")
        if math.isnan(upper):
            raise RateScheduleError(f"spreads[{index}] bound must not be NaN")
        if upper <= lower:
            raise RateScheduleError(
                f"spreads bounds must strictly ascend from 0; "
                f"spreads[{index}] bound {upper!r} does not exceed {lower!r}"
            )
        rate = benchmark_apr + spread
        if rate < 0:
            raise RateScheduleError(
                f"spreads[{index}] yields a negative APR ({rate!r}); "
                f"a negative margin loan rate is almost certainly a sign error"
            )
        tiers.append(MarginRateTier(lower, float(upper), rate))
        lower = float(upper)

    if tiers[-1].max_balance_usd != float("inf"):
        raise RateScheduleError(
            "the final spread bound must be float('inf') so no part of a balance "
            "is left unpriced"
        )
    return tiers


def _validate_tier_schedule(tiers: Sequence[MarginRateTier]) -> List[MarginRateTier]:
    """
    Return a sorted copy of ``tiers`` after checking it can price any balance.

    The blended calculation walks the tiers filling each bracket's width, which
    is only meaningful if the brackets start at zero, are contiguous, and the
    last one is open-ended. A schedule with a gap silently prices the balance in
    the gap at the wrong tier's rate; a schedule with a finite top tier silently
    prices everything above it at **zero**, understating the cost of exactly the
    largest loans. Both fail here instead.
    """
    if not tiers:
        raise RateScheduleError("rate_tiers must not be empty")

    ordered = sorted(tiers, key=lambda t: t.min_balance_usd)
    for index, tier in enumerate(ordered):
        _require_finite(f"tier[{index}].min_balance_usd", tier.min_balance_usd, allow_negative=False)
        _require_finite(f"tier[{index}].apr", tier.apr, allow_negative=False)
        if isinstance(tier.max_balance_usd, bool) or not isinstance(tier.max_balance_usd, (int, float)):
            raise RateScheduleError(f"tier[{index}].max_balance_usd must be a number")
        if math.isnan(tier.max_balance_usd):
            raise RateScheduleError(f"tier[{index}].max_balance_usd must not be NaN")
        if tier.max_balance_usd <= tier.min_balance_usd:
            raise RateScheduleError(
                f"tier[{index}] has max_balance_usd ({tier.max_balance_usd!r}) <= "
                f"min_balance_usd ({tier.min_balance_usd!r}); brackets must have positive width"
            )

    if ordered[0].min_balance_usd != 0.0:
        raise RateScheduleError(
            f"the lowest tier must start at 0, not {ordered[0].min_balance_usd!r}; "
            f"otherwise the first slice of every balance is priced by the wrong bracket"
        )
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.max_balance_usd != upper.min_balance_usd:
            raise RateScheduleError(
                f"tier schedule is not contiguous: a bracket ends at "
                f"{lower.max_balance_usd!r} but the next begins at {upper.min_balance_usd!r}"
            )
    if ordered[-1].max_balance_usd != float("inf"):
        raise RateScheduleError(
            f"the top tier must be open-ended (float('inf')), not "
            f"{ordered[-1].max_balance_usd!r}; a finite top tier leaves any excess "
            f"balance unpriced and silently understates interest on the largest loans"
        )
    return ordered


class MarginInterestTracker:
    """
    Accrues margin loan interest and short borrow fees on a calendar-day basis.

    Supports blended (progressive) tier schedules, in which each slice of the
    balance is priced by its own bracket — the structure IBKR and prime brokers
    use — as well as flat schedules where crossing a threshold reprices the
    whole balance.
    """

    def __init__(
        self,
        rate_tiers: Optional[List[MarginRateTier]] = None,
        day_count_convention_margin: int = 360,
        day_count_convention_borrow: int = 360,
        is_blended_rate: bool = True,
        short_collateral_markup: float = 1.0,
    ):
        """
        Args:
            rate_tiers: Margin loan schedule. Defaults to the dated illustrative
                ``DEFAULT_BLENDED_TIERS``; supply your broker's current table.
            day_count_convention_margin: Days per year for margin interest. 360
                for USD and most other currencies at IBKR; 365 for exceptional
                currencies such as GBP.
            day_count_convention_borrow: Days per year for borrow fees. IBKR
                computes borrow fees as ``value * rate / 360``.
            is_blended_rate: True for progressive tiers, False when crossing a
                threshold reprices the entire balance.
            short_collateral_markup: Multiplier turning short market value into
                the collateral the borrow fee is charged on. 1.0 charges on raw
                market value and understates the fee; 1.02 matches the US
                securities-lending convention IBKR applies. It cannot reproduce
                IBKR's round-up-to-the-next-whole-dollar-per-share step, which
                needs per-share prices — pass ``EodBalance.short_collateral_usd``
                for an exact match.
        """
        # Copy before sorting: sorting in place would reorder the caller's list and,
        # when the default is used, permanently reorder the module-level constant
        # shared by every other tracker in the process.
        self.rate_tiers: List[MarginRateTier] = _validate_tier_schedule(
            list(rate_tiers) if rate_tiers is not None else list(DEFAULT_BLENDED_TIERS)
        )

        for name, convention in (
            ("day_count_convention_margin", day_count_convention_margin),
            ("day_count_convention_borrow", day_count_convention_borrow),
        ):
            if isinstance(convention, bool) or not isinstance(convention, int):
                raise FinancingDataError(f"{name} must be an int, got {convention!r}")
            if convention <= 0:
                raise FinancingDataError(
                    f"{name} must be positive, got {convention!r} "
                    f"(360 for USD financing, 365 for currencies such as GBP)"
                )

        self.day_count_convention_margin = day_count_convention_margin
        self.day_count_convention_borrow = day_count_convention_borrow
        self.is_blended_rate = is_blended_rate
        self.short_collateral_markup = _require_finite(
            "short_collateral_markup", short_collateral_markup, allow_negative=False
        )
        self.holidays: set[datetime.date] = set()

    def add_holidays(self, holidays: Sequence[datetime.date]) -> None:
        """
        Register non-settlement dates.

        Holidays do not change the *total* financing cost — the balance exists
        on a holiday exactly as it does on a weekend, and interest accrues on
        it. What they change is where the charge lands in the ledger: a position
        held over a Friday with a holiday Monday produces one four-day accrual
        block rather than a three-day and a one-day block.
        """
        for holiday in holidays:
            if not isinstance(holiday, datetime.date) or isinstance(holiday, datetime.datetime):
                raise FinancingDataError(
                    f"holidays must be datetime.date (not datetime.datetime), got {holiday!r}"
                )
        self.holidays.update(holidays)

    def get_effective_apr(self, debit_balance_usd: float) -> float:
        """
        Effective annual rate for a debit balance under the configured schedule.

        Under a blended schedule this is the balance-weighted average of the
        bracket rates, so it *falls* as the balance grows. A credit (zero or
        negative) balance owes no margin interest and returns 0.0.
        """
        _require_finite("debit_balance_usd", debit_balance_usd)
        if debit_balance_usd <= 0:
            return 0.0

        if not self.is_blended_rate:
            for tier in self.rate_tiers:
                if tier.min_balance_usd <= debit_balance_usd < tier.max_balance_usd:
                    return tier.apr
            return self.rate_tiers[-1].apr

        total_annual_interest = 0.0
        remaining_balance = debit_balance_usd
        for tier in self.rate_tiers:
            if remaining_balance <= 0:
                break
            tier_width = tier.max_balance_usd - tier.min_balance_usd
            applicable_balance_in_tier = min(remaining_balance, tier_width)
            total_annual_interest += applicable_balance_in_tier * tier.apr
            remaining_balance -= applicable_balance_in_tier

        # _validate_tier_schedule guarantees an open-ended top tier, so the loop
        # always consumes the whole balance and nothing is left unpriced.
        return total_annual_interest / debit_balance_usd

    def _block_flags(self, start: datetime.date, days: int) -> Tuple[bool, bool]:
        """Report whether an accrual block covers any weekend day / holiday."""
        covers_weekend = False
        covers_holiday = False
        for offset in range(days):
            day = start + datetime.timedelta(days=offset)
            if day.weekday() in _WEEKEND_DAYS:
                covers_weekend = True
            if day in self.holidays:
                covers_holiday = True
        return covers_weekend, covers_holiday

    def _next_accrual_anchor(self, date: datetime.date) -> datetime.date:
        """Next settlement day after ``date``, skipping weekends and holidays."""
        candidate = date + datetime.timedelta(days=1)
        while candidate.weekday() in _WEEKEND_DAYS or candidate in self.holidays:
            candidate += datetime.timedelta(days=1)
        return candidate

    def accrue_daily_balances(
        self,
        balances: Sequence[EodBalance],
        through_date: datetime.date,
        gross_pnl_usd: float = 0.0,
    ) -> MarginInterestSummary:
        """
        Accrue financing over a series of end-of-day balance observations.

        This is the primary entry point, because it derives the day count from
        the dates themselves — it cannot be fed a count of trading days by
        mistake. Each observation accrues from its own date until the next
        observation's date, and the final one accrues until ``through_date``.
        Weekends and holidays are covered automatically: if you observe Friday
        and then Monday, Friday's row carries three days.

        Balances may change day to day, and under a blended schedule the
        effective APR is recomputed for each observation's balance — the reason
        a single average balance is not a substitute.

        Args:
            balances: End-of-day observations, strictly ascending by date.
            through_date: The date the accrual stops, **exclusive**. Use the
                date the debit balance was cleared. Must be after the last
                observation.
            gross_pnl_usd: Gross trading P&L for the window, before financing.

        Returns:
            A summary whose ``adjusted_net_pnl_usd`` is ``gross_pnl_usd`` less
            total margin interest and borrow fees.

        Raises:
            FinancingDataError: on unusable numbers, unordered or duplicated
                dates, or a ``through_date`` at or before the last observation.
        """
        _require_finite("gross_pnl_usd", gross_pnl_usd)
        if not balances:
            return MarginInterestSummary(0, 0.0, 0.0, gross_pnl_usd, gross_pnl_usd, [])

        for index, balance in enumerate(balances):
            if not isinstance(balance.date, datetime.date) or isinstance(balance.date, datetime.datetime):
                raise FinancingDataError(
                    f"balances[{index}].date must be a datetime.date, got {balance.date!r}"
                )
            if index and balance.date <= balances[index - 1].date:
                raise FinancingDataError(
                    f"balances must be strictly ascending by date; balances[{index}] "
                    f"({balance.date}) does not follow balances[{index - 1}] "
                    f"({balances[index - 1].date}). Duplicate dates would double-charge."
                )
        if not isinstance(through_date, datetime.date) or isinstance(through_date, datetime.datetime):
            raise FinancingDataError(f"through_date must be a datetime.date, got {through_date!r}")
        if through_date <= balances[-1].date:
            raise FinancingDataError(
                f"through_date ({through_date}) must be after the last balance date "
                f"({balances[-1].date}); financing accrues for at least one day."
            )

        records: List[DailyAccrualRecord] = []
        total_margin_interest = 0.0
        total_borrow_fees = 0.0
        total_days = 0

        for index, balance in enumerate(balances):
            next_date = balances[index + 1].date if index + 1 < len(balances) else through_date
            days = (next_date - balance.date).days

            debit = _require_finite(f"balances[{index}].debit_balance_usd", balance.debit_balance_usd)
            short_mv = _require_finite(
                f"balances[{index}].short_market_value_usd", balance.short_market_value_usd
            )
            borrow_apr = _require_finite(
                f"balances[{index}].short_borrow_fee_apr", balance.short_borrow_fee_apr,
                allow_negative=False,
            )
            if balance.short_collateral_usd is None:
                collateral = max(short_mv, 0.0) * self.short_collateral_markup
            else:
                collateral = _require_finite(
                    f"balances[{index}].short_collateral_usd", balance.short_collateral_usd,
                    allow_negative=False,
                )

            effective_apr = self.get_effective_apr(debit)
            # A credit balance earns interest rather than owing it; crediting it here
            # would silently offset borrow fees with income this module does not model.
            chargeable_debit = max(debit, 0.0)
            margin_interest = (
                chargeable_debit * (effective_apr / self.day_count_convention_margin) * days
            )
            borrow_fee = collateral * (borrow_apr / self.day_count_convention_borrow) * days

            covers_weekend, covers_holiday = self._block_flags(balance.date, days)
            total_margin_interest += margin_interest
            total_borrow_fees += borrow_fee
            total_days += days

            records.append(DailyAccrualRecord(
                date=balance.date,
                debit_balance_usd=debit,
                short_market_value_usd=short_mv,
                is_weekend=covers_weekend,
                is_holiday=covers_holiday,
                days_accrued=days,
                margin_interest_usd=margin_interest,
                short_borrow_fee_usd=borrow_fee,
                total_daily_cost_usd=margin_interest + borrow_fee,
                effective_margin_apr=effective_apr,
                short_collateral_usd=collateral,
            ))

        total_cost = total_margin_interest + total_borrow_fees
        adjusted_pnl = gross_pnl_usd - total_cost

        logger.info(
            "Financing over %d calendar days: margin interest %.2f, borrow fees %.2f, "
            "gross P&L %.2f, adjusted net P&L %.2f",
            total_days, total_margin_interest, total_borrow_fees, gross_pnl_usd, adjusted_pnl,
        )

        return MarginInterestSummary(
            total_days_held=total_days,
            total_margin_interest_usd=total_margin_interest,
            total_borrow_fees_usd=total_borrow_fees,
            gross_pnl_usd=gross_pnl_usd,
            adjusted_net_pnl_usd=adjusted_pnl,
            daily_records=records,
        )

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
        Accrue financing on a constant balance held for ``holding_days``.

        ``holding_days`` is a count of **calendar** days, not trading days. A
        position opened at Monday's close and closed at the following Monday's
        close is 7, not 5. Passing a trading-day count under-charges by roughly
        two sevenths, which is the single most common error in retail-grade
        financing models. When you have both dates, prefer
        :meth:`accrue_daily_balances`, which cannot be miscounted.

        The resulting ledger batches weekends and configured holidays into the
        preceding settlement day, so a hold beginning on a Friday produces one
        three-day row rather than three one-day rows. That is presentation: the
        total is ``balance * apr / day_count * holding_days`` either way.

        Args:
            start_date: Date of the first end-of-day balance subject to financing.
            holding_days: Calendar days the balance is carried.
            daily_debit_balance_usd: Constant end-of-day debit balance.
            daily_short_mv_usd: Constant end-of-day gross short market value.
            short_borrow_fee_apr: Borrow rate as a decimal (0.10 for 10%).
            gross_pnl_usd: Gross trading P&L before financing.

        Raises:
            FinancingDataError: on a negative or non-integer ``holding_days``,
                a non-date ``start_date``, or unusable balances.
        """
        if isinstance(holding_days, bool) or not isinstance(holding_days, int):
            raise FinancingDataError(
                f"holding_days must be an int number of calendar days, got {holding_days!r}"
            )
        if holding_days < 0:
            raise FinancingDataError(
                f"holding_days must be >= 0, got {holding_days!r}; a negative holding "
                f"period usually means the start and end dates are transposed"
            )
        if not isinstance(start_date, datetime.date) or isinstance(start_date, datetime.datetime):
            raise FinancingDataError(f"start_date must be a datetime.date, got {start_date!r}")

        debit = _require_finite("daily_debit_balance_usd", daily_debit_balance_usd)
        short_mv = _require_finite("daily_short_mv_usd", daily_short_mv_usd)
        _require_finite("short_borrow_fee_apr", short_borrow_fee_apr, allow_negative=False)
        _require_finite("gross_pnl_usd", gross_pnl_usd)

        if (debit <= 0 and short_mv <= 0) or holding_days == 0:
            return MarginInterestSummary(
                total_days_held=holding_days,
                total_margin_interest_usd=0.0,
                total_borrow_fees_usd=0.0,
                gross_pnl_usd=gross_pnl_usd,
                adjusted_net_pnl_usd=gross_pnl_usd,
                daily_records=[],
            )

        end_date = start_date + datetime.timedelta(days=holding_days)
        balances: List[EodBalance] = []
        anchor = start_date
        while anchor < end_date:
            balances.append(EodBalance(
                date=anchor,
                debit_balance_usd=debit,
                short_market_value_usd=short_mv,
                short_borrow_fee_apr=short_borrow_fee_apr,
            ))
            anchor = self._next_accrual_anchor(anchor)

        return self.accrue_daily_balances(
            balances, through_date=end_date, gross_pnl_usd=gross_pnl_usd
        )

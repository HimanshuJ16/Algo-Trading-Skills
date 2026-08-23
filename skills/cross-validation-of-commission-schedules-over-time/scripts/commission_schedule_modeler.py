"""
cross-validation-of-commission-schedules-over-time: Time-varying broker commission
schedule modeler and historical fee impact auditor.

Purpose
-------
Backtests spanning multiple years must charge the commission schedule that was
actually in force on each trade date. Applying today's US retail zero-commission
structure retroactively inflates backtested P&L for any strategy that trades
frequently.

Safety model
------------
The most dangerous failure mode for this module is *silently* charging $0.00 to a
trade whose date cannot be resolved to a tier -- that reproduces exactly the
retroactive-zero-commission bias the skill exists to prevent. Therefore this
module NEVER falls back to a default tier: an unparseable timestamp or a date
outside the schedule's coverage raises.

Regulatory fees (US)
--------------------
"Zero commission" is not zero cost. On US equity *sales* two pass-through
regulatory charges still apply and both vary over time:

  * SEC Section 31 fee -- assessed on covered *sales*, quoted in dollars per
    million of sale proceeds; the rate is reset annually and often mid-year.
  * FINRA Trading Activity Fee (TAF) -- assessed per share on each *sale* of a
    covered equity security, subject to a per-trade maximum.

These are modelled only when the caller supplies a ``regulatory_schedule``. When
none is supplied the results are explicitly flagged
(``regulatory_fees_modeled=False``) rather than reported as zero, so a report can
never imply a cost was measured and found to be zero when it was simply not
modelled. See ``references/standards.md`` for sources.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_FAR_FUTURE = "9999-12-31"

BUY = "BUY"
SELL = "SELL"
_VALID_SIDES = frozenset({BUY, SELL})


class CommissionScheduleError(ValueError):
    """Raised when a schedule is malformed or does not cover a trade date."""


def _round_cents(value: float) -> float:
    """Round a monetary amount to cents using ROUND_HALF_UP.

    ``round()`` uses banker's rounding on top of binary float representation,
    which drifts on fee totals accumulated over thousands of backtested trades.
    """
    return float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_trade_date(value: Any, ctx: str) -> date:
    """Parse a trade timestamp to a calendar date.

    Accepts ``datetime.date``/``datetime.datetime`` and ISO-8601 strings, with or
    without a time component (``2019-10-07`` or ``2019-10-07T14:30:00``). Raises
    on anything else -- it must never degrade into a default tier.

    The date is taken verbatim. If the schedule's effective dates are stated in
    exchange-local time, convert the timestamp to that timezone before calling.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise CommissionScheduleError(
            f"{ctx}: timestamp must be an ISO-8601 date string or a date object, got {value!r}"
        )
    head = value.strip().replace("T", " ").split(" ")[0]
    try:
        return date.fromisoformat(head)
    except ValueError as exc:
        raise CommissionScheduleError(
            f"{ctx}: timestamp {value!r} is not an ISO-8601 (YYYY-MM-DD) date; "
            "refusing to guess a fee tier"
        ) from exc


def _normalize_side(side: Any, ctx: str) -> str:
    if not isinstance(side, str) or side.strip().upper() not in _VALID_SIDES:
        raise CommissionScheduleError(f"{ctx}: side must be 'BUY' or 'SELL', got {side!r}")
    return side.strip().upper()


@dataclass
class CommissionTier:
    """A broker commission structure in force over a closed date interval.

    Both ``effective_start`` and ``effective_end`` are inclusive ISO dates.

    Fee composition, in order of application::

        raw   = fixed_ticket_fee + shares * per_share_fee + trade_value * pct_value_fee
        floor = max(raw, min_trade_fee)
        final = min(floor, trade_value * max_pct_of_value)     # only when a cap is set

    The cap is applied *after* the minimum, i.e. the cap dominates. This matches
    the Interactive Brokers Fixed US-equity structure ($0.005/share, $1.00 minimum
    per order, maximum 1% of trade value). Brokers whose minimum is not overridden
    by a cap should be modelled with ``max_pct_of_value=None``.

    A flat per-ticket broker is modelled with ``fixed_ticket_fee`` alone; leave
    ``min_trade_fee`` at 0.0 rather than duplicating the ticket fee into it.
    """

    effective_start: str
    effective_end: str
    fixed_ticket_fee: float = 0.0
    per_share_fee: float = 0.0
    min_trade_fee: float = 0.0
    pct_value_fee: float = 0.0
    max_pct_of_value: Optional[float] = None
    label: str = ""
    source: str = ""

    def start_date(self) -> date:
        return _parse_trade_date(self.effective_start, f"tier {self.label or self.effective_start} start")

    def end_date(self) -> date:
        return _parse_trade_date(self.effective_end, f"tier {self.label or self.effective_start} end")

    def covers(self, trade_date: date) -> bool:
        return self.start_date() <= trade_date <= self.end_date()


@dataclass
class RegulatoryFeeTier:
    """US pass-through regulatory fees in force over a closed date interval.

    Both charges apply to *sales* only:

      * ``sec_fee_per_million`` -- SEC Section 31 fee, dollars per $1,000,000 of
        covered-sale proceeds.
      * ``taf_per_share`` / ``taf_max_per_trade`` -- FINRA Trading Activity Fee.

    Rates change on regulator-published effective dates. Populate this schedule
    from the SEC fee-rate advisories and FINRA Schedule A entries covering the
    backtest range; this module ships no default regulatory history.
    """

    effective_start: str
    effective_end: str
    sec_fee_per_million: float = 0.0
    taf_per_share: float = 0.0
    taf_max_per_trade: Optional[float] = None
    source: str = ""

    def start_date(self) -> date:
        return _parse_trade_date(self.effective_start, "regulatory tier start")

    def end_date(self) -> date:
        return _parse_trade_date(self.effective_end, "regulatory tier end")

    def covers(self, trade_date: date) -> bool:
        return self.start_date() <= trade_date <= self.end_date()


@dataclass
class TradeCommissionResult:
    trade_id: str
    timestamp: str
    symbol: str
    shares: float
    price: float
    trade_value: float
    effective_tier_start: str
    calculated_commission: float
    side: str = BUY
    tier_label: str = ""
    regulatory_fees: float = 0.0
    regulatory_fees_modeled: bool = False
    total_cost: float = 0.0


@dataclass
class FeeScheduleImpactReport:
    total_trades: int
    total_volume: float
    total_commission_historical: float
    total_commission_modern_flat: float
    historical_fee_drag_pct: float
    modern_fee_drag_pct: float
    pnl_impact_delta_usd: float
    message: str
    total_regulatory_fees: float = 0.0
    regulatory_fees_modeled: bool = False


# --- Reference schedules -----------------------------------------------------
# Charles Schwab standard online US equity/ETF commission. Rates and effective
# dates are from Schwab press releases and contemporaneous reporting; see
# references/standards.md for citations. NOTE: the $8.95 tier's *start* date is a
# placeholder floor -- only the rate and the date it ended are sourced. Verify it
# before backtesting periods before 2017-02-03.
DEFAULT_SCHWAB_RETAIL_SCHEDULE: List[CommissionTier] = [
    CommissionTier(
        "2010-01-01", "2017-02-02", fixed_ticket_fee=8.95, label="schwab-8.95",
        source="rate sourced; tier START date is an UNVERIFIED placeholder floor",
    ),
    CommissionTier(
        "2017-02-03", "2017-03-02", fixed_ticket_fee=6.95, label="schwab-6.95",
        source="effective 2017-02-03",
    ),
    CommissionTier(
        "2017-03-03", "2019-10-06", fixed_ticket_fee=4.95, label="schwab-4.95",
        source="effective 2017-03-03",
    ),
    CommissionTier(
        "2019-10-07", _FAR_FUTURE, fixed_ticket_fee=0.0, label="schwab-zero-commission",
        source="effective 2019-10-07",
    ),
]

# Interactive Brokers Fixed pricing for US stocks/ETFs: $0.005/share, $1.00
# minimum per order, maximum 1% of trade value. Exported as a worked example of
# per-share pricing carrying both a floor and a cap. Confirm the effective dates
# against IBKR's published schedule for your backtest range before using it.
IBKR_FIXED_US_EQUITY_TIER = CommissionTier(
    "2010-01-01", _FAR_FUTURE,
    per_share_fee=0.005, min_trade_fee=1.00, max_pct_of_value=0.01,
    label="ibkr-fixed-us-equity",
    source="IBKR Fixed US stock pricing; verify effective dates before use",
)


class HistoricalCommissionModeler:
    """Applies time-varying broker commission schedules to historical trades.

    Raises ``CommissionScheduleError`` rather than defaulting when a trade date
    falls outside the schedule -- see the module docstring.
    """

    def __init__(
        self,
        schedule: Optional[Sequence[CommissionTier]] = None,
        regulatory_schedule: Optional[Sequence[RegulatoryFeeTier]] = None,
    ) -> None:
        if schedule is None:
            logger.warning(
                "No commission schedule supplied; using the built-in Schwab retail reference "
                "schedule. Replace it with your broker's published schedule before relying on "
                "backtest cost figures."
            )
            schedule = DEFAULT_SCHWAB_RETAIL_SCHEDULE
        self.schedule: List[CommissionTier] = self._validate_schedule(list(schedule), "commission")
        self.regulatory_schedule: Optional[List[RegulatoryFeeTier]] = (
            self._validate_schedule(list(regulatory_schedule), "regulatory")
            if regulatory_schedule is not None
            else None
        )

    @staticmethod
    def _validate_schedule(tiers: List[Any], kind: str) -> List[Any]:
        """Reject malformed schedules at construction rather than mispricing later.

        Returns a sorted, defensively copied tier list. The copy matters: tiers
        are mutable dataclasses and the module-level reference schedules are
        shared, so retaining the caller's objects would let one modeler's tiers be
        mutated through another instance (or through the module global).

        Overlaps are an error (ambiguous rate); gaps are permitted but warned
        about, and any trade landing in a gap raises at lookup time.
        """
        if not tiers:
            raise CommissionScheduleError(f"{kind} schedule must contain at least one tier")
        tiers = deepcopy(tiers)

        for tier in tiers:
            if tier.start_date() > tier.end_date():
                raise CommissionScheduleError(
                    f"{kind} tier {tier.effective_start}..{tier.effective_end} ends before it starts"
                )
            for name in ("fixed_ticket_fee", "per_share_fee", "min_trade_fee", "pct_value_fee",
                         "sec_fee_per_million", "taf_per_share"):
                value = getattr(tier, name, 0.0)
                if value is not None and value < 0:
                    raise CommissionScheduleError(
                        f"{kind} tier {tier.effective_start}: field {name} must be >= 0, got {value}"
                    )

        ordered = sorted(tiers, key=lambda t: t.start_date())
        for prev, curr in zip(ordered, ordered[1:]):
            if curr.start_date() <= prev.end_date():
                raise CommissionScheduleError(
                    f"overlapping {kind} tiers: {prev.effective_start}..{prev.effective_end} and "
                    f"{curr.effective_start}..{curr.effective_end}; overlapping tiers make the "
                    "applicable rate ambiguous"
                )
            if (curr.start_date() - prev.end_date()).days > 1:
                logger.warning(
                    "Gap in %s schedule between %s and %s; trades in that window will raise.",
                    kind, prev.effective_end, curr.effective_start,
                )
        return ordered

    def _resolve_tier(self, trade_date: date, trade_id: str) -> CommissionTier:
        for tier in self.schedule:
            if tier.covers(trade_date):
                return tier
        raise CommissionScheduleError(
            f"trade {trade_id}: no commission tier covers {trade_date.isoformat()} "
            f"(schedule spans {self.schedule[0].effective_start}..{self.schedule[-1].effective_end}). "
            "Extend the schedule; refusing to apply an out-of-range rate."
        )

    def _regulatory_fees(
        self, trade_date: date, shares: float, trade_value: float, side: str, trade_id: str
    ) -> float:
        """SEC Section 31 fee + FINRA TAF. Both are assessed on sales only."""
        if self.regulatory_schedule is None or side != SELL:
            return 0.0
        for tier in self.regulatory_schedule:
            if tier.covers(trade_date):
                sec_fee = trade_value * tier.sec_fee_per_million / 1_000_000.0
                taf = shares * tier.taf_per_share
                if tier.taf_max_per_trade is not None:
                    taf = min(taf, tier.taf_max_per_trade)
                return _round_cents(sec_fee + taf)
        raise CommissionScheduleError(
            f"trade {trade_id}: no regulatory fee tier covers {trade_date.isoformat()}; extend the "
            "regulatory schedule, or pass regulatory_schedule=None to exclude regulatory fees "
            "explicitly."
        )

    def calculate_trade_commission(
        self,
        trade_id: str,
        timestamp: Any,
        symbol: str,
        shares: float,
        price: float,
        side: str = BUY,
    ) -> TradeCommissionResult:
        """Compute the commission that was actually in force on ``timestamp``.

        ``shares`` must be a positive quantity; direction is carried by ``side``.
        Signed quantities are rejected because a negative quantity would silently
        reduce (or invert) a per-share fee.
        """
        ctx = f"trade {trade_id}"
        trade_date = _parse_trade_date(timestamp, ctx)
        side = _normalize_side(side, ctx)

        if isinstance(shares, bool) or not isinstance(shares, (int, float)) \
                or shares != shares or shares <= 0:
            raise CommissionScheduleError(
                f"{ctx}: shares must be a positive number (use side='SELL' for sales), got {shares!r}"
            )
        if isinstance(price, bool) or not isinstance(price, (int, float)) \
                or price != price or price < 0:
            raise CommissionScheduleError(f"{ctx}: price must be a non-negative number, got {price!r}")

        trade_val = shares * price
        tier = self._resolve_tier(trade_date, trade_id)

        raw_comm = (
            tier.fixed_ticket_fee
            + shares * tier.per_share_fee
            + trade_val * tier.pct_value_fee
        )
        final_comm = max(raw_comm, tier.min_trade_fee)
        if tier.max_pct_of_value is not None:
            final_comm = min(final_comm, trade_val * tier.max_pct_of_value)

        commission = _round_cents(final_comm)
        reg_fees = self._regulatory_fees(trade_date, shares, trade_val, side, trade_id)

        return TradeCommissionResult(
            trade_id=trade_id,
            timestamp=trade_date.isoformat(),
            symbol=symbol,
            shares=shares,
            price=price,
            trade_value=_round_cents(trade_val),
            effective_tier_start=tier.effective_start,
            calculated_commission=commission,
            side=side,
            tier_label=tier.label,
            regulatory_fees=reg_fees,
            regulatory_fees_modeled=self.regulatory_schedule is not None,
            total_cost=_round_cents(commission + reg_fees),
        )

    def audit_impact(
        self,
        trades: Sequence[Dict[str, Any]],
        starting_capital: float = 100000.0,
        modern_baseline: Optional[CommissionTier] = None,
    ) -> FeeScheduleImpactReport:
        """Quantify the P&L difference between the historical schedule and a flat
        modern baseline applied retroactively to every trade.

        ``modern_baseline`` defaults to a zero-commission tier -- the naive
        assumption this skill exists to expose. Pass a tier to compare against a
        non-zero flat structure instead. Only the baseline's *fee fields* are
        used; its date range is ignored so that it applies to every trade.

        Regulatory fees are reported separately and are never included in the
        historical-vs-modern delta, because they apply under both schedules.
        """
        if isinstance(starting_capital, bool) or not isinstance(starting_capital, (int, float)) \
                or starting_capital <= 0:
            raise CommissionScheduleError(
                f"starting_capital must be a positive number, got {starting_capital!r}"
            )
        if modern_baseline is None:
            modern_baseline = CommissionTier("0001-01-01", _FAR_FUTURE, label="modern-zero-commission")

        baseline_modeler = HistoricalCommissionModeler([
            CommissionTier(
                "0001-01-01", _FAR_FUTURE,
                fixed_ticket_fee=modern_baseline.fixed_ticket_fee,
                per_share_fee=modern_baseline.per_share_fee,
                min_trade_fee=modern_baseline.min_trade_fee,
                pct_value_fee=modern_baseline.pct_value_fee,
                max_pct_of_value=modern_baseline.max_pct_of_value,
                label=modern_baseline.label or "modern-baseline",
            )
        ])

        tot_hist_fee = 0.0
        tot_modern_fee = 0.0
        tot_reg_fee = 0.0
        tot_val = 0.0

        for idx, t in enumerate(trades):
            missing = [k for k in ("id", "date", "symbol", "shares", "price") if k not in t]
            if missing:
                raise CommissionScheduleError(
                    f"trade at index {idx}: missing required key(s) {missing}"
                )
            side = t.get("side", BUY)
            res_hist = self.calculate_trade_commission(
                t["id"], t["date"], t["symbol"], t["shares"], t["price"], side=side
            )
            res_modern = baseline_modeler.calculate_trade_commission(
                t["id"], t["date"], t["symbol"], t["shares"], t["price"], side=side
            )
            tot_hist_fee += res_hist.calculated_commission
            tot_modern_fee += res_modern.calculated_commission
            tot_reg_fee += res_hist.regulatory_fees
            tot_val += res_hist.trade_value

        hist_drag = (tot_hist_fee / starting_capital) * 100.0
        modern_drag = (tot_modern_fee / starting_capital) * 100.0
        delta = tot_hist_fee - tot_modern_fee
        reg_modeled = self.regulatory_schedule is not None

        msg = (
            f"Commission Audit ({len(trades)} trades): Historical Fee Drag={hist_drag:.2f}% "
            f"(${tot_hist_fee:.2f}) vs Modern Flat Drag={modern_drag:.2f}% "
            f"(${tot_modern_fee:.2f}). P&L Delta=${delta:.2f}. Regulatory fees "
            + (f"modelled: ${tot_reg_fee:.2f}." if reg_modeled
               else "NOT modelled (excluded from this report, not measured as zero).")
        )
        logger.info(msg)

        return FeeScheduleImpactReport(
            total_trades=len(trades),
            total_volume=_round_cents(tot_val),
            total_commission_historical=_round_cents(tot_hist_fee),
            total_commission_modern_flat=_round_cents(tot_modern_fee),
            historical_fee_drag_pct=round(hist_drag, 2),
            modern_fee_drag_pct=round(modern_drag, 2),
            pnl_impact_delta_usd=_round_cents(delta),
            message=msg,
            total_regulatory_fees=_round_cents(tot_reg_fee),
            regulatory_fees_modeled=reg_modeled,
        )

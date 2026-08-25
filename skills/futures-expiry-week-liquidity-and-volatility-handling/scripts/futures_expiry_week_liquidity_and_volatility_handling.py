"""Futures expiry-week liquidity and volatility safeguards.

Stateless, advisory decision engine that reads a point-in-time microstructure
snapshot of an *expiring* futures contract and returns the execution constraints
that should apply to it: whether market orders may be used, how far order size
must be cut, whether new entries are still permitted, and whether the position
must be rolled or escalated.

Why the expiring contract needs its own rules
---------------------------------------------
Liquidity leaves the expiring contract *before* it expires. CME designates the
Equity Index roll date as the Monday preceding the third Friday of the expiration
month, and from that date the second-nearest expiration is identified as the lead
month. So for essentially the whole of expiry week the contract the strategy is
still holding is no longer where the depth is -- the book thins and the quoted
spread widens while the position is still open. See ``references/standards.md``
for sources.

What this engine does *not* do
------------------------------
* It never places, cancels, or routes an order. It returns a report.
* It holds no state between calls and applies no multi-session smoothing.
* It does not model full-book market impact. ``top_of_book_depth_qty`` is one
  level; an order can be small relative to the haircut and still sweep several
  levels. Size against impact with ``liquidity-adjusted-position-sizing``.
* It does not schedule or execute the roll itself -- see
  ``futures-contract-roll-automation``.

Units and conventions (the engine cannot infer these -- supply them consistently)
--------------------------------------------------------------------------------
* ``days_to_expiration``: **business** days to Last Trading Day. ``0`` means today
  is the final trading session; negative means the contract has stopped trading.
* ``bid_ask_spread_ticks``: ``(ask - bid) / tick_size`` for *this* product. A tick
  is product-specific (E-mini S&P 500 futures trade in 0.25 index-point ticks;
  CME Single Stock futures in 0.01 points, $1.00 per tick), so a threshold
  expressed in ticks is a different currency amount on every contract.
* ``top_of_book_depth_qty`` and ``baseline_average_depth_qty``: the same depth
  convention on both (e.g. both the resting size on the near side, or both the
  bid+ask sum). Mixing conventions silently scales the ratio.

Fail-closed contract
--------------------
Every input is validated before any comparison is made. Missing or corrupt
microstructure data raises ``ValueError``; it never produces a permissive report.
A ``NaN`` spread compares ``False`` against every threshold, so an unvalidated
engine answers "market orders are fine" precisely when it knows nothing.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# --- Report statuses -----------------------------------------------------------
STATUS_NORMAL = "NORMAL_EXECUTION"
STATUS_RESTRICTED = "EXPIRY_WEEK_RESTRICTED"
STATUS_MANDATORY_ROLL = "MANDATORY_ROLL_REQUIRED"
STATUS_EXPIRED_ESCALATE = "EXPIRED_ESCALATE"

# --- Restriction reason codes --------------------------------------------------
REASON_PAST_LAST_TRADING_DAY = "PAST_LAST_TRADING_DAY"
REASON_EXPIRATION_CUTOFF = "EXPIRATION_CUTOFF"
REASON_WIDE_SPREAD = "WIDE_SPREAD"
REASON_THIN_DEPTH = "THIN_TOP_OF_BOOK_DEPTH"
REASON_QUAD_WITCHING = "QUAD_WITCHING_WEEK"


@dataclass
class FuturesOrderBookState:
    """Point-in-time microstructure snapshot of one expiring futures contract.

    Args:
        symbol: Exchange contract symbol, e.g. ``'ESH6'``.
        days_to_expiration: **Business** days from the current session to Last
            Trading Day. ``0`` means today is the last trading session; negative
            means the contract can no longer be traded.
        bid_ask_spread_ticks: Current quoted spread expressed in this product's
            ticks, ``(ask - bid) / tick_size``. ``0.0`` is a locked book;
            negative (crossed) is rejected rather than read as "tight".
        top_of_book_depth_qty: Resting quantity at the top of the book, in
            contracts, under the caller's chosen depth convention.
        baseline_average_depth_qty: The same measure averaged over a normal-market
            reference window for the same contract. Must be positive: a zero or
            missing baseline cannot be normalised against.
        is_quadruple_witching_week: ``True`` during a quarterly (Mar/Jun/Sep/Dec)
            third-Friday expiration week, when index futures, index options,
            equity options and -- since CME relisted them in July 2026 -- single
            stock futures expire together. See ``references/standards.md``; the
            settlement times differ by instrument on that day.
    """

    symbol: str
    days_to_expiration: int
    bid_ask_spread_ticks: float
    top_of_book_depth_qty: int
    baseline_average_depth_qty: int
    is_quadruple_witching_week: bool = False

    def validate(self) -> None:
        """Raise ``ValueError`` if this snapshot cannot be safely acted on.

        Every check exists because the un-validated comparison fails *open*: a
        non-finite or absent value compares ``False`` against the spread and depth
        thresholds and therefore lifts the restriction it was meant to trigger.
        """
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if not isinstance(self.days_to_expiration, int) or isinstance(
            self.days_to_expiration, bool
        ):
            raise ValueError(
                f"{self.symbol}: days_to_expiration must be an integer count of "
                f"business days, got {self.days_to_expiration!r}. A float (or NaN) "
                f"silently disables the expiration comparison."
            )
        if not isinstance(self.bid_ask_spread_ticks, (int, float)) or isinstance(
            self.bid_ask_spread_ticks, bool
        ):
            raise ValueError(
                f"{self.symbol}: bid_ask_spread_ticks must be a number, got "
                f"{self.bid_ask_spread_ticks!r}."
            )
        if not math.isfinite(self.bid_ask_spread_ticks):
            raise ValueError(
                f"{self.symbol}: bid_ask_spread_ticks must be finite, got "
                f"{self.bid_ask_spread_ticks!r}. A non-finite spread compares False "
                f"against the wide-spread threshold and would permit market orders "
                f"on a book the engine cannot read."
            )
        if self.bid_ask_spread_ticks < 0:
            raise ValueError(
                f"{self.symbol}: bid_ask_spread_ticks must be >= 0, got "
                f"{self.bid_ask_spread_ticks!r}. A negative (crossed) book is a data "
                f"or venue-state problem, not a tight market."
            )
        for label, value in (
            ("top_of_book_depth_qty", self.top_of_book_depth_qty),
            ("baseline_average_depth_qty", self.baseline_average_depth_qty),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"{self.symbol}: {label} must be a number, got {value!r}."
                )
            if not math.isfinite(value):
                raise ValueError(
                    f"{self.symbol}: {label} must be finite, got {value!r}. A "
                    f"non-finite depth produces a NaN depth ratio, which compares "
                    f"False against the haircut threshold and suppresses the haircut."
                )
        if self.top_of_book_depth_qty < 0:
            raise ValueError(
                f"{self.symbol}: top_of_book_depth_qty must be >= 0, got "
                f"{self.top_of_book_depth_qty!r}."
            )
        if self.baseline_average_depth_qty <= 0:
            raise ValueError(
                f"{self.symbol}: baseline_average_depth_qty must be > 0, got "
                f"{self.baseline_average_depth_qty!r}. Clamping an absent baseline to "
                f"1 would inflate the depth ratio and cancel the haircut exactly when "
                f"the reference data is missing."
            )
        if not isinstance(self.is_quadruple_witching_week, bool):
            raise ValueError(
                f"{self.symbol}: is_quadruple_witching_week must be a bool, got "
                f"{self.is_quadruple_witching_week!r}."
            )


@dataclass
class FuturesExpiryRiskReport:
    """Execution constraints derived from one order-book snapshot.

    Args:
        symbol: Contract the report applies to.
        days_to_expiration: Business days to Last Trading Day, echoed from input.
        is_market_orders_allowed: ``False`` when the quoted spread exceeds the
            configured threshold. Driven by the spread only -- a thin book is
            answered with the size haircut, not with a market-order block, because
            top-of-book depth does not bound the cost of crossing.
        size_haircut_factor: Multiplier applied to the caller's base order size
            (``1.0`` = full size, ``0.50`` = halved).
        adjusted_max_order_qty: ``floor(base_order_qty * size_haircut_factor)`` --
            a **cap** on whatever order the caller is otherwise permitted to send.
            When ``is_new_entry_allowed`` is ``False`` the only permitted orders are
            risk-reducing (close or roll); this cap does not re-authorise an entry.
            Forced to ``0`` under ``EXPIRED_ESCALATE`` regardless of the haircut,
            because no quantity is executable in a contract that has stopped
            trading.
        is_new_entry_allowed: ``False`` once the expiration cutoff is reached.
        is_mandatory_roll_required: ``True`` at the expiration cutoff, while the
            contract is still tradable.
        status: One of ``NORMAL_EXECUTION``, ``EXPIRY_WEEK_RESTRICTED``,
            ``MANDATORY_ROLL_REQUIRED``, ``EXPIRED_ESCALATE``.
        audit_notes: Human-readable summary; persist the whole report, not this.
        depth_ratio: ``top_of_book_depth_qty / baseline_average_depth_qty``.
        is_spread_wide: Whether the spread test fired.
        is_depth_thinned: Whether the depth test fired.
        restriction_reasons: Every condition that fired, not only the first.
        is_order_size_suppressed: ``True`` when the haircut floored a positive base
            quantity to ``0``. The engine is saying "do not send this order", not
            "send a zero-quantity order" -- most venues reject the latter.
        requires_manual_escalation: ``True`` only for ``EXPIRED_ESCALATE``. There is
            no automated recovery: the contract has stopped trading and the position
            is heading to settlement.
    """

    symbol: str
    days_to_expiration: int
    is_market_orders_allowed: bool
    size_haircut_factor: float
    adjusted_max_order_qty: int
    is_new_entry_allowed: bool
    is_mandatory_roll_required: bool
    status: str
    audit_notes: str
    # Fields below were appended in 2.0.0; they default so that existing callers
    # constructing a report positionally keep working unchanged.
    depth_ratio: float = 1.0
    is_spread_wide: bool = False
    is_depth_thinned: bool = False
    restriction_reasons: List[str] = field(default_factory=list)
    is_order_size_suppressed: bool = False
    requires_manual_escalation: bool = False


class FuturesExpiryRiskHandlerEngine:
    """Audits the microstructure state of an expiring futures contract.

    The engine is deliberately advisory: it returns constraints, it does not
    enforce them. Wire the report into the order path -- a report nobody reads
    blocks nothing.

    Args:
        max_spread_ticks_threshold: Market orders are blocked when the quoted
            spread is **strictly greater** than this many ticks. A spread exactly
            at the threshold is not wide.
        min_depth_ratio_threshold: The size haircut applies when the depth ratio is
            **strictly less** than this. A ratio exactly at the threshold is not
            thinned.
        mandatory_roll_dbe_cutoff: New entries are blocked and a roll is mandated
            when ``days_to_expiration <= cutoff`` (**inclusive**). The default of 2
            business days is this library's, not a rule: CME's own Equity Index roll
            date is the Monday before the third Friday, i.e. roughly four business
            days out, so a cutoff of 2 fires *after* the market has already moved on.
            Calibrate per product.
        size_haircut_factor: Multiplier applied when the depth or quad-witching
            condition fires. Must be in ``(0, 1]``.

    Raises:
        ValueError: If any threshold is outside its documented range. A negative or
            non-finite threshold would disable the control it configures.
    """

    def __init__(
        self,
        max_spread_ticks_threshold: float = 2.0,
        min_depth_ratio_threshold: float = 0.30,
        mandatory_roll_dbe_cutoff: int = 2,
        size_haircut_factor: float = 0.50,
    ) -> None:
        if (
            not isinstance(max_spread_ticks_threshold, (int, float))
            or isinstance(max_spread_ticks_threshold, bool)
            or not math.isfinite(max_spread_ticks_threshold)
            or max_spread_ticks_threshold < 0
        ):
            raise ValueError(
                f"max_spread_ticks_threshold must be a finite number >= 0, got "
                f"{max_spread_ticks_threshold!r}."
            )
        if (
            not isinstance(min_depth_ratio_threshold, (int, float))
            or isinstance(min_depth_ratio_threshold, bool)
            or not math.isfinite(min_depth_ratio_threshold)
            or not 0 < min_depth_ratio_threshold <= 1
        ):
            raise ValueError(
                f"min_depth_ratio_threshold must be a finite ratio in (0, 1], got "
                f"{min_depth_ratio_threshold!r}. A value > 1 would haircut every "
                f"snapshot including a normal book; <= 0 would never haircut."
            )
        if (
            not isinstance(mandatory_roll_dbe_cutoff, int)
            or isinstance(mandatory_roll_dbe_cutoff, bool)
            or mandatory_roll_dbe_cutoff < 0
        ):
            raise ValueError(
                f"mandatory_roll_dbe_cutoff must be an integer count of business "
                f"days >= 0, got {mandatory_roll_dbe_cutoff!r}."
            )
        if (
            not isinstance(size_haircut_factor, (int, float))
            or isinstance(size_haircut_factor, bool)
            or not math.isfinite(size_haircut_factor)
            or not 0 < size_haircut_factor <= 1
        ):
            raise ValueError(
                f"size_haircut_factor must be a finite multiplier in (0, 1], got "
                f"{size_haircut_factor!r}."
            )

        self.max_spread_ticks_threshold = float(max_spread_ticks_threshold)
        self.min_depth_ratio_threshold = float(min_depth_ratio_threshold)
        self.mandatory_roll_dbe_cutoff = mandatory_roll_dbe_cutoff
        self.size_haircut_factor = float(size_haircut_factor)

    def audit_expiry_execution_safeguards(
        self,
        order_book: FuturesOrderBookState,
        base_order_qty: int,
    ) -> FuturesExpiryRiskReport:
        """Return the execution constraints implied by one order-book snapshot.

        Evaluation order -- the expiration audit runs first because it can void the
        rest of the decision:

        1. ``days_to_expiration < 0`` -> ``EXPIRED_ESCALATE``. The contract has
           stopped trading, so no size is executable: the report caps quantity at
           ``0``, blocks market orders and new entries, and does **not** mandate a
           roll, because the leg that would be lifted no longer trades.
        2. ``days_to_expiration <= mandatory_roll_dbe_cutoff`` ->
           ``MANDATORY_ROLL_REQUIRED``. Still tradable; entries blocked.
        3. Any of wide spread / thin depth / quad-witching week ->
           ``EXPIRY_WEEK_RESTRICTED``.
        4. Otherwise ``NORMAL_EXECUTION``.

        The spread, depth and quad-witching tests are evaluated in **every** branch
        and every condition that fired is reported in ``restriction_reasons``, so a
        mandatory-roll report still carries the state of the book the roll has to
        be executed into.

        Args:
            order_book: Validated microstructure snapshot of the expiring contract.
            base_order_qty: Positive integer quantity the strategy would send
                absent any expiry-week constraint.

        Returns:
            A ``FuturesExpiryRiskReport``.

        Raises:
            ValueError: If ``base_order_qty`` is not a positive integer, or if the
                snapshot fails ``FuturesOrderBookState.validate``.
        """
        if not isinstance(order_book, FuturesOrderBookState):
            raise ValueError(
                f"order_book must be a FuturesOrderBookState, got "
                f"{type(order_book).__name__}."
            )
        if not isinstance(base_order_qty, int) or isinstance(base_order_qty, bool):
            raise ValueError(
                f"base_order_qty must be an integer number of contracts, got "
                f"{base_order_qty!r}."
            )
        if base_order_qty <= 0:
            raise ValueError(
                f"base_order_qty must be > 0, got {base_order_qty}. Size the order "
                f"first, then apply the expiry haircut to it."
            )
        order_book.validate()

        # --- Microstructure tests (evaluated in every branch) --------------------
        is_spread_wide = (
            order_book.bid_ask_spread_ticks > self.max_spread_ticks_threshold
        )
        depth_ratio = (
            order_book.top_of_book_depth_qty / order_book.baseline_average_depth_qty
        )
        is_depth_thinned = depth_ratio < self.min_depth_ratio_threshold
        is_quad_witching = order_book.is_quadruple_witching_week

        # --- Expiration audit (runs first; can void the rest) --------------------
        is_past_last_trading_day = order_book.days_to_expiration < 0
        is_dbe_critical = order_book.days_to_expiration <= self.mandatory_roll_dbe_cutoff

        reasons: List[str] = []
        if is_past_last_trading_day:
            reasons.append(REASON_PAST_LAST_TRADING_DAY)
        elif is_dbe_critical:
            reasons.append(REASON_EXPIRATION_CUTOFF)
        if is_spread_wide:
            reasons.append(REASON_WIDE_SPREAD)
        if is_depth_thinned:
            reasons.append(REASON_THIN_DEPTH)
        if is_quad_witching:
            reasons.append(REASON_QUAD_WITCHING)

        # Market orders are gated on the spread alone: the spread is the immediate
        # cost of crossing, whereas one depth level does not bound market-order cost.
        market_orders_allowed = not is_spread_wide

        haircut_factor = (
            self.size_haircut_factor if (is_depth_thinned or is_quad_witching) else 1.0
        )
        adjusted_qty = math.floor(base_order_qty * haircut_factor)
        book_summary = (
            f"Spread = {order_book.bid_ask_spread_ticks:.2f} ticks "
            f"(threshold {self.max_spread_ticks_threshold:.2f}, market orders "
            f"{'BLOCKED' if is_spread_wide else 'OK'}), depth = "
            f"{order_book.top_of_book_depth_qty:,} ({depth_ratio * 100:.1f}% of "
            f"baseline {order_book.baseline_average_depth_qty:,}), "
            f"QuadWitching={is_quad_witching}"
        )

        if is_past_last_trading_day:
            status = STATUS_EXPIRED_ESCALATE
            market_orders_allowed = False
            adjusted_qty = 0
            new_entry_allowed = False
            mandatory_roll = False
            notes = (
                f"FUTURES EXPIRY ESCALATE [{order_book.symbol}]: "
                f"days_to_expiration = {order_book.days_to_expiration}d -- the "
                f"contract has stopped trading. No order can be sent and the "
                f"position cannot be rolled out of this leg; escalate to a human. "
                f"{book_summary}."
            )
            logger.critical(notes)
        elif is_dbe_critical:
            status = STATUS_MANDATORY_ROLL
            new_entry_allowed = False
            mandatory_roll = True
            notes = (
                f"FUTURES EXPIRY MANDATORY ROLL [{order_book.symbol}]: DBE = "
                f"{order_book.days_to_expiration}d <= "
                f"{self.mandatory_roll_dbe_cutoff}d cutoff. New entries BLOCKED; "
                f"roll to the next expiration. {book_summary}. Size cap for the "
                f"risk-reducing order: {base_order_qty} -> {adjusted_qty} "
                f"({haircut_factor * 100:.0f}%)."
            )
            logger.warning(notes)
        elif is_spread_wide or is_depth_thinned or is_quad_witching:
            status = STATUS_RESTRICTED
            new_entry_allowed = True
            mandatory_roll = False
            notes = (
                f"FUTURES EXPIRY RESTRICTED [{order_book.symbol}]: {book_summary}. "
                f"Order qty capped {base_order_qty} -> {adjusted_qty} "
                f"({haircut_factor * 100:.0f}%). Reasons: {', '.join(reasons)}."
            )
            logger.warning(notes)
        else:
            status = STATUS_NORMAL
            new_entry_allowed = True
            mandatory_roll = False
            notes = (
                f"FUTURES EXPIRY NORMAL [{order_book.symbol}]: order book liquid. "
                f"Full size {base_order_qty} allowed. {book_summary}."
            )
            logger.info(notes)

        is_order_size_suppressed = adjusted_qty <= 0
        if is_order_size_suppressed and status != STATUS_EXPIRED_ESCALATE:
            notes += (
                f" SIZE SUPPRESSED: the {haircut_factor * 100:.0f}% haircut floors "
                f"a base quantity of {base_order_qty} to 0 -- do not send the order, "
                f"rather than sending a zero-quantity one."
            )
            logger.warning(
                "Expiry haircut suppressed the order entirely for %s "
                "(base_order_qty=%d, haircut=%.2f).",
                order_book.symbol,
                base_order_qty,
                haircut_factor,
            )

        return FuturesExpiryRiskReport(
            symbol=order_book.symbol,
            days_to_expiration=order_book.days_to_expiration,
            is_market_orders_allowed=market_orders_allowed,
            size_haircut_factor=haircut_factor,
            adjusted_max_order_qty=adjusted_qty,
            is_new_entry_allowed=new_entry_allowed,
            is_mandatory_roll_required=mandatory_roll,
            status=status,
            audit_notes=notes,
            depth_ratio=depth_ratio,
            is_spread_wide=is_spread_wide,
            is_depth_thinned=is_depth_thinned,
            restriction_reasons=reasons,
            is_order_size_suppressed=is_order_size_suppressed,
            requires_manual_escalation=(status == STATUS_EXPIRED_ESCALATE),
        )

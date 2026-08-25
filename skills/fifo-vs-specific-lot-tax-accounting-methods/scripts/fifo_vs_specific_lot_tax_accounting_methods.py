"""Tax lot matching for securities: FIFO, LIFO, HIFO and Specific Identification.

Matches a sell order against an inventory of open tax lots, computes the realized
gain or loss per lot, and classifies each match as short-term (STCG) or long-term
(LTCG).

Jurisdiction: **United States federal income tax only.** Every rule encoded here
is a US rule -- the first-in-first-out default absent an adequate identification,
the "more than one year" long-term holding period, and the requirement that a
specific identification be made no later than the earlier of the settlement date
or the Rule 15c6-1 settlement time. Other regimes differ structurally: the UK
matches disposals same-day / next-30-days / Section 104 pool with no taxpayer
election at all, and India mandates FIFO for dematerialised securities. See
`references/standards.md` for the sourced citations. Do not reuse this
classification logic for another jurisdiction without re-deriving its rules.

This module computes and records. It does not file anything, does not apply
wash-sale adjustments (see `wash-sale-rule-tracking-us`), does not adjust basis
for corporate actions, and does not choose a method for the taxpayer.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Matching strategies. FIFO is the default because, absent an adequate
# identification, Treas. Reg. 1.1012-1(c)(1) charges the sale against the
# earliest lot acquired.
STRATEGY_FIFO = "FIFO"
STRATEGY_LIFO = "LIFO"
STRATEGY_HIFO = "HIFO"
STRATEGY_SPECIFIC_LOT = "SPECIFIC_LOT"
SUPPORTED_STRATEGIES = (
    STRATEGY_FIFO, STRATEGY_LIFO, STRATEGY_HIFO, STRATEGY_SPECIFIC_LOT,
)

# Every strategy other than FIFO departs from the regulatory default and is
# therefore an election of specific identification: LIFO and HIFO are just
# standing instructions for which particular shares to deliver. All three require
# a contemporaneous identification record.
SPECIFIC_ID_STRATEGIES = (STRATEGY_LIFO, STRATEGY_HIFO, STRATEGY_SPECIFIC_LOT)

TERM_SHORT = "STCG"                     # holding period of 1 year or less
TERM_LONG = "LTCG"                      # holding period of more than 1 year

# Quantity tolerance for float dust when depleting lots. Well below the smallest
# fraction of a share any broker supports.
_QUANTITY_EPSILON = 1e-9

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def parse_trade_date(value: str, field_name: str = "date") -> date:
    """Parse an accepted date string into a ``date``.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM:SS`` and ``YYYY-MM-DD HH:MM:SS``
    (optionally with a trailing ``Z``).

    Dates are parsed rather than compared as strings. Lexicographic ordering of
    date strings is only correct when every value is zero-padded and in the same
    format: ``"2024-10-05" < "2024-9-01"`` sorts October before September, which
    silently reverses FIFO/LIFO ordering and changes which lots are consumed.

    Raises:
        ValueError: if the value is not a string in one of the accepted formats.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"{field_name} {value!r} is not an accepted format "
        f"(expected one of {', '.join(_DATE_FORMATS)}, optional trailing 'Z')"
    )


def one_year_anniversary(acquisition: date) -> date:
    """Return the date one year after ``acquisition``.

    February 29 has no anniversary in a common year. This module resolves that to
    March 1, which is the conservative choice: it makes long-term treatment
    require one more day rather than one fewer. The IRS guidance consulted for
    this module does not settle the February 29 case, so a taxpayer acquiring on
    a leap day and disposing on February 28 / March 1 of the following year
    should confirm the boundary with a tax adviser. This matches the behaviour of
    `crypto-transaction-tax-lot-tracking`.
    """
    try:
        return acquisition.replace(year=acquisition.year + 1)
    except ValueError:
        return date(acquisition.year + 1, 3, 1)


def is_long_term(acquisition: date, sale: date) -> bool:
    """True if the holding period is **more than one year**.

    The holding period begins on the day after acquisition and includes the day
    of disposition (Rev. Rul. 66-7; Instructions for Form 8949), so a sale on the
    one-year anniversary is exactly one year -- short-term -- and long-term
    treatment begins the following day.

    This is deliberately calendar arithmetic rather than a ``days_held > 365``
    comparison. Across a leap year 366 elapsed days can still be exactly one
    year: bought 2024-01-01 and sold 2025-01-01 is one year to the day, which a
    day count misclassifies as long-term.
    """
    return sale > one_year_anniversary(acquisition)


def holding_period_days(acquisition: date, sale: date) -> int:
    """Elapsed days from acquisition through sale, for the audit trail only.

    Term classification uses :func:`is_long_term`. Do not re-derive the term from
    this number.
    """
    return (sale - acquisition).days


@dataclass
class OpenTaxLot:
    """One open (undepleted) tax lot of a security.

    ``cost_basis_per_share`` is the per-share basis and should already include
    the acquisition-side commissions and fees allocable to this lot, and any
    prior adjustment (wash-sale basis addition, corporate action). This module
    does not compute those adjustments.

    Note: there is deliberately no ``holding_period_days`` field. A holding
    period is a function of the acquisition date *and the sale date*, so a number
    frozen on the lot is stale for every sale after the one it was computed for,
    and can contradict ``acquisition_date_iso`` outright.
    """

    lot_id: str
    symbol: str
    acquisition_date_iso: str           # 'YYYY-MM-DD' or ISO-8601 with time
    quantity: float                     # fractional shares are supported
    cost_basis_per_share: float


@dataclass
class RealizedLotMatch:
    """One matched lot within a sale -- one Form 8949 row.

    Each row carries its own acquisition date, sale date, proceeds, basis and
    term, because a single sale can straddle Part I (short-term) and Part II
    (long-term) of Form 8949.
    """

    lot_id: str
    symbol: str
    shares_matched: float
    acquisition_date_iso: str
    sale_date_iso: str
    holding_period_days: int
    cost_basis_per_share: float
    sale_price_per_share: float
    cost_basis_usd: float
    proceeds_usd: float
    realized_gain_loss_usd: float
    capital_gain_type: str              # TERM_SHORT ('STCG') or TERM_LONG ('LTCG')


@dataclass
class TaxLotAccountingReport:
    """Outcome of one sell order: aggregates plus the per-lot breakdown."""

    symbol: str
    matching_strategy_used: str         # one of SUPPORTED_STRATEGIES
    sale_date_iso: str
    total_shares_sold: float
    total_sale_proceeds_usd: float
    total_cost_basis_usd: float
    total_realized_gain_loss_usd: float
    total_stcg_gain_loss_usd: float
    total_ltcg_gain_loss_usd: float
    matched_lots: List[RealizedLotMatch]
    remaining_open_lots: List[OpenTaxLot]
    audit_notes: str
    identification_reference: Optional[str] = None

    @property
    def is_mixed_term(self) -> bool:
        """True if this sale spans both short-term and long-term lots.

        Such a sale cannot be reported as a single Form 8949 row -- it splits
        across Part I and Part II. Use ``matched_lots``, not the aggregates.
        """
        return len({m.capital_gain_type for m in self.matched_lots}) > 1


class TaxLotAccountingEngine:
    """Matches a sell order against open tax lots under a chosen method.

    FIFO is the default and needs no election. LIFO, HIFO and explicit
    SPECIFIC_LOT are all forms of specific identification and require an
    ``identification_reference`` recording the identification; the engine refuses
    them otherwise rather than producing a basis figure the taxpayer cannot
    support on audit.

    The engine is stateless -- it does not retain lots between calls. Callers
    hold the inventory and feed back ``report.remaining_open_lots``.
    """

    def __init__(self, default_strategy: str = STRATEGY_FIFO) -> None:
        """
        Args:
            default_strategy: Method used when a sell order does not name one.
                Defaults to FIFO, the treatment that applies absent an adequate
                identification.

        Raises:
            ValueError: if the strategy is not one of ``SUPPORTED_STRATEGIES``.
        """
        self.default_strategy = _validate_strategy(default_strategy)

    def process_sell_order(
        self,
        open_lots: Sequence[OpenTaxLot],
        sale_qty: float,
        sale_price: float,
        sale_date: str,
        strategy: Optional[str] = None,
        target_lot_ids: Optional[Sequence[str]] = None,
        identification_reference: Optional[str] = None,
    ) -> TaxLotAccountingReport:
        """Match a sell order against ``open_lots`` and return the per-lot breakdown.

        The sale is applied atomically to copies: the caller's ``open_lots`` are
        never mutated, and a sale that cannot be satisfied raises before any
        result is produced.

        Args:
            open_lots: Open lots for a single symbol. Mixing symbols raises --
                a sale of one security must never consume another's basis.
            sale_qty: Shares sold. Must be positive.
            sale_price: Gross proceeds per share, net of selling expenses if the
                caller nets them. Zero is permitted (a worthless or zero-proceeds
                disposition); negative is not.
            sale_date: Trade date of the sale, 'YYYY-MM-DD' or ISO-8601. Required
                -- the holding period, and therefore STCG vs LTCG, cannot be
                determined without it.
            strategy: FIFO, LIFO, HIFO or SPECIFIC_LOT. Defaults to the engine's
                ``default_strategy``.
            target_lot_ids: For SPECIFIC_LOT, the designated lots **in delivery
                order**. Only these lots are consumed.
            identification_reference: Record of the identification supporting a
                non-FIFO election -- e.g. a broker confirmation id or a standing
                instruction id. Required for LIFO, HIFO and SPECIFIC_LOT.

        Returns:
            A :class:`TaxLotAccountingReport`. ``remaining_open_lots`` are copies
            in the caller's original order, excluding fully depleted lots.

        Raises:
            ValueError: on an unsupported strategy, a non-FIFO election with no
                identification reference, invalid quantities or amounts, mixed or
                duplicate lots, an unparseable date, a lot acquired after the
                sale, insufficient inventory, or SPECIFIC_LOT designations that
                do not cover the sale quantity.
        """
        strat = _validate_strategy(strategy or self.default_strategy)
        if strat in SPECIFIC_ID_STRATEGIES and not identification_reference:
            raise ValueError(
                f"{strat} departs from the FIFO default and is an election of specific "
                "identification. It requires identification_reference -- a record of the "
                "identification made no later than the earlier of the settlement date or "
                "the Rule 15c6-1 settlement time (a standing instruction qualifies). "
                "Absent an adequate identification the shares are charged against the "
                f"earliest lot; pass strategy='{STRATEGY_FIFO}' if that is what actually "
                "happened."
            )

        sale_qty = _validate_positive(sale_qty, "sale_qty")
        sale_price = _validate_non_negative(sale_price, "sale_price")
        sale_dt = parse_trade_date(sale_date, "sale_date")

        candidates = self._validated_candidates(open_lots, sale_dt, sale_date)

        available = sum(lot.quantity for lot in candidates)
        if available + _QUANTITY_EPSILON < sale_qty:
            raise ValueError(
                f"Insufficient open tax lot quantity ({available}) for sell order "
                f"({sale_qty}). No lots were consumed. A shortfall usually means a "
                "missing acquisition, a lot recorded under the wrong symbol or account, "
                "or a double-counted sale -- it must not be resolved by inventing basis."
            )

        ordered = _order_lots(candidates, strat, target_lot_ids)

        # Phase 1: plan without mutating, so a failure leaves nothing half-applied.
        plan: List[Tuple[OpenTaxLot, float]] = []
        remaining = sale_qty
        for lot in ordered:
            if remaining <= _QUANTITY_EPSILON:
                break
            take = min(remaining, lot.quantity)
            plan.append((lot, take))
            remaining -= take

        if remaining > _QUANTITY_EPSILON:
            # Only reachable for SPECIFIC_LOT, where `ordered` is restricted to the
            # designated lots. Spilling into undesignated lots would silently
            # deliver shares the taxpayer never identified.
            designated = sum(lot.quantity for lot in ordered)
            raise ValueError(
                f"SPECIFIC_LOT designation covers only {designated} share(s) but "
                f"{sale_qty} were sold. No lots were consumed. Designate additional "
                f"lots in target_lot_ids, or run the undesignated remainder as a "
                f"separate {STRATEGY_FIFO} sale -- the engine will not silently deliver "
                "shares that were never identified."
            )

        # Phase 2: commit against the copies.
        matches: List[RealizedLotMatch] = []
        for lot, take in plan:
            lot.quantity -= take
            acquired = parse_trade_date(lot.acquisition_date_iso)
            lot_basis = take * lot.cost_basis_per_share
            lot_proceeds = take * sale_price
            matches.append(RealizedLotMatch(
                lot_id=lot.lot_id,
                symbol=lot.symbol,
                shares_matched=take,
                acquisition_date_iso=lot.acquisition_date_iso,
                sale_date_iso=sale_date,
                holding_period_days=holding_period_days(acquired, sale_dt),
                cost_basis_per_share=lot.cost_basis_per_share,
                sale_price_per_share=sale_price,
                cost_basis_usd=round(lot_basis, 2),
                proceeds_usd=round(lot_proceeds, 2),
                realized_gain_loss_usd=round(lot_proceeds - lot_basis, 2),
                capital_gain_type=TERM_LONG if is_long_term(acquired, sale_dt) else TERM_SHORT,
            ))

        raw_basis = sum(take * lot.cost_basis_per_share for lot, take in plan)
        raw_stcg = sum(
            take * (sale_price - lot.cost_basis_per_share)
            for (lot, take), m in zip(plan, matches) if m.capital_gain_type == TERM_SHORT
        )
        raw_ltcg = sum(
            take * (sale_price - lot.cost_basis_per_share)
            for (lot, take), m in zip(plan, matches) if m.capital_gain_type == TERM_LONG
        )

        total_proceeds = round(sale_qty * sale_price, 2)
        total_cost = round(raw_basis, 2)
        total_stcg = round(raw_stcg, 2)
        total_ltcg = round(raw_ltcg, 2)
        # Derived from the rounded parts so the STCG/LTCG split always reconciles to
        # the total exactly; it can differ from (proceeds - basis) by up to a cent.
        total_pnl = round(total_stcg + total_ltcg, 2)

        symbol = candidates[0].symbol
        notes = (
            f"TAX LOT ACCOUNTING MATCH COMPLETE [{strat}]: Sold {sale_qty} sh of {symbol} "
            f"@ ${sale_price:,.2f} on {sale_date}. Total realized PnL = ${total_pnl:,.2f} "
            f"(STCG = ${total_stcg:,.2f}, LTCG = ${total_ltcg:,.2f}) across "
            f"{len(matches)} tax lot(s)."
        )
        logger.info(notes)

        report = TaxLotAccountingReport(
            symbol=symbol,
            matching_strategy_used=strat,
            sale_date_iso=sale_date,
            total_shares_sold=sale_qty,
            total_sale_proceeds_usd=total_proceeds,
            total_cost_basis_usd=total_cost,
            total_realized_gain_loss_usd=total_pnl,
            total_stcg_gain_loss_usd=total_stcg,
            total_ltcg_gain_loss_usd=total_ltcg,
            matched_lots=matches,
            remaining_open_lots=[lot for lot in candidates if lot.quantity > _QUANTITY_EPSILON],
            audit_notes=notes,
            identification_reference=identification_reference,
        )
        if report.is_mixed_term:
            logger.warning(
                "SALE of %s on %s spans short-term and long-term lots; it reports as "
                "multiple Form 8949 rows across Part I and Part II. Use matched_lots, "
                "not the aggregate figures.", symbol, sale_date,
            )
        return report

    @staticmethod
    def _validated_candidates(
        open_lots: Sequence[OpenTaxLot], sale_dt: date, sale_date: str,
    ) -> List[OpenTaxLot]:
        """Validate the inventory and return independent copies to match against.

        Copies are returned in the caller's original order so that ties break
        deterministically on insertion order under Python's stable sort, and so
        ``remaining_open_lots`` comes back in a stable order.
        """
        if not open_lots:
            raise ValueError("open_lots cannot be empty.")

        copies: List[OpenTaxLot] = []
        seen_ids = set()
        symbols = set()
        for lot in open_lots:
            if not isinstance(lot, OpenTaxLot):
                raise ValueError(f"open_lots must contain OpenTaxLot instances, got {lot!r}")
            if lot.lot_id in seen_ids:
                raise ValueError(
                    f"Duplicate lot_id {lot.lot_id!r} in open_lots. Lot ids must be unique "
                    "or a specific identification cannot name one unambiguously."
                )
            seen_ids.add(lot.lot_id)
            symbols.add(lot.symbol)

            _validate_positive(lot.quantity, f"lot {lot.lot_id!r} quantity")
            _validate_non_negative(
                lot.cost_basis_per_share, f"lot {lot.lot_id!r} cost_basis_per_share"
            )
            acquired = parse_trade_date(
                lot.acquisition_date_iso, f"lot {lot.lot_id!r} acquisition_date_iso"
            )
            if acquired > sale_dt:
                raise ValueError(
                    f"Lot {lot.lot_id!r} was acquired {lot.acquisition_date_iso}, after the "
                    f"sale date {sale_date}. A lot cannot be matched against a sale that "
                    "predates it."
                )
            copies.append(OpenTaxLot(
                lot_id=lot.lot_id,
                symbol=lot.symbol,
                acquisition_date_iso=lot.acquisition_date_iso,
                quantity=float(lot.quantity),
                cost_basis_per_share=float(lot.cost_basis_per_share),
            ))

        if len(symbols) > 1:
            raise ValueError(
                f"open_lots spans multiple symbols ({', '.join(sorted(symbols))}). Process "
                "one symbol per sell order -- a sale of one security must never consume "
                "another security's basis."
            )
        return copies


def _order_lots(
    lots: Sequence[OpenTaxLot], strategy: str, target_lot_ids: Optional[Sequence[str]],
) -> List[OpenTaxLot]:
    """Rank candidate lots for the given strategy.

    For SPECIFIC_LOT the result is restricted to the designated lots, in the
    order they were designated -- undesignated lots are not candidates at all.
    For the other strategies ties break on the caller's input order (Python's
    sort is stable), so the same inputs always produce the same match plan.
    """
    if strategy == STRATEGY_SPECIFIC_LOT:
        if not target_lot_ids:
            raise ValueError(
                f"target_lot_ids must be provided for {STRATEGY_SPECIFIC_LOT} matching."
            )
        by_id = {lot.lot_id: lot for lot in lots}
        seen = set()
        designated: List[OpenTaxLot] = []
        for lot_id in target_lot_ids:
            if lot_id not in by_id:
                raise ValueError(
                    f"Designated lot_id {lot_id!r} is not an open lot in this inventory. "
                    "An identification naming a lot that does not exist is not an "
                    "adequate identification."
                )
            if lot_id in seen:
                raise ValueError(
                    f"Designated lot_id {lot_id!r} appears twice in target_lot_ids."
                )
            seen.add(lot_id)
            designated.append(by_id[lot_id])
        return designated

    if strategy == STRATEGY_HIFO:
        return sorted(lots, key=lambda lot: lot.cost_basis_per_share, reverse=True)
    if strategy == STRATEGY_LIFO:
        return sorted(
            lots, key=lambda lot: parse_trade_date(lot.acquisition_date_iso), reverse=True
        )
    return sorted(lots, key=lambda lot: parse_trade_date(lot.acquisition_date_iso))


def _validate_strategy(strategy: str) -> str:
    """Validate a matching strategy, rejecting anything unrecognised.

    An unrecognised strategy must never fall through to a default: a typo
    silently treated as FIFO changes which lots are consumed and therefore the
    tax owed.
    """
    if not isinstance(strategy, str) or strategy.upper() not in SUPPORTED_STRATEGIES:
        raise ValueError(
            f"Unsupported matching strategy {strategy!r}; expected one of "
            f"{', '.join(SUPPORTED_STRATEGIES)}"
        )
    return strategy.upper()


def _validate_finite(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _validate_positive(value: float, name: str) -> float:
    _validate_finite(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return float(value)


def _validate_non_negative(value: float, name: str) -> float:
    _validate_finite(value, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return float(value)

"""US wash sale tracking under IRC section 1091 for a single symbol's trade ledger.

Consumes a chronological ledger of long-side buy/sell executions, matches sells
against open tax lots FIFO, identifies realized losses, searches the 61-day
window (30 days before the disposition, the disposition date, 30 days after) for
replacement acquisitions, disallows the matched portion of each loss, and carries
the disallowed amount forward into the replacement lot's basis so that a later
disposition of those replacement shares reports the deferred loss.

Jurisdiction: **United States federal income tax only.** The rules encoded here
are 26 U.S.C. section 1091 and Treas. Reg. section 1.1091-1. See
``references/standards.md`` for the sourced citations, and the "When NOT to Use"
section of ``SKILL.md`` for the cases this module deliberately does not model --
short sales (section 1091(e)), option and contract acquisitions (Treas. Reg.
section 1.1091-1(f)), cross-account and IRA replacement (Rev. Rul. 2008-5),
"substantially identical" securities carrying a different identifier, dealers
(section 1091(a)), and section 475(f) mark-to-market traders.

This module computes and records. It does not file anything, does not itself
produce a Form 1099-B, and does not decide what is "substantially identical" --
the caller decides that by choosing which executions share a ``symbol``.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# Share quantities are floats (fractional shares are routine). Compare against a
# tolerance rather than 0.0 so repeated subtraction cannot leave a lot open with
# a residue of 1e-16 shares.
_QTY_EPS = 1e-9

# 26 U.S.C. 1091(a): "within a period beginning 30 days before the date of such
# sale or disposition and ending 30 days after such date". 30 + 1 + 30 = 61 days.
IRC_1091_WINDOW_DAYS = 30


class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class WashSaleError(Exception):
    """Raised for ledger inputs this engine cannot account for correctly."""


@dataclass
class TradeExecution:
    """One long-side execution, in a single account, for one security identifier."""

    trade_id: str
    symbol: str
    trade_date: datetime.date
    side: TradeSide
    price: float
    quantity: float


@dataclass
class WashSaleMatch:
    """One loss slice matched against one replacement acquisition slice.

    ``adjusted_replacement_basis_per_share`` is the section 1091(d) basis of the
    ``matched_quantity`` replacement shares only. Where the replacement
    acquisition is larger than the matched quantity, the unmatched remainder of
    that acquisition keeps its original basis.
    """

    loss_trade_id: str
    replacement_trade_id: str
    symbol: str
    loss_date: datetime.date
    replacement_date: datetime.date
    matched_quantity: float
    disallowed_loss_usd: float
    adjusted_replacement_basis_per_share: float


@dataclass
class WashSaleSummary:
    """Per-symbol, per-account result, mapped to Form 1099-B boxes.

    ``total_proceeds_usd`` maps to Box 1d, ``total_cost_basis_usd`` to Box 1e
    (basis *after* any section 1091(d) adjustment carried into a lot before it
    was sold), ``total_disallowed_wash_loss_usd`` to Box 1g, and
    ``net_allowed_taxable_pnl_usd`` to proceeds - basis + Box 1g.

    ``deferred_loss_in_open_lots_usd`` is the disallowed loss still embedded in
    the basis of lots that are still open at the end of the ledger. It is the
    portion of Box 1g that has not yet been recovered through a later
    disposition, and it is the figure to carry into the next tax year.
    """

    symbol: str
    total_realized_gross_pnl_usd: float
    total_disallowed_wash_loss_usd: float
    net_allowed_taxable_pnl_usd: float
    wash_matches: List[WashSaleMatch] = field(default_factory=list)
    total_proceeds_usd: float = 0.0
    total_cost_basis_usd: float = 0.0
    deferred_loss_in_open_lots_usd: float = 0.0


@dataclass
class _OpenLot:
    """An open (or partially open) share slice with a mutable section 1091(d) basis."""

    trade_id: str
    trade_date: datetime.date
    quantity: float
    basis_per_share: float
    wash_adjustment_per_share: float = 0.0


class USWashSaleTrackingEngine:
    """US IRS wash sale tracking engine (26 U.S.C. section 1091).

    Evaluation is a single chronological pass, and that ordering is the point: a
    disallowed loss increases the basis of the replacement lot under section
    1091(d), so if those replacement shares are themselves sold later in the
    ledger, the increased basis must be the basis used for that later
    disposition. Computing realized P&L from unadjusted basis first and adding
    disallowed losses back afterwards double counts the deferral.
    """

    def __init__(self, window_days: int = IRC_1091_WINDOW_DAYS):
        """
        :param window_days: Half-width of the section 1091(a) window, in days.
            The statutory value is 30 (a 61-day window inclusive of the
            disposition date). Exposed only so tests and sensitivity analyses can
            vary it; any other value produces a non-statutory result and is
            logged as such.
        """
        if isinstance(window_days, bool) or not isinstance(window_days, int):
            raise WashSaleError("window_days must be an int (statutory value: 30).")
        if window_days < 0:
            raise WashSaleError("window_days must be non-negative.")
        if window_days != IRC_1091_WINDOW_DAYS:
            logger.warning(
                "Wash sale window set to +/-%d days; 26 U.S.C. 1091(a) specifies +/-%d. "
                "Output is not a statutory section 1091 result.",
                window_days,
                IRC_1091_WINDOW_DAYS,
            )
        self.window_days = window_days
        self.trades: List[TradeExecution] = []
        self._trade_ids: Dict[str, str] = {}
        logger.info("Initialized US wash sale engine (IRC 1091 window = +/-%d days)", window_days)

    # ------------------------------------------------------------------ ingest

    def add_trade(self, trade: TradeExecution) -> None:
        """Append an execution to the ledger.

        Add executions in execution sequence. Evaluation sorts by ``trade_date``
        with a stable sort, so insertion order breaks ties between same-day
        executions: a same-day buy added before a same-day sell is available for
        that sell to consume FIFO, and one added after it is not.
        """
        if not isinstance(trade, TradeExecution):
            raise WashSaleError("trade must be a TradeExecution.")
        if not isinstance(trade.trade_id, str) or not trade.trade_id:
            raise WashSaleError("trade_id must be a non-empty string.")
        if not isinstance(trade.symbol, str) or not trade.symbol:
            raise WashSaleError("symbol must be a non-empty string.")
        if not isinstance(trade.side, TradeSide):
            raise WashSaleError("side must be a TradeSide member.")
        if not isinstance(trade.trade_date, datetime.date) or isinstance(
            trade.trade_date, datetime.datetime
        ):
            # datetime.datetime subclasses date. Rejecting it keeps the +/-30 day
            # arithmetic on whole trade dates, so a same-day comparison cannot
            # silently depend on a time-of-day component.
            raise WashSaleError("trade_date must be a datetime.date, not a datetime.datetime.")
        if trade.price <= 0 or trade.quantity <= 0:
            raise WashSaleError("Trade price and quantity must be positive.")
        if trade.trade_id in self._trade_ids:
            # Replacement capacity and pending basis adjustments are keyed by
            # trade_id; a duplicate id would silently share and corrupt both.
            raise WashSaleError(f"Duplicate trade_id '{trade.trade_id}' in ledger.")

        self._trade_ids[trade.trade_id] = trade.symbol
        self.trades.append(trade)
        logger.debug(
            "Added trade [%s] %s %s %s @ $%.2f",
            trade.trade_id,
            trade.side.value,
            trade.quantity,
            trade.symbol,
            trade.price,
        )

    # -------------------------------------------------------------- evaluation

    def evaluate_wash_sales_for_symbol(self, symbol: str) -> WashSaleSummary:
        """Evaluate section 1091 disallowances and basis adjustments for one symbol.

        :raises WashSaleError: if a sell cannot be fully matched against open
            lots. An unmatched sell is either a short sale -- governed by section
            1091(e), which this engine does not model -- or an incomplete ledger.
            Either way, realized P&L and the Box 1g total would be understated,
            so it is reported rather than silently skipped.
        """
        if not isinstance(symbol, str) or not symbol:
            raise WashSaleError("symbol must be a non-empty string.")

        # Stable sort on trade_date only, so same-day executions keep insertion order.
        sym_trades = sorted(
            (t for t in self.trades if t.symbol == symbol),
            key=lambda t: t.trade_date,
        )
        if not sym_trades:
            return WashSaleSummary(
                symbol=symbol,
                total_realized_gross_pnl_usd=0.0,
                total_disallowed_wash_loss_usd=0.0,
                net_allowed_taxable_pnl_usd=0.0,
            )

        order_index = {t.trade_id: i for i, t in enumerate(sym_trades)}
        # Acquisitions in order of acquisition. Treas. Reg. 1.1091-1(c) matches
        # replacement shares "beginning with the earliest acquisition".
        buys = [t for t in sym_trades if t.side is TradeSide.BUY]
        # Treas. Reg. 1.1091-1(e): an acquisition whose purchase already made a
        # loss nondeductible is disregarded for every other loss. One replacement
        # share therefore absorbs at most one loss share.
        replacement_capacity: Dict[str, float] = {t.trade_id: t.quantity for t in buys}
        # Adjustments owed to acquisitions the pass has not reached yet.
        pending_adjustments: Dict[str, List[Tuple[float, float]]] = {}

        open_lots: List[_OpenLot] = []
        wash_matches: List[WashSaleMatch] = []
        proceeds = 0.0
        cost_basis = 0.0
        total_disallowed = 0.0

        for sell_index, tr in enumerate(sym_trades):
            if tr.side is TradeSide.BUY:
                open_lots.extend(
                    self._materialise_lot(tr, pending_adjustments.pop(tr.trade_id, ()))
                )
                continue

            # Consume the whole sell first. Only then are the post-sale holdings
            # known, and a share disposed of by this very sell must not then be
            # treated as replacement stock for it.
            sale_proceeds, sale_basis, loss_slices = self._consume_fifo(open_lots, tr)
            proceeds += sale_proceeds
            cost_basis += sale_basis

            # Treas. Reg. 1.1091-1(b) applies section 1091 to losses in order of
            # disposition; the outer loop is chronological and, within one
            # disposition, loss slices come out in FIFO lot order.
            for origin_trade_id, loss_qty, loss_per_share in loss_slices:
                total_disallowed += self._match_replacements(
                    symbol=symbol,
                    sell=tr,
                    sell_index=sell_index,
                    origin_trade_id=origin_trade_id,
                    loss_qty=loss_qty,
                    loss_per_share=loss_per_share,
                    buys=buys,
                    order_index=order_index,
                    replacement_capacity=replacement_capacity,
                    pending_adjustments=pending_adjustments,
                    open_lots=open_lots,
                    wash_matches=wash_matches,
                )

        gross_pnl = proceeds - cost_basis
        net_allowed = gross_pnl + total_disallowed
        deferred = sum((lot.wash_adjustment_per_share * lot.quantity for lot in open_lots), 0.0)

        logger.info(
            "Wash sale evaluation [%s]: Proceeds=$%.2f Basis=$%.2f GrossPnL=$%.2f "
            "Disallowed=$%.2f NetAllowedPnL=$%.2f Matches=%d DeferredInOpenLots=$%.2f",
            symbol,
            proceeds,
            cost_basis,
            gross_pnl,
            total_disallowed,
            net_allowed,
            len(wash_matches),
            deferred,
        )

        return WashSaleSummary(
            symbol=symbol,
            total_realized_gross_pnl_usd=round(gross_pnl, 2),
            total_disallowed_wash_loss_usd=round(total_disallowed, 2),
            net_allowed_taxable_pnl_usd=round(net_allowed, 2),
            wash_matches=wash_matches,
            total_proceeds_usd=round(proceeds, 2),
            total_cost_basis_usd=round(cost_basis, 2),
            deferred_loss_in_open_lots_usd=round(deferred, 2),
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _materialise_lot(
        trade: TradeExecution,
        adjustments: Sequence[Tuple[float, float]],
    ) -> List[_OpenLot]:
        """Open the lot for ``trade``, splitting off any pre-assigned wash slices.

        Adjusted slices are placed ahead of the unadjusted remainder. All shares
        of the acquisition share one acquisition date, so their order within the
        FIFO queue is a convention rather than a rule; fixing it here keeps the
        result deterministic.
        """
        lots: List[_OpenLot] = []
        assigned = 0.0
        for qty, per_share in adjustments:
            lots.append(
                _OpenLot(
                    trade_id=trade.trade_id,
                    trade_date=trade.trade_date,
                    quantity=qty,
                    basis_per_share=trade.price + per_share,
                    wash_adjustment_per_share=per_share,
                )
            )
            assigned += qty
        remainder = trade.quantity - assigned
        if remainder > _QTY_EPS:
            lots.append(
                _OpenLot(
                    trade_id=trade.trade_id,
                    trade_date=trade.trade_date,
                    quantity=remainder,
                    basis_per_share=trade.price,
                )
            )
        return lots

    @staticmethod
    def _consume_fifo(
        open_lots: List[_OpenLot],
        sell: TradeExecution,
    ) -> Tuple[float, float, List[Tuple[str, float, float]]]:
        """Deplete ``open_lots`` FIFO against ``sell``.

        Returns ``(proceeds, cost_basis, loss_slices)`` where each loss slice is
        ``(origin_trade_id, quantity, loss_per_share)`` for the slices that
        realized a loss against their section 1091(d)-adjusted basis.
        """
        remaining = sell.quantity
        proceeds = 0.0
        cost_basis = 0.0
        loss_slices: List[Tuple[str, float, float]] = []

        while remaining > _QTY_EPS and open_lots:
            lot = open_lots[0]
            qty = min(remaining, lot.quantity)
            proceeds += sell.price * qty
            cost_basis += lot.basis_per_share * qty
            if sell.price < lot.basis_per_share:
                loss_slices.append((lot.trade_id, qty, lot.basis_per_share - sell.price))
            lot.quantity -= qty
            remaining -= qty
            if lot.quantity <= _QTY_EPS:
                open_lots.pop(0)

        if remaining > _QTY_EPS:
            raise WashSaleError(
                f"Sell [{sell.trade_id}] for {sell.quantity} {sell.symbol} exceeds open long "
                f"quantity by {remaining}. Short sales are governed by 26 U.S.C. 1091(e) and "
                "are not modelled by this engine; supply a complete long-side ledger."
            )
        return proceeds, cost_basis, loss_slices

    @staticmethod
    def _available_replacement_quantity(open_lots: Sequence[_OpenLot], trade_id: str) -> float:
        """Shares of ``trade_id`` that are still held *and* have absorbed no loss yet.

        Both halves matter. A share disposed of before or by the loss sale is not
        stock the taxpayer holds as a replacement, and a share whose acquisition
        already made another loss nondeductible is disregarded under
        Treas. Reg. 1.1091-1(e). Capping on total held quantity alone
        double counts a share that is held but already adjusted.
        """
        return sum(
            (
                lot.quantity
                for lot in open_lots
                if lot.trade_id == trade_id and lot.wash_adjustment_per_share == 0.0
            ),
            0.0,
        )

    @staticmethod
    def _apply_adjustment_to_open_lots(
        open_lots: List[_OpenLot],
        trade_id: str,
        quantity: float,
        per_share: float,
    ) -> None:
        """Add ``per_share`` to the basis of ``quantity`` still-held shares of ``trade_id``.

        Only slices that carry no wash adjustment yet are eligible, which is
        Treas. Reg. 1.1091-1(e): a share whose acquisition already made a loss
        nondeductible is disregarded for any other loss. Replacement capacity
        accounting guarantees enough unadjusted shares remain.
        """
        remaining = quantity
        i = 0
        while remaining > _QTY_EPS and i < len(open_lots):
            lot = open_lots[i]
            if lot.trade_id != trade_id or lot.wash_adjustment_per_share != 0.0:
                i += 1
                continue
            take = min(remaining, lot.quantity)
            if take < lot.quantity - _QTY_EPS:
                # Split: only ``take`` shares of this slice take the adjustment.
                lot.quantity -= take
                open_lots.insert(
                    i,
                    _OpenLot(
                        trade_id=lot.trade_id,
                        trade_date=lot.trade_date,
                        quantity=take,
                        basis_per_share=lot.basis_per_share + per_share,
                        wash_adjustment_per_share=per_share,
                    ),
                )
                i += 1
            else:
                lot.basis_per_share += per_share
                lot.wash_adjustment_per_share = per_share
            remaining -= take
            i += 1

        if remaining > _QTY_EPS:  # pragma: no cover - guarded by capacity accounting
            raise WashSaleError(
                f"Internal error: could not place a wash adjustment on {remaining} shares "
                f"of acquisition '{trade_id}'."
            )

    def _match_replacements(
        self,
        *,
        symbol: str,
        sell: TradeExecution,
        sell_index: int,
        origin_trade_id: str,
        loss_qty: float,
        loss_per_share: float,
        buys: Sequence[TradeExecution],
        order_index: Dict[str, int],
        replacement_capacity: Dict[str, float],
        pending_adjustments: Dict[str, List[Tuple[float, float]]],
        open_lots: List[_OpenLot],
        wash_matches: List[WashSaleMatch],
    ) -> float:
        """Disallow one loss slice against replacement acquisitions; return the amount disallowed."""
        remaining = loss_qty
        disallowed_total = 0.0

        for rep in buys:
            if remaining <= _QTY_EPS:
                break
            # A lot is never its own replacement: the shares sold and the shares
            # left over from the same acquisition were not bought to replace the
            # shares sold. See references/standards.md, "Same-acquisition shares".
            if rep.trade_id == origin_trade_id:
                continue
            if abs((rep.trade_date - sell.trade_date).days) > self.window_days:
                continue

            capacity = replacement_capacity.get(rep.trade_id, 0.0)
            acquired_before_sell = order_index[rep.trade_id] < sell_index
            if acquired_before_sell:
                # A pre-existing acquisition is replacement stock only to the
                # extent it is still held after this disposition and has not
                # already absorbed a loss. Shares this very sell disposed of are
                # not stock the taxpayer "acquired" to replace the loss shares.
                capacity = min(
                    capacity, self._available_replacement_quantity(open_lots, rep.trade_id)
                )
            if capacity <= _QTY_EPS:
                continue

            matched = min(remaining, capacity)
            disallowed = matched * loss_per_share

            if acquired_before_sell:
                self._apply_adjustment_to_open_lots(
                    open_lots, rep.trade_id, matched, loss_per_share
                )
            else:
                pending_adjustments.setdefault(rep.trade_id, []).append((matched, loss_per_share))

            wash_matches.append(
                WashSaleMatch(
                    loss_trade_id=sell.trade_id,
                    replacement_trade_id=rep.trade_id,
                    symbol=symbol,
                    loss_date=sell.trade_date,
                    replacement_date=rep.trade_date,
                    matched_quantity=matched,
                    disallowed_loss_usd=round(disallowed, 2),
                    # Section 1091(d): basis of the replacement shares is their
                    # purchase price plus the disallowed loss per share. Capacity
                    # accounting means these particular shares carry no earlier
                    # adjustment, so rep.price is their unadjusted basis.
                    adjusted_replacement_basis_per_share=round(rep.price + loss_per_share, 4),
                )
            )

            replacement_capacity[rep.trade_id] = capacity_left = (
                replacement_capacity[rep.trade_id] - matched
            )
            if capacity_left < 0:  # pragma: no cover - defensive
                raise WashSaleError(f"Negative replacement capacity for '{rep.trade_id}'.")
            remaining -= matched
            disallowed_total += disallowed

        return disallowed_total

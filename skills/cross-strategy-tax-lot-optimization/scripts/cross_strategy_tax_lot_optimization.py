"""Cross-strategy tax-lot optimization for multi-strategy US taxable entities.

Scope and jurisdiction
----------------------
This module implements US federal tax-lot mechanics only:

* Specific Lot Identification / HIFO ordering under Treas. Reg. 1.1012-1(c).
  The default when no adequate identification is made is FIFO
  (Treas. Reg. 1.1012-1(c)(1)(i)).
* Short-term vs long-term classification under IRC 1222 ("more than one year").
* A *first-pass* wash-sale interception screen under IRC 1091. Full replacement
  matching, cost-basis adjustment (IRC 1091(d)) and holding-period tacking
  (IRC 1223(3)) are deliberately NOT implemented here - use the
  `wash-sale-rule-tracking-us` skill for the authoritative ledger.

Monetary values use `float`, which is adequate for lot *selection* but not for
cent-exact Form 8949 / 1099-B reporting. Route the selected lots through a
decimal-based reporting ledger before filing.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# IRC 1091(a): 30 days before the loss sale, the sale date itself, and 30 days
# after - a 61-day window. Expressed here as a symmetric +/- day offset.
DEFAULT_WASH_SALE_WINDOW_DAYS = 30

SUPPORTED_METHODS = ("HIFO_MIN_TAX", "LTCG_OPTIMIZED", "FIFO")

# Share quantities below this are binary floating-point dust left by subtraction
# (e.g. 0.1 + 0.2 - 0.3 == 5.55e-17) and are snapped to zero, so a depleted lot
# does not linger as a phantom sub-atomic holding.
QUANTITY_EPSILON = 1e-9


def _require_finite(value: float, field_name: str) -> float:
    """Rejects NaN and +/-Inf, which would otherwise propagate silently into PnL."""
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return value


def _parse_iso_date(value: str, field_name: str) -> date:
    """Parses a YYYY-MM-DD string, raising ValueError with a useful message."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be an ISO YYYY-MM-DD date string, got {value!r}"
        ) from exc


def _add_one_year(start: date) -> date:
    """Returns the one-year anniversary of `start`, clamping 29 Feb to 28 Feb."""
    try:
        return start.replace(year=start.year + 1)
    except ValueError:  # 29 Feb in a non-leap target year
        return start.replace(year=start.year + 1, day=28)


def is_long_term(acquisition_date: date, sale_date: date) -> bool:
    """IRC 1222 long-term test: held for *more than* one year.

    The holding period starts the day after acquisition and includes the
    disposition date, so a lot acquired on D is long-term only when it is
    disposed of strictly after the one-year anniversary of D.
    """
    return sale_date > _add_one_year(acquisition_date)


@dataclass
class TaxLot:
    """An open tax lot held by one sub-strategy under a single tax entity."""

    lot_id: str
    strategy_id: str
    symbol: str
    acquisition_date: str               # YYYY-MM-DD
    days_held: int                      # fallback long-term proxy; see optimize_sell_order
    cost_basis_per_share: float
    quantity: float


@dataclass
class ReplacementPurchase:
    """A buy of a substantially identical security near a loss sale (IRC 1091)."""

    symbol: str
    strategy_id: str
    days_from_sale: int                 # negative = before the sale, positive = after
    quantity: Optional[float] = None    # None = quantity unknown


@dataclass
class StrategyOrder:
    """A single sub-strategy's intended order, before internal netting."""

    strategy_id: str
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    quantity: float


@dataclass
class NettedOrderResult:
    symbol: str
    gross_buy_quantity: float
    gross_sell_quantity: float
    internally_crossed_quantity: float
    net_side: str                       # 'BUY', 'SELL' or 'FLAT'
    net_quantity: float
    wash_sale_risk: bool
    warnings: List[str] = field(default_factory=list)


@dataclass
class SelectedTaxLotExecution:
    lot_id: str
    strategy_id: str
    shares_sold: float
    cost_basis_per_share: float
    execution_price: float
    realized_gain_loss_usd: float
    is_long_term: bool
    is_wash_sale_triggered: bool
    wash_sale_matched_quantity: float = 0.0
    disallowed_loss_usd: float = 0.0


@dataclass
class TaxOptimizationResult:
    symbol: str
    requested_sell_quantity: float
    total_realized_gain_loss_usd: float
    short_term_realized_usd: float
    long_term_realized_usd: float
    executed_lots: List[SelectedTaxLotExecution]
    wash_sale_warning: bool
    method: str = "HIFO_MIN_TAX"
    total_disallowed_loss_usd: float = 0.0
    net_deductible_gain_loss_usd: float = 0.0
    warnings: List[str] = field(default_factory=list)


class CrossStrategyTaxLotOptimizer:
    """Tax-lot selection, internal netting, and wash-sale interception.

    All sub-strategies registered on one instance are assumed to belong to a
    *single* US tax entity. That assumption drives two behaviours that are wrong
    if the pods are separately-taxed entities: internal crossing is treated as a
    non-taxable book transfer, and one pod's buy is treated as a replacement
    purchase against another pod's realized loss.
    """

    def __init__(self, wash_sale_window_days: int = DEFAULT_WASH_SALE_WINDOW_DAYS):
        if wash_sale_window_days < 0:
            raise ValueError("wash_sale_window_days must be non-negative")
        self.wash_sale_window_days = wash_sale_window_days
        self.tax_lots: List[TaxLot] = []
        self.replacement_purchases: List[ReplacementPurchase] = []

    # ------------------------------------------------------------------
    # Inventory registration
    # ------------------------------------------------------------------
    def add_tax_lot(self, lot: TaxLot) -> None:
        """Registers an open tax lot, validating its economics and date format."""
        _require_finite(lot.quantity, f"Lot {lot.lot_id}: quantity")
        _require_finite(lot.cost_basis_per_share, f"Lot {lot.lot_id}: cost_basis_per_share")
        if lot.quantity <= 0:
            raise ValueError(f"Lot {lot.lot_id}: quantity must be > 0, got {lot.quantity}")
        if lot.cost_basis_per_share < 0:
            raise ValueError(
                f"Lot {lot.lot_id}: cost_basis_per_share must be >= 0, "
                f"got {lot.cost_basis_per_share}"
            )
        if lot.days_held < 0:
            raise ValueError(f"Lot {lot.lot_id}: days_held must be >= 0, got {lot.days_held}")
        _parse_iso_date(lot.acquisition_date, f"Lot {lot.lot_id}: acquisition_date")
        self.tax_lots.append(lot)

    def register_replacement_purchase(
        self,
        symbol: str,
        strategy_id: str,
        days_from_sale: int,
        quantity: Optional[float] = None,
    ) -> None:
        """Registers a buy that may be an IRC 1091 replacement purchase.

        `days_from_sale` is signed relative to the loss sale: negative for a buy
        that preceded the sale, positive for one that followed it, 0 for a
        same-day buy. All three fall inside the 61-day window.

        `quantity=None` means the replacement size is unknown; the optimizer then
        treats the replacement as covering the entire loss, which is the
        conservative (larger-disallowance) assumption.
        """
        if quantity is not None:
            _require_finite(quantity, "Replacement purchase quantity")
        if quantity is not None and quantity <= 0:
            raise ValueError(f"Replacement purchase quantity must be > 0, got {quantity}")
        self.replacement_purchases.append(
            ReplacementPurchase(symbol.upper(), strategy_id, int(days_from_sale), quantity)
        )

    def register_recent_buy(
        self,
        symbol: str,
        strategy_id: str,
        days_ago: int,
        quantity: Optional[float] = None,
    ) -> None:
        """Registers a buy `days_ago` days BEFORE the loss sale.

        Retained for backward compatibility. Prefer
        `register_replacement_purchase`, which can also express buys occurring
        *after* the loss sale - the more common wash-sale trigger.
        """
        if days_ago < 0:
            raise ValueError(
                "days_ago must be >= 0; use register_replacement_purchase with a "
                "positive days_from_sale for buys occurring after the loss sale"
            )
        self.register_replacement_purchase(symbol, strategy_id, -days_ago, quantity)

    # ------------------------------------------------------------------
    # Step 1: cross-strategy internal netting
    # ------------------------------------------------------------------
    def net_cross_strategy_orders(self, orders: Sequence[StrategyOrder]) -> NettedOrderResult:
        """Nets offsetting sub-strategy orders for one symbol before broker routing.

        Only the *net* residual should be routed externally and fed to
        `optimize_sell_order`. Within a single tax entity the internally crossed
        quantity never leaves the entity, so it realizes no gain or loss - running
        lot selection on the gross sell quantity would double-count realized
        losses and overstate harvested capital losses.
        """
        if not orders:
            raise ValueError("orders must contain at least one StrategyOrder")

        symbols = {o.symbol.upper() for o in orders}
        if len(symbols) != 1:
            raise ValueError(
                f"net_cross_strategy_orders requires a single symbol, got {sorted(symbols)}"
            )
        symbol = symbols.pop()

        gross_buy = 0.0
        gross_sell = 0.0
        for order in orders:
            side = order.side.upper()
            if side not in ("BUY", "SELL"):
                raise ValueError(f"Order side must be 'BUY' or 'SELL', got {order.side!r}")
            _require_finite(order.quantity, f"Order quantity for strategy {order.strategy_id}")
            if order.quantity <= 0:
                raise ValueError(
                    f"Order quantity must be > 0 for strategy {order.strategy_id}, "
                    f"got {order.quantity}"
                )
            if side == "BUY":
                gross_buy += order.quantity
            else:
                gross_sell += order.quantity

        crossed = min(gross_buy, gross_sell)
        signed_net = gross_buy - gross_sell
        if signed_net > 0:
            net_side = "BUY"
        elif signed_net < 0:
            net_side = "SELL"
        else:
            net_side = "FLAT"

        warnings: List[str] = []
        if crossed > 0:
            warnings.append(
                f"{crossed:,.2f} shares of {symbol} crossed internally between "
                "sub-strategies; treat as a non-taxable book transfer only if every "
                "pod sits inside one tax entity."
            )
            warnings.append(
                "Internal crossing does not cure a wash sale: where the pods are "
                "separate tax entities the buying pod's acquisition remains a "
                "replacement purchase under IRC 1091."
            )
            logger.info(
                f"Internally crossed {crossed:,.2f} shares of {symbol} "
                f"(gross buy {gross_buy:,.2f} / gross sell {gross_sell:,.2f})"
            )

        return NettedOrderResult(
            symbol=symbol,
            gross_buy_quantity=gross_buy,
            gross_sell_quantity=gross_sell,
            internally_crossed_quantity=crossed,
            net_side=net_side,
            net_quantity=abs(signed_net),
            wash_sale_risk=crossed > 0,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Step 2: tax-lot selection
    # ------------------------------------------------------------------
    def _sorted_lots(self, lots: List[TaxLot], method: str) -> List[TaxLot]:
        if method == "HIFO_MIN_TAX":
            # Highest cost basis first -> maximizes losses / minimizes gains.
            return sorted(lots, key=lambda l: l.cost_basis_per_share, reverse=True)
        if method == "LTCG_OPTIMIZED":
            # Long-term lots first (preferential rate), then highest cost basis
            # within each holding-period bucket.
            return sorted(
                lots,
                key=lambda l: (l.days_held > 365, l.cost_basis_per_share),
                reverse=True,
            )
        # FIFO: Treas. Reg. 1.1012-1(c)(1)(i) charges the sale against the
        # *earliest lot acquired*, so sort on acquisition date, not days_held.
        return sorted(lots, key=lambda l: (l.acquisition_date, -l.days_held))

    def _replacement_pool(self, symbol_upper: str) -> Optional[float]:
        """Total replacement shares inside the +/- window around the loss sale.

        Returns None when any in-window replacement purchase has an unknown
        quantity, signalling "assume full coverage".
        """
        total = 0.0
        for buy in self.replacement_purchases:
            if buy.symbol != symbol_upper:
                continue
            if abs(buy.days_from_sale) > self.wash_sale_window_days:
                continue
            if buy.quantity is None:
                return None
            total += buy.quantity
        return total

    def optimize_sell_order(
        self,
        symbol: str,
        sell_quantity: float,
        current_market_price: float,
        method: str = "HIFO_MIN_TAX",
        sale_date: Optional[str] = None,
        dry_run: bool = False,
    ) -> TaxOptimizationResult:
        """Selects tax lots for a sell order and screens them for wash sales.

        Args:
            symbol: Security being sold.
            sell_quantity: Shares to sell. Must be > 0 and <= open inventory.
                Pass the *net* quantity from `net_cross_strategy_orders`, not the
                gross sub-strategy total.
            current_market_price: Execution price per share. Must be > 0.
            method: One of `SUPPORTED_METHODS`. Unrecognized values raise rather
                than silently degrading to FIFO.
            sale_date: Optional YYYY-MM-DD disposition date. When supplied, the
                long-term test uses calendar arithmetic against each lot's
                `acquisition_date`; otherwise it falls back to the
                `days_held > 365` proxy, which misclassifies lots whose holding
                period spans a leap day.
            dry_run: When True the open inventory is left untouched, so the same
                inventory can be scored under several methods before committing.

        Returns:
            A `TaxOptimizationResult`. `total_disallowed_loss_usd` is the IRC 1091
            disallowance for this sale only; the corresponding basis increase on
            the replacement shares (IRC 1091(d)) is not applied here.

            The replacement pool is evaluated fresh on every call and is *not*
            consumed across calls, so two successive loss sales can each match the
            same registered replacement shares and jointly overstate the total
            disallowance. This screen is per-sale by design; for a multi-sale
            ledger that consumes replacement shares once, use the
            `wash-sale-rule-tracking-us` skill.

        Raises:
            ValueError: on invalid inputs, an unknown method, or insufficient open
                inventory for the requested quantity.
        """
        method_clean = str(method).upper()
        if method_clean not in SUPPORTED_METHODS:
            raise ValueError(
                f"Unsupported method {method!r}; expected one of {list(SUPPORTED_METHODS)}"
            )
        if not sell_quantity > 0:
            raise ValueError(f"sell_quantity must be > 0, got {sell_quantity}")
        if not current_market_price > 0:
            raise ValueError(f"current_market_price must be > 0, got {current_market_price}")

        sale_dt = _parse_iso_date(sale_date, "sale_date") if sale_date else None

        symbol_upper = symbol.upper()
        matching_lots = [
            l for l in self.tax_lots if l.symbol.upper() == symbol_upper and l.quantity > 0
        ]
        if not matching_lots:
            raise ValueError(f"No available tax lots for symbol {symbol}")

        available = sum(l.quantity for l in matching_lots)
        if sell_quantity > available:
            raise ValueError(
                f"Insufficient open tax lot quantity for {symbol_upper}: "
                f"requested {sell_quantity}, available {available}"
            )

        sorted_lots = self._sorted_lots(matching_lots, method_clean)

        warnings: List[str] = []
        pool = self._replacement_pool(symbol_upper)
        remaining_replacement = float("inf") if pool is None else pool
        if pool is None:
            warnings.append(
                "At least one in-window replacement purchase has an unknown "
                "quantity; the disallowance is a conservative upper bound."
            )

        remaining_qty = sell_quantity
        executed_lots: List[SelectedTaxLotExecution] = []
        total_pnl = 0.0
        st_pnl = 0.0
        lt_pnl = 0.0
        total_disallowed = 0.0

        for lot in sorted_lots:
            if remaining_qty <= 0:
                break

            qty_to_take = min(remaining_qty, lot.quantity)
            pnl_per_share = current_market_price - lot.cost_basis_per_share
            lot_pnl = qty_to_take * pnl_per_share

            if sale_dt is not None:
                lot_acquired = _parse_iso_date(
                    lot.acquisition_date, f"Lot {lot.lot_id}: acquisition_date"
                )
                if sale_dt < lot_acquired:
                    raise ValueError(
                        f"Lot {lot.lot_id}: sale_date {sale_dt.isoformat()} precedes "
                        f"acquisition_date {lot_acquired.isoformat()}"
                    )
                is_lt = is_long_term(lot_acquired, sale_dt)
            else:
                is_lt = lot.days_held > 365

            # IRC 1091(b): the disallowance is limited to the number of
            # replacement shares acquired inside the window, matched share for
            # share against the loss shares.
            matched_qty = 0.0
            disallowed = 0.0
            if lot_pnl < 0 and remaining_replacement > 0:
                matched_qty = min(qty_to_take, remaining_replacement)
                disallowed = matched_qty * abs(pnl_per_share)
                remaining_replacement -= matched_qty
                total_disallowed += disallowed
                logger.warning(
                    f"WASH SALE INTERCEPTED on {symbol_upper} Lot {lot.lot_id}: "
                    f"{matched_qty:,.2f} of {qty_to_take:,.2f} loss shares matched to "
                    f"replacement purchases; ${disallowed:,.2f} loss disallowed. "
                    f"Basis adjustment under IRC 1091(d) is not applied here - see "
                    f"the wash-sale-rule-tracking-us skill."
                )

            executed_lots.append(
                SelectedTaxLotExecution(
                    lot_id=lot.lot_id,
                    strategy_id=lot.strategy_id,
                    shares_sold=qty_to_take,
                    cost_basis_per_share=lot.cost_basis_per_share,
                    execution_price=current_market_price,
                    realized_gain_loss_usd=round(lot_pnl, 2),
                    is_long_term=is_lt,
                    is_wash_sale_triggered=matched_qty > 0,
                    wash_sale_matched_quantity=matched_qty,
                    disallowed_loss_usd=round(disallowed, 2),
                )
            )

            if not dry_run:
                lot.quantity -= qty_to_take
                if abs(lot.quantity) < QUANTITY_EPSILON:
                    lot.quantity = 0.0
            remaining_qty -= qty_to_take
            if abs(remaining_qty) < QUANTITY_EPSILON:
                remaining_qty = 0.0
            total_pnl += lot_pnl

            if is_lt:
                lt_pnl += lot_pnl
            else:
                st_pnl += lot_pnl

        if method_clean != "FIFO":
            warnings.append(
                "Non-FIFO selection requires adequate identification under Treas. "
                "Reg. 1.1012-1(c)(8) no later than the earlier of settlement date or "
                "the Rule 15c6-1 settlement time (T+1 since 2024-05-28); absent that, "
                "FIFO applies by default under Treas. Reg. 1.1012-1(c)(1)(i)."
            )

        return TaxOptimizationResult(
            symbol=symbol_upper,
            requested_sell_quantity=sell_quantity,
            total_realized_gain_loss_usd=round(total_pnl, 2),
            short_term_realized_usd=round(st_pnl, 2),
            long_term_realized_usd=round(lt_pnl, 2),
            executed_lots=executed_lots,
            wash_sale_warning=total_disallowed > 0,
            method=method_clean,
            total_disallowed_loss_usd=round(total_disallowed, 2),
            net_deductible_gain_loss_usd=round(total_pnl + total_disallowed, 2),
            warnings=warnings,
        )

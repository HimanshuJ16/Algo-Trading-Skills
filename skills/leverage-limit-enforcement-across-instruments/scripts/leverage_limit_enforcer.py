"""
leverage-limit-enforcement-across-instruments: pre-trade leverage gateway.

Projects the gross, net-directional, and per-asset-class leverage a portfolio
would carry *if* a proposed order were filled, and vetoes the order when a
configured cap would be breached.

Exposure convention
-------------------
Exposures are netted per instrument and summed gross across instruments,
mirroring the AIFMD gross-method shape (Commission Delegated Regulation (EU)
No 231/2013, Art. 7: "the sum of the absolute values of all positions", with
derivatives converted to an equivalent underlying position per Annex II).
Two rows for the same symbol are one position, so a closing order reduces
exposure instead of adding a second leg.

Options and other non-linear instruments are supplied under the asset class of
their *underlying*, with ``notional_usd`` set to the underlying notional
(contracts x contract size x underlying price, never the premium paid) and
``exposure_delta`` set to the option delta -- the Annex II conversion.

Fail-closed design
-------------------
Every input is validated before any ratio is computed: a malformed side, a
non-finite notional, or an asset class with no configured cap raises or
rejects rather than being silently coerced. Limit comparisons are made on
unrounded ratios; rounding is applied only to the reported figures.

De-risking is never blocked. An order that does not increase gross, net, or
its own asset-class leverage is approved even when the book is already over a
cap, so a breach can always be remediated.

Thread safety: the engine holds configuration only and is safe to share across
threads. It cannot protect the caller's own position state -- serialize
check-then-place sequences yourself when orders arrive concurrently.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Accepted ``side`` values. Anything else raises rather than defaulting to a
#: short, because a mislabelled side silently inverts the net-leverage gate.
VALID_SIDES = frozenset({"BUY", "SELL"})

#: Relative slack applied to limit comparisons so a position sitting *exactly*
#: on a cap is not rejected by float representation error. Deliberately tiny:
#: rounding ratios to 2dp before comparing would admit real breaches of up to
#: 0.005x NAV (on a $1bn book, $5m of unauthorised notional).
LIMIT_RELATIVE_TOLERANCE = 1e-9

STATUS_APPROVED = "ORDER_LEVERAGE_APPROVED"
STATUS_APPROVED_RISK_REDUCING = "APPROVED_RISK_REDUCING_WHILE_OVER_LIMIT"
STATUS_REJECTED_GROSS = "REJECTED_GROSS_LEVERAGE_BREACH"
STATUS_REJECTED_NET = "REJECTED_NET_LEVERAGE_BREACH"
STATUS_REJECTED_ASSET_CLASS = "REJECTED_ASSET_CLASS_LEVERAGE_BREACH"
STATUS_REJECTED_UNKNOWN_ASSET_CLASS = "REJECTED_UNKNOWN_ASSET_CLASS"


def _validate_symbol(symbol: str, context: str) -> str:
    """Normalise a symbol to stripped upper case.

    Netting keys off this value, so normalisation is deliberately lenient:
    failing to recognise ``btc-perp`` and ``BTC-PERP`` as one instrument would
    make a closing order look like a new opposing leg.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"{context}: symbol must be a non-empty string, got {symbol!r}")
    return symbol.strip().upper()


def _validate_side(side: str, context: str) -> str:
    if not isinstance(side, str):
        raise ValueError(f"{context}: side must be a string, got {side!r}")
    normalised = side.strip().upper()
    if normalised not in VALID_SIDES:
        raise ValueError(
            f"{context}: side must be one of {sorted(VALID_SIDES)}, got {side!r}. "
            "Aliases such as 'LONG'/'SHORT' are rejected rather than guessed, "
            "because an unrecognised side would be counted as a short and "
            "invert the net-leverage measurement."
        )
    return normalised


def _validate_asset_class(asset_class: str, context: str) -> str:
    if not isinstance(asset_class, str) or not asset_class.strip():
        raise ValueError(
            f"{context}: asset_class must be a non-empty string, got {asset_class!r}"
        )
    return asset_class.strip().upper()


def _validate_notional(notional: float, context: str) -> float:
    if isinstance(notional, bool) or not isinstance(notional, (int, float)):
        raise ValueError(f"{context}: notional must be a number, got {notional!r}")
    value = float(notional)
    if not math.isfinite(value):
        raise ValueError(f"{context}: notional must be finite, got {notional!r}")
    if value < 0.0:
        raise ValueError(
            f"{context}: notional must be non-negative, got {notional!r}. "
            "Direction is carried by 'side' only; a negative notional would "
            "double-encode it and desynchronise the gross and net measures."
        )
    return value


def _validate_delta(delta: float, context: str) -> float:
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        raise ValueError(f"{context}: exposure_delta must be a number, got {delta!r}")
    value = float(delta)
    if not math.isfinite(value):
        raise ValueError(f"{context}: exposure_delta must be finite, got {delta!r}")
    return value


def _validate_limit(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    limit = float(value)
    if not math.isfinite(limit) or limit <= 0.0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    return limit


def _within_limit(ratio: float, limit: float) -> bool:
    """Compare an *unrounded* leverage ratio against an inclusive cap."""
    return ratio <= limit * (1.0 + LIMIT_RELATIVE_TOLERANCE)


@dataclass
class PositionSpec:
    """One open position.

    ``notional_usd`` is the absolute exposure of the instrument in USD; for
    derivatives it is the underlying notional, not the premium or the margin
    posted. ``side`` carries the direction of the position in the instrument
    and ``exposure_delta`` the sensitivity of that instrument to its
    underlying, so the signed underlying exposure is
    ``(+1 if BUY else -1) * notional_usd * exposure_delta``. A long put is
    therefore ``side='BUY'`` with a negative delta, which correctly resolves to
    short underlying exposure.
    """

    symbol: str                         # e.g. 'BTC-PERP', 'AAPL', 'EURUSD'
    asset_class: str                    # 'EQUITY', 'CRYPTO', 'FX', 'FUTURES'
    side: str                           # 'BUY' (Long) or 'SELL' (Short)
    notional_usd: float                 # Absolute underlying notional, USD
    exposure_delta: float = 1.0         # 1.0 for linear instruments; option delta otherwise


@dataclass
class ProposedOrderSpec:
    """A single proposed order awaiting the pre-trade leverage decision."""

    symbol: str
    asset_class: str                    # 'EQUITY', 'CRYPTO', 'FX', 'FUTURES'
    side: str                           # 'BUY' or 'SELL'
    order_notional_usd: float           # Absolute underlying notional, USD
    exposure_delta: float = 1.0


@dataclass
class LeverageEnforcementReport:
    current_gross_leverage: float
    current_net_leverage: float
    projected_gross_leverage: float
    projected_net_leverage: float
    projected_asset_class_leverage: float
    is_gross_limit_passed: bool
    is_net_limit_passed: bool
    is_asset_class_limit_passed: bool
    status: str                         # one of the STATUS_* constants
    audit_notes: str
    order_asset_class: str = ""
    order_asset_class_limit: float = 0.0
    current_asset_class_leverage: float = 0.0
    projected_asset_class_leverages: Dict[str, float] = field(default_factory=dict)
    is_risk_reducing: bool = False

    @property
    def is_approved(self) -> bool:
        """True when the order may be routed."""
        return self.status in (STATUS_APPROVED, STATUS_APPROVED_RISK_REDUCING)


class LeverageLimitEnforcerEngine:
    """
    Pre-trade risk gateway enforcing global Gross Leverage, Net Directional
    Leverage, and asset-class specific leverage caps across multi-instrument
    portfolios (Equities, Crypto, FX, Futures).

    Net leverage is bounded above by gross leverage by the triangle
    inequality, so a ``max_net_leverage`` at or above ``max_gross_leverage``
    is a gate that can never bind; the constructor warns rather than silently
    accepting a dead control.

    Asset classes with no configured cap are rejected by default. Set
    ``default_asset_class_limit`` to apply an explicit fallback instead --
    never leave the fallback implicit, or an instrument type nobody configured
    trades against a limit nobody chose.
    """

    def __init__(
        self,
        max_gross_leverage: float = 3.0,
        max_net_leverage: float = 1.5,
        asset_class_limits: Optional[Dict[str, float]] = None,
        default_asset_class_limit: Optional[float] = None,
    ):
        self.max_gross_leverage = _validate_limit(max_gross_leverage, "max_gross_leverage")
        self.max_net_leverage = _validate_limit(max_net_leverage, "max_net_leverage")

        if self.max_net_leverage >= self.max_gross_leverage:
            logger.warning(
                "max_net_leverage (%.2fx) >= max_gross_leverage (%.2fx): net leverage "
                "never exceeds gross leverage, so the net gate can never bind.",
                self.max_net_leverage,
                self.max_gross_leverage,
            )

        raw_limits = asset_class_limits if asset_class_limits is not None else {
            # House-policy defaults, not regulatory prescriptions. See
            # references/standards.md for what each is anchored to and for the
            # jurisdictions where a tighter cap is mandatory.
            "EQUITY": 2.0,      # Reg T initial margin, 12 CFR 220.12(a): 50% -> 2:1
            "CRYPTO": 3.0,      # Volatility-based house cap
            "FX": 10.0,         # House cap for major pairs
            "FUTURES": 5.0,     # House cap; exchange SPAN margin is the binding constraint
        }
        if not isinstance(raw_limits, dict) or not raw_limits:
            raise ValueError(
                f"asset_class_limits must be a non-empty dict, got {asset_class_limits!r}"
            )
        self.asset_class_limits: Dict[str, float] = {
            _validate_asset_class(ac, "asset_class_limits"): _validate_limit(
                limit, f"asset_class_limits[{ac!r}]"
            )
            for ac, limit in raw_limits.items()
        }

        self.default_asset_class_limit: Optional[float] = (
            None
            if default_asset_class_limit is None
            else _validate_limit(default_asset_class_limit, "default_asset_class_limit")
        )

    # ------------------------------------------------------------------
    # Exposure aggregation
    # ------------------------------------------------------------------
    def _signed_exposures(
        self, positions: List[PositionSpec], context: str
    ) -> Dict[str, Tuple[str, float]]:
        """Map each normalised symbol to ``(asset_class, signed_exposure_usd)``.

        Rows sharing a symbol are netted, because a book holds one position per
        instrument; treating a closing order as a second opposing leg is what
        makes a naive gate veto de-risking.
        """
        if isinstance(positions, (str, bytes)) or not isinstance(positions, (list, tuple)):
            raise ValueError(
                f"{context} must be a list of PositionSpec, got "
                f"{type(positions).__name__}"
            )
        exposures: Dict[str, Tuple[str, float]] = {}
        for index, position in enumerate(positions):
            if not isinstance(position, PositionSpec):
                raise ValueError(
                    f"{context}[{index}]: expected PositionSpec, got {type(position).__name__}"
                )
            row = f"{context}[{index}]"
            symbol = _validate_symbol(position.symbol, row)
            asset_class = _validate_asset_class(position.asset_class, row)
            side = _validate_side(position.side, row)
            notional = _validate_notional(position.notional_usd, row)
            delta = _validate_delta(position.exposure_delta, row)

            signed = (notional if side == "BUY" else -notional) * delta

            if symbol in exposures:
                existing_class, existing_signed = exposures[symbol]
                if existing_class != asset_class:
                    raise ValueError(
                        f"{row}: symbol {symbol!r} is reported under two asset classes "
                        f"({existing_class!r} and {asset_class!r}); netting and "
                        "asset-class caps cannot both be resolved."
                    )
                exposures[symbol] = (existing_class, existing_signed + signed)
            else:
                exposures[symbol] = (asset_class, signed)
        return exposures

    @staticmethod
    def _aggregate(
        exposures: Dict[str, Tuple[str, float]]
    ) -> Tuple[float, float, Dict[str, float]]:
        # math.fsum, not sum: the limit comparison runs at a 1e-9 relative
        # tolerance, and naive accumulation over a large book drifts by more
        # than that. Exact summation costs nothing at this scale.
        gross_notional = math.fsum(abs(signed) for _, signed in exposures.values())
        net_notional = abs(math.fsum(signed for _, signed in exposures.values()))
        by_class: Dict[str, List[float]] = {}
        for asset_class, signed in exposures.values():
            by_class.setdefault(asset_class, []).append(abs(signed))
        asset_notionals = {ac: math.fsum(values) for ac, values in by_class.items()}
        return gross_notional, net_notional, asset_notionals

    def compute_exposures(
        self, positions: List[PositionSpec]
    ) -> Tuple[float, float, Dict[str, float]]:
        """Return ``(gross_notional, net_notional, per_asset_class_gross)`` in USD.

        Positions are netted per symbol first, then summed gross across
        symbols. Longs and shorts in *different* instruments are deliberately
        not netted into the gross figure: that offset assumes a correlation
        hedge that fails precisely in the stress event the cap exists for.
        """
        return self._aggregate(self._signed_exposures(positions, "positions"))

    # ------------------------------------------------------------------
    # Pre-trade decision
    # ------------------------------------------------------------------
    def audit_proposed_order(
        self,
        portfolio_equity_usd: float,
        current_positions: List[PositionSpec],
        proposed_order: ProposedOrderSpec,
    ) -> LeverageEnforcementReport:
        """
        Project gross, net, and per-asset-class leverage with the proposed order
        filled, and approve or veto it against the configured caps.
        """
        if isinstance(portfolio_equity_usd, bool) or not isinstance(
            portfolio_equity_usd, (int, float)
        ):
            raise ValueError(
                f"portfolio_equity_usd must be a number, got {portfolio_equity_usd!r}"
            )
        equity = float(portfolio_equity_usd)
        if not math.isfinite(equity):
            raise ValueError(
                f"portfolio_equity_usd must be finite, got {portfolio_equity_usd!r}. "
                "A NaN equity would propagate into every ratio and produce a "
                "report whose numbers cannot be audited."
            )
        if equity <= 0.0:
            raise ValueError(
                f"Portfolio Equity (${equity:,.2f}) must be strictly positive."
            )
        if not isinstance(proposed_order, ProposedOrderSpec):
            raise ValueError(
                f"proposed_order must be a ProposedOrderSpec, got "
                f"{type(proposed_order).__name__}"
            )

        order_symbol = _validate_symbol(proposed_order.symbol, "proposed_order")
        order_class = _validate_asset_class(proposed_order.asset_class, "proposed_order")
        order_side = _validate_side(proposed_order.side, "proposed_order")
        order_notional = _validate_notional(
            proposed_order.order_notional_usd, "proposed_order"
        )
        order_delta = _validate_delta(proposed_order.exposure_delta, "proposed_order")

        # 1. Current exposures (netted per symbol).
        current_exposures = self._signed_exposures(current_positions, "current_positions")
        cur_gross, cur_net, cur_class_notionals = self._aggregate(current_exposures)
        cur_gross_lev = cur_gross / equity
        cur_net_lev = cur_net / equity
        cur_ac_lev = cur_class_notionals.get(order_class, 0.0) / equity

        # 2. Project the book with the order filled, netting against any
        #    existing position in the same symbol.
        projected_exposures = dict(current_exposures)
        order_signed = (
            order_notional if order_side == "BUY" else -order_notional
        ) * order_delta
        if order_symbol in projected_exposures:
            held_class, held_signed = projected_exposures[order_symbol]
            if held_class != order_class:
                raise ValueError(
                    f"proposed_order: symbol {order_symbol!r} is held as {held_class!r} "
                    f"but the order declares {order_class!r}; refusing to net across "
                    "asset classes."
                )
            projected_exposures[order_symbol] = (held_class, held_signed + order_signed)
        else:
            projected_exposures[order_symbol] = (order_class, order_signed)

        proj_gross, proj_net, proj_class_notionals = self._aggregate(projected_exposures)
        proj_gross_lev = proj_gross / equity
        proj_net_lev = proj_net / equity
        proj_ac_lev = proj_class_notionals.get(order_class, 0.0) / equity
        proj_class_levs = {
            ac: notional / equity for ac, notional in proj_class_notionals.items()
        }

        # 3. Resolve the applicable asset-class cap. Fail closed when the class
        #    was never configured and no explicit fallback was supplied: an
        #    unenforceable cap is treated as a failed cap, not an absent one.
        ac_max_limit = self.asset_class_limits.get(order_class)
        if ac_max_limit is None and self.default_asset_class_limit is not None:
            ac_max_limit = self.default_asset_class_limit
            logger.warning(
                "No asset-class cap configured for %r; applying "
                "default_asset_class_limit=%.2fx.",
                order_class,
                ac_max_limit,
            )

        # 4. Evaluate the caps on UNROUNDED ratios.
        is_gross_ok = _within_limit(proj_gross_lev, self.max_gross_leverage)
        is_net_ok = _within_limit(proj_net_lev, self.max_net_leverage)
        is_ac_ok = ac_max_limit is not None and _within_limit(proj_ac_lev, ac_max_limit)

        # An order is treated as de-risking only when it raises none of the
        # three measures AND strictly lowers every measure that is currently
        # breached. Such an order is approved even though the book is over a
        # cap -- otherwise the gate locks the desk out of the only orders that
        # can cure the breach. The "strictly lowers" half matters: reversing a
        # position to the same size leaves every ratio unchanged, which is a
        # large new trade, not remediation.
        upper = 1.0 + LIMIT_RELATIVE_TOLERANCE
        lower = 1.0 - LIMIT_RELATIVE_TOLERANCE
        measures = (
            (proj_gross_lev, cur_gross_lev, is_gross_ok),
            (proj_net_lev, cur_net_lev, is_net_ok),
            (proj_ac_lev, cur_ac_lev, is_ac_ok),
        )
        is_risk_reducing = all(
            projected <= current * upper for projected, current, _ in measures
        ) and all(
            projected < current * lower for projected, current, ok in measures if not ok
        )

        if is_gross_ok and is_net_ok and is_ac_ok:
            status = STATUS_APPROVED
            notes = (
                f"LEVERAGE APPROVED [{order_symbol}]: Projected Gross Lev = {proj_gross_lev:.4f}x "
                f"(Limit {self.max_gross_leverage:.2f}x), Net Lev = {proj_net_lev:.4f}x "
                f"(Limit {self.max_net_leverage:.2f}x), {order_class} Lev = {proj_ac_lev:.4f}x "
                f"(Limit {ac_max_limit:.2f}x)."
            )
            logger.info(notes)
        elif is_risk_reducing:
            status = STATUS_APPROVED_RISK_REDUCING
            breached = ", ".join(
                name
                for name, ok in (
                    ("gross", is_gross_ok),
                    ("net", is_net_ok),
                    (order_class.lower(), is_ac_ok),
                )
                if not ok
            )
            notes = (
                f"LEVERAGE APPROVED (RISK-REDUCING) [{order_symbol}]: book remains over the "
                f"{breached} cap, but the order lowers gross {cur_gross_lev:.4f}x -> "
                f"{proj_gross_lev:.4f}x, net {cur_net_lev:.4f}x -> {proj_net_lev:.4f}x, "
                f"{order_class} {cur_ac_lev:.4f}x -> {proj_ac_lev:.4f}x. "
                "Remediation still required."
            )
            logger.warning(notes)
        elif not is_gross_ok:
            status = STATUS_REJECTED_GROSS
            notes = (
                f"LEVERAGE VETO [{order_symbol}]: Projected Gross Leverage {proj_gross_lev:.4f}x "
                f"exceeds max limit ({self.max_gross_leverage:.2f}x)! Order rejected."
            )
            logger.warning(notes)
        elif not is_net_ok:
            status = STATUS_REJECTED_NET
            notes = (
                f"LEVERAGE VETO [{order_symbol}]: Projected Net Leverage {proj_net_lev:.4f}x "
                f"exceeds max limit ({self.max_net_leverage:.2f}x)! Order rejected."
            )
            logger.warning(notes)
        elif ac_max_limit is None:
            status = STATUS_REJECTED_UNKNOWN_ASSET_CLASS
            notes = (
                f"LEVERAGE VETO [{order_symbol} ({order_class})]: no leverage cap is "
                f"configured for asset class {order_class!r} and no "
                "default_asset_class_limit was set. Order rejected (fail-closed). "
                "Orders that reduce this exposure are still permitted."
            )
            logger.warning(notes)
        else:
            status = STATUS_REJECTED_ASSET_CLASS
            notes = (
                f"LEVERAGE VETO [{order_symbol} ({order_class})]: Projected {order_class} "
                f"Leverage {proj_ac_lev:.4f}x exceeds asset class limit "
                f"({ac_max_limit:.2f}x)! Order rejected."
            )
            logger.warning(notes)

        # Caps on classes the order does not touch are unchanged by this order,
        # so they do not veto it -- but a pre-existing breach elsewhere is
        # surfaced rather than left silent.
        for other_class, other_lev in proj_class_levs.items():
            if other_class == order_class:
                continue
            other_limit = self.asset_class_limits.get(
                other_class, self.default_asset_class_limit
            )
            if other_limit is not None and not _within_limit(other_lev, other_limit):
                logger.warning(
                    "Asset class %s is over its cap at %.4fx (limit %.2fx); this order "
                    "does not change it, but the breach needs remediation.",
                    other_class,
                    other_lev,
                    other_limit,
                )

        return LeverageEnforcementReport(
            current_gross_leverage=round(cur_gross_lev, 6),
            current_net_leverage=round(cur_net_lev, 6),
            projected_gross_leverage=round(proj_gross_lev, 6),
            projected_net_leverage=round(proj_net_lev, 6),
            projected_asset_class_leverage=round(proj_ac_lev, 6),
            is_gross_limit_passed=is_gross_ok,
            is_net_limit_passed=is_net_ok,
            is_asset_class_limit_passed=is_ac_ok,
            status=status,
            audit_notes=notes,
            order_asset_class=order_class,
            # 0.0 means "no cap could be applied", never "a cap of zero".
            order_asset_class_limit=0.0 if ac_max_limit is None else ac_max_limit,
            current_asset_class_leverage=round(cur_ac_lev, 6),
            projected_asset_class_leverages={
                ac: round(lev, 6) for ac, lev in proj_class_levs.items()
            },
            is_risk_reducing=is_risk_reducing,
        )

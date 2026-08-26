"""MetaTrader 5 (MT5) Python bridge: MqlTradeRequest construction and TRADE_RETCODE triage.

This module owns exactly one operation: submitting a **market deal**
(``TRADE_ACTION_DEAL``) to an MT5 trade server through an injected terminal
adapter, and classifying the ``MqlTradeResult.retcode`` that comes back.

It deliberately does **not** import the ``MetaTrader5`` package. That package
ships ``win_amd64`` wheels only and requires a running, logged-in terminal, so
importing it would make this module unusable (and untestable) anywhere else.
The terminal is supplied as an adapter satisfying :class:`MT5TerminalAdapter`;
in production that adapter is a thin wrapper over ``MetaTrader5`` itself.

The engine is deliberately **single-shot**: it never retries. MT5's
``order_send()`` carries no client-assigned order id, so a lost response is an
unresolvable ambiguity from the client's side. When that happens the engine
returns ``requires_reconciliation=True`` and leaves the retry decision - which
must be preceded by a ``history_deals_get`` / ``positions_get`` lookup on the
magic number - to the caller.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# --- MQL5 trade constants (ENUM_TRADE_REQUEST_ACTIONS / ENUM_ORDER_TYPE) ----
TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1

# ENUM_ORDER_TYPE_FILLING. Only FOK and IOC are candidates for a market deal:
# ORDER_FILLING_BOC applies to limit/stop-limit orders only, and
# ORDER_FILLING_RETURN is disabled under Market Execution. The numeric values
# of BOC/RETURN differ across MQL5 builds, so they are not defined here.
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1

# SYMBOL_FILLING_MODE bitmask flags. NOTE: these use a *different* numbering
# from ENUM_ORDER_TYPE_FILLING above - the bitmask advertises what the symbol
# permits, the enum names what the request asks for. Conflating the two is the
# usual cause of retcode 10030 ("Unsupported filling mode").
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
SYMBOL_FILLING_BOC = 4

# --- ENUM_TRADE_RETURN_CODES (the subset this module reasons about) --------
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_CANCEL = 10007
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_ERROR = 10011
TRADE_RETCODE_TIMEOUT = 10012
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_TRADE_DISABLED = 10017
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021
TRADE_RETCODE_TOO_MANY_REQUESTS = 10024
TRADE_RETCODE_SERVER_DISABLES_AT = 10026
TRADE_RETCODE_CLIENT_DISABLES_AT = 10027
TRADE_RETCODE_LOCKED = 10028
TRADE_RETCODE_FROZEN = 10029
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_CONNECTION = 10031
TRADE_RETCODE_LIMIT_VOLUME = 10034

#: Retained for backward compatibility. The official MQL5 name for 10013 is
#: ``TRADE_RETCODE_INVALID`` ("Invalid request"); this alias predates that
#: correction and is kept so existing imports keep working.
TRADE_RETCODE_INVALID_REQUEST = TRADE_RETCODE_INVALID

# --- Retry disposition -----------------------------------------------------
#: Order reached the server and the outcome is fully known.
DISPOSITION_COMPLETE = "COMPLETE"
#: Server rejected the request on its merits. Resending the *same* request will
#: be rejected again; change the request or stand down.
DISPOSITION_TERMINAL = "TERMINAL"
#: Server explicitly refused *this* attempt for a transient market reason and
#: nothing was filled. Safe to re-quote and resend under a bounded attempt cap.
DISPOSITION_RETRYABLE = "RETRYABLE"
#: The outcome is unknown to the client. NEVER resend without first
#: reconciling against the trade server.
DISPOSITION_AMBIGUOUS = "AMBIGUOUS"
#: Rejected locally by this module; nothing was ever sent.
DISPOSITION_NOT_SENT = "NOT_SENT"

_AMBIGUOUS_RETCODES = frozenset({
    TRADE_RETCODE_ERROR,       # 10011 request processing error
    TRADE_RETCODE_TIMEOUT,     # 10012 request canceled by timeout
    TRADE_RETCODE_LOCKED,      # 10028 request locked for processing
    TRADE_RETCODE_CONNECTION,  # 10031 no connection with the trade server
})

_RETRYABLE_RETCODES = frozenset({
    TRADE_RETCODE_REQUOTE,            # 10004
    TRADE_RETCODE_PRICE_CHANGED,      # 10020
    TRADE_RETCODE_PRICE_OFF,          # 10021
    TRADE_RETCODE_TOO_MANY_REQUESTS,  # 10024
})


class MT5BridgeError(Exception):
    """Raised for configuration faults that must never reach a trade server."""


@runtime_checkable
class MT5TerminalAdapter(Protocol):
    """Minimal surface this engine needs from a live MT5 terminal.

    Mirrors the ``MetaTrader5`` package: both calls return ``None`` on error
    (the caller is expected to consult ``last_error()``).
    """

    def order_send(self, request: Dict[str, Any]) -> Any:
        """Submit an ``MqlTradeRequest`` dict; return an ``MqlTradeResult`` or ``None``."""

    def symbol_info(self, symbol: str) -> Any:
        """Return the symbol's specification namedtuple, or ``None`` if unknown."""


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a namedtuple-like object or a plain dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _step_decimals(step: float) -> int:
    """Decimal places implied by a volume step (0.01 -> 2, 0.001 -> 3)."""
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


@dataclass
class MT5Config:
    """Terminal/session configuration for a single strategy identity.

    ``password`` is excluded from ``repr`` so it cannot leak into logs or
    tracebacks that render the dataclass.
    """

    login: int
    password: str = field(repr=False)
    server: str
    path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"
    max_slippage_points: int = 10
    magic_number: int = 234000
    preferred_filling: str = "IOC"

    def __post_init__(self) -> None:
        if self.login <= 0:
            raise MT5BridgeError("MT5Config.login must be a positive account number.")
        if not self.server:
            raise MT5BridgeError("MT5Config.server must name the broker's trade server.")
        if self.max_slippage_points < 0:
            raise MT5BridgeError("MT5Config.max_slippage_points must be >= 0 points.")
        self.preferred_filling = str(self.preferred_filling).upper()
        if self.preferred_filling not in ("IOC", "FOK"):
            raise MT5BridgeError(
                "MT5Config.preferred_filling must be 'IOC' or 'FOK' - a market deal "
                "(TRADE_ACTION_DEAL) supports no other ENUM_ORDER_TYPE_FILLING value."
            )
        if self.magic_number <= 0:
            # magic 0 is indistinguishable from a manually placed trade, which
            # makes the post-timeout reconciliation described in the module
            # docstring impossible to scope to this strategy.
            raise MT5BridgeError(
                "MT5Config.magic_number must be a positive, strategy-unique id: it is "
                "the only tag available for reconciling an ambiguous order_send()."
            )


@dataclass(frozen=True)
class MT5SymbolSpec:
    """Broker-published trading conditions for one symbol.

    Every field maps to a property returned by ``MetaTrader5.symbol_info()``.
    None of them has a safe default, so they must come from the terminal - a
    hard-coded 0.01 lot step is wrong for micro accounts (0.001) and for many
    index/metal CFDs (0.1 or 1.0).
    """

    symbol: str
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int
    point: float
    trade_stops_level: int = 0
    trade_freeze_level: int = 0
    filling_mode: int = 0
    volume_limit: float = 0.0

    @classmethod
    def from_symbol_info(cls, symbol: str, info: Any) -> "MT5SymbolSpec":
        """Build a spec from a ``symbol_info()`` namedtuple (or an equivalent dict)."""
        return cls(
            symbol=str(_attr(info, "name", symbol) or symbol),
            volume_min=float(_attr(info, "volume_min", 0.0)),
            volume_max=float(_attr(info, "volume_max", 0.0)),
            volume_step=float(_attr(info, "volume_step", 0.0)),
            digits=int(_attr(info, "digits", 5)),
            point=float(_attr(info, "point", 0.0)),
            trade_stops_level=int(_attr(info, "trade_stops_level", 0)),
            trade_freeze_level=int(_attr(info, "trade_freeze_level", 0)),
            filling_mode=int(_attr(info, "filling_mode", 0)),
            volume_limit=float(_attr(info, "volume_limit", 0.0)),
        )


@dataclass
class MT5OrderRequest:
    """A market-deal intent, before broker-side normalisation."""

    symbol: str                          # e.g. 'EURUSD', 'GBPUSD'
    order_type: str                      # 'BUY' or 'SELL' (market deal only)
    volume_lots: float                   # e.g. 0.1 lots
    price: float                         # current quote to deal at: Ask for BUY, Bid for SELL
    sl_price: Optional[float] = None     # Stop Loss price; None or 0.0 means "no stop"
    tp_price: Optional[float] = None     # Take Profit price; None or 0.0 means "no target"
    comment: str = "Python_Algo_Bot"


@dataclass
class MT5OrderReport:
    """Outcome of one ``order_send()`` attempt.

    ``is_executed`` is True only when volume actually traded (retcode 10009 or
    10010). Read ``requires_reconciliation`` before deciding anything about a
    resend: ``is_executed is False`` does **not** mean nothing happened.
    """

    order_id: int
    symbol: str
    order_type: str
    volume_lots: float
    execution_price: float
    retcode: int
    mql_trade_request: Dict[str, Any]
    is_executed: bool
    status: str
    audit_notes: str
    deal_id: int = 0
    filled_volume_lots: float = 0.0
    retry_disposition: str = DISPOSITION_NOT_SENT
    requires_reconciliation: bool = False
    broker_comment: str = ""


def classify_retcode(retcode: int) -> str:
    """Map an ``MqlTradeResult.retcode`` to a retry disposition.

    The default for an unrecognised code is :data:`DISPOSITION_TERMINAL`, not
    "retryable": an unknown server response is never a licence to resend a
    non-idempotent order.
    """
    if retcode in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_PLACED):
        return DISPOSITION_COMPLETE
    if retcode in _AMBIGUOUS_RETCODES:
        return DISPOSITION_AMBIGUOUS
    if retcode in _RETRYABLE_RETCODES:
        return DISPOSITION_RETRYABLE
    return DISPOSITION_TERMINAL


def resolve_filling_mode(spec: MT5SymbolSpec, preferred: str = "IOC") -> Optional[int]:
    """Pick an ``ENUM_ORDER_TYPE_FILLING`` value the symbol actually permits.

    Returns ``None`` when the symbol advertises neither FOK nor IOC, in which
    case a ``TRADE_ACTION_DEAL`` cannot be filled and must not be sent.

    A ``filling_mode`` of 0 means the terminal reported no flags; the preferred
    mode is then returned unverified and the caller is warned.
    """
    preferred = preferred.upper()
    modes = {
        "IOC": (SYMBOL_FILLING_IOC, ORDER_FILLING_IOC),
        "FOK": (SYMBOL_FILLING_FOK, ORDER_FILLING_FOK),
    }
    if preferred not in modes:
        raise MT5BridgeError(
            f"preferred filling must be 'IOC' or 'FOK', got {preferred!r}; a market deal "
            "supports no other ENUM_ORDER_TYPE_FILLING value."
        )
    if spec.filling_mode == 0:
        logger.warning(
            "MT5 FILLING UNVERIFIED [%s]: symbol reported SYMBOL_FILLING_MODE=0; "
            "sending preferred %s unchecked. Expect retcode 10030 if unsupported.",
            spec.symbol, preferred,
        )
        return modes[preferred][1]
    fallback = "FOK" if preferred == "IOC" else "IOC"
    for name in (preferred, fallback):
        symbol_flag, order_filling = modes[name]
        if spec.filling_mode & symbol_flag:
            return order_filling
    return None


class MT5PythonBridgeEngine:
    """Validates, serialises and submits a single MT5 market deal.

    Construction requires either a terminal adapter or an explicit
    ``dry_run=True``. There is no third mode: an engine with neither would have
    to fabricate a result, and a fabricated ``TRADE_RETCODE_DONE`` is
    indistinguishable from a real fill to every caller downstream.
    """

    def __init__(
        self,
        config: MT5Config,
        mock_ipc_adapter: Optional[MT5TerminalAdapter] = None,
        dry_run: bool = False,
    ) -> None:
        if mock_ipc_adapter is None and not dry_run:
            raise MT5BridgeError(
                "MT5PythonBridgeEngine requires a terminal adapter, or dry_run=True to "
                "validate and serialise requests without submitting them. Refusing to "
                "run a simulated success path that a caller could mistake for a fill."
            )
        self.config = config
        self.ipc = mock_ipc_adapter
        self.dry_run = dry_run

    # -- symbol metadata ---------------------------------------------------
    def get_symbol_spec(self, symbol: str) -> Optional[MT5SymbolSpec]:
        """Fetch trading conditions from the terminal; ``None`` if unavailable."""
        if self.ipc is None:
            return None
        try:
            info = self.ipc.symbol_info(symbol)
        except Exception:  # adapter/IPC fault - treat as "metadata unavailable"
            logger.exception("MT5 SYMBOL LOOKUP FAILED [%s]", symbol)
            return None
        if info is None:
            return None
        return MT5SymbolSpec.from_symbol_info(symbol, info)

    # -- local rejection ---------------------------------------------------
    def _reject(
        self,
        order: MT5OrderRequest,
        retcode: int,
        status: str,
        notes: str,
    ) -> MT5OrderReport:
        logger.error(notes)
        return MT5OrderReport(
            order_id=0,
            symbol=order.symbol,
            order_type=order.order_type,
            volume_lots=order.volume_lots,
            execution_price=0.0,
            retcode=retcode,
            mql_trade_request={},
            is_executed=False,
            status=status,
            audit_notes=notes,
            retry_disposition=DISPOSITION_NOT_SENT,
            requires_reconciliation=False,
        )

    # -- validation --------------------------------------------------------
    def _validate_volume(
        self, order: MT5OrderRequest, spec: MT5SymbolSpec
    ) -> Optional[MT5OrderReport]:
        """Check volume against the broker's own min / max / step / limit."""
        vol = order.volume_lots
        if not isinstance(vol, (int, float)) or isinstance(vol, bool) or not math.isfinite(vol):
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: volume must be a finite number, got {vol!r}.",
            )
        if vol <= 0:
            # Guards the degenerate case where a malformed spec reports
            # volume_min == 0 and would let a zero-lot request through.
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: volume must be positive, got {vol}.",
            )
        if spec.volume_step <= 0:
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: broker reported volume_step="
                f"{spec.volume_step}; lot sizing cannot be validated.",
            )
        if vol < spec.volume_min:
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: {vol} lots is below the symbol's "
                f"volume_min of {spec.volume_min}.",
            )
        if spec.volume_max > 0 and vol > spec.volume_max:
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: {vol} lots exceeds the symbol's "
                f"volume_max of {spec.volume_max}.",
            )
        if spec.volume_limit > 0 and vol > spec.volume_limit:
            return self._reject(
                order, TRADE_RETCODE_LIMIT_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 VOLUME LIMIT [{order.symbol}]: {vol} lots exceeds the symbol's "
                f"volume_limit of {spec.volume_limit} for one direction.",
            )
        steps = round(vol / spec.volume_step)
        if abs(steps * spec.volume_step - vol) > spec.volume_step * 1e-6:
            return self._reject(
                order, TRADE_RETCODE_INVALID_VOLUME, "MT5_INVALID_VOLUME",
                f"MT5 INVALID VOLUME [{order.symbol}]: {vol} lots is not a multiple of the "
                f"symbol's volume_step of {spec.volume_step}.",
            )
        return None

    def _validate_stops(
        self,
        order: MT5OrderRequest,
        spec: MT5SymbolSpec,
        is_buy: bool,
    ) -> Optional[MT5OrderReport]:
        """Check SL/TP side and minimum indention (``SYMBOL_TRADE_STOPS_LEVEL``).

        The indention is measured against the request price. The server
        evaluates it against the live quote at arrival, so passing this check
        makes 10016 unlikely, not impossible.
        """
        side = "Buy" if is_buy else "Sell"
        checks = (
            ("SL", order.sl_price, "below" if is_buy else "above"),
            ("TP", order.tp_price, "above" if is_buy else "below"),
        )
        for label, level, direction in checks:
            if level is None or level == 0.0:
                continue  # 0.0 is MT5's "no level set"
            if not math.isfinite(level) or level < 0:
                return self._reject(
                    order, TRADE_RETCODE_INVALID_STOPS, "MT5_INVALID_STOPS",
                    f"MT5 INVALID STOPS [{order.symbol}]: {label} price {level!r} must be a "
                    "positive, finite price.",
                )
            correct_side = (level < order.price) if direction == "below" else (level > order.price)
            if not correct_side:
                return self._reject(
                    order, TRADE_RETCODE_INVALID_STOPS, "MT5_INVALID_STOPS",
                    f"MT5 INVALID STOPS [{order.symbol}]: {side} {label} ({level:.5f}) must be "
                    f"strictly {direction} the entry price ({order.price:.5f}).",
                )
            if spec.trade_stops_level > 0 and spec.point > 0:
                # Measure on the prices as they will actually be serialised, and
                # allow a sub-point tolerance: a stop placed exactly at the limit
                # lands a hair under it in IEEE-754 (1.08500 - 1.08480 is
                # 0.00019999999999997797) and must not be rejected for that.
                distance_points = (
                    abs(round(order.price, spec.digits) - round(level, spec.digits)) / spec.point
                )
                if distance_points < spec.trade_stops_level - 1e-6:
                    return self._reject(
                        order, TRADE_RETCODE_INVALID_STOPS, "MT5_INVALID_STOPS",
                        f"MT5 INVALID STOPS [{order.symbol}]: {side} {label} ({level:.5f}) is "
                        f"{distance_points:.1f} points from {order.price:.5f}, inside the symbol's "
                        f"trade_stops_level of {spec.trade_stops_level} points.",
                    )
        return None

    # -- submission --------------------------------------------------------
    def execute_forex_order(
        self,
        order: MT5OrderRequest,
        symbol_spec: Optional[MT5SymbolSpec] = None,
    ) -> MT5OrderReport:
        """Validate, serialise and submit one ``TRADE_ACTION_DEAL`` request.

        ``symbol_spec`` may be supplied directly (it is required in ``dry_run``
        mode); otherwise it is read from the terminal via ``symbol_info()``.
        Submission is attempted exactly once - see the module docstring on why
        this engine does not retry.
        """
        side_upper = str(order.order_type).strip().upper()
        if side_upper not in ("BUY", "SELL"):
            return self._reject(
                order, TRADE_RETCODE_INVALID, "MT5_INVALID_ORDER_TYPE",
                f"MT5 INVALID ORDER TYPE [{order.symbol}]: order_type {order.order_type!r} is not "
                "'BUY' or 'SELL'. This engine submits market deals only; pending-order types "
                "require TRADE_ACTION_PENDING and are out of scope.",
            )
        is_buy = side_upper == "BUY"

        if (
            not isinstance(order.price, (int, float))
            or isinstance(order.price, bool)
            or not math.isfinite(order.price)
            or order.price <= 0
        ):
            return self._reject(
                order, TRADE_RETCODE_INVALID_PRICE, "MT5_INVALID_PRICE",
                f"MT5 INVALID PRICE [{order.symbol}]: price must be a positive, finite quote, "
                f"got {order.price!r}.",
            )

        if symbol_spec is not None and symbol_spec.symbol != order.symbol:
            # The request is serialised with spec.symbol, so a mismatched spec
            # would silently route the deal to a different instrument.
            return self._reject(
                order, TRADE_RETCODE_INVALID, "MT5_SYMBOL_MISMATCH",
                f"MT5 SYMBOL MISMATCH: order names {order.symbol!r} but the supplied "
                f"symbol_spec describes {symbol_spec.symbol!r}. Refusing to trade a "
                "different instrument than the one requested.",
            )

        spec = symbol_spec if symbol_spec is not None else self.get_symbol_spec(order.symbol)
        if spec is None:
            return self._reject(
                order, TRADE_RETCODE_INVALID, "MT5_SYMBOL_UNAVAILABLE",
                f"MT5 SYMBOL UNAVAILABLE [{order.symbol}]: symbol_info() returned no "
                "specification. Confirm the exact broker symbol name and that it is selected "
                "in Market Watch (symbol_select) before trading it.",
            )

        volume_error = self._validate_volume(order, spec)
        if volume_error is not None:
            return volume_error

        stops_error = self._validate_stops(order, spec, is_buy)
        if stops_error is not None:
            return stops_error

        filling = resolve_filling_mode(spec, self.config.preferred_filling)
        if filling is None:
            return self._reject(
                order, TRADE_RETCODE_INVALID_FILL, "MT5_INVALID_FILLING",
                f"MT5 INVALID FILLING [{order.symbol}]: SYMBOL_FILLING_MODE={spec.filling_mode} "
                "permits neither FOK nor IOC, so a market deal cannot be filled on this symbol.",
            )

        if len(order.comment) > 31:
            logger.warning(
                "MT5 COMMENT LENGTH [%s]: comment is %d characters; MT5 order comments are "
                "short and the trade server may truncate or overwrite them. Never use the "
                "comment as a client order id.", order.symbol, len(order.comment),
            )

        digits = spec.digits
        steps = round(order.volume_lots / spec.volume_step)
        normalised_volume = round(steps * spec.volume_step, _step_decimals(spec.volume_step))

        mql_request: Dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "symbol": spec.symbol,
            "volume": float(normalised_volume),
            "type": ORDER_TYPE_BUY if is_buy else ORDER_TYPE_SELL,
            "price": round(float(order.price), digits),
            "sl": round(float(order.sl_price), digits) if order.sl_price else 0.0,
            "tp": round(float(order.tp_price), digits) if order.tp_price else 0.0,
            "deviation": self.config.max_slippage_points,
            "type_filling": filling,
            "magic": self.config.magic_number,
            "comment": order.comment,
        }

        if self.dry_run:
            notes = (
                f"MT5 DRY RUN [{spec.symbol}]: validated {side_upper} {normalised_volume} lots at "
                f"{mql_request['price']}; request serialised but NOT submitted."
            )
            logger.info(notes)
            return MT5OrderReport(
                order_id=0, symbol=spec.symbol, order_type=side_upper,
                volume_lots=normalised_volume, execution_price=0.0,
                retcode=0, mql_trade_request=mql_request, is_executed=False,
                status="MT5_DRY_RUN_VALIDATED", audit_notes=notes,
                retry_disposition=DISPOSITION_NOT_SENT, requires_reconciliation=False,
            )

        # Everything past this point may have moved real money. Failing to
        # observe the outcome is not the same as failing to trade.
        try:
            res = self.ipc.order_send(mql_request)  # type: ignore[union-attr]
        except Exception as exc:  # any IPC/adapter fault: the order may still have landed
            return self._ambiguous(
                spec, side_upper, normalised_volume, mql_request,
                f"order_send() raised {type(exc).__name__}: {exc}. The request may already "
                "have been accepted.",
            )

        if res is None:
            # MetaTrader5.order_send() returns None when the terminal could not
            # process the call at all. last_error() explains why, but says
            # nothing about whether the trade server saw the request.
            return self._ambiguous(
                spec, side_upper, normalised_volume, mql_request,
                "order_send() returned None (no MqlTradeResult). Call last_error() for the "
                "terminal-side reason.",
            )

        retcode = int(_attr(res, "retcode", TRADE_RETCODE_ERROR))
        disposition = classify_retcode(retcode)
        broker_comment = str(_attr(res, "comment", "") or "")
        order_id = int(_attr(res, "order", 0) or 0)
        deal_id = int(_attr(res, "deal", 0) or 0)
        # MqlTradeResult.volume is "Deal volume, confirmed by broker" - the only
        # trustworthy fill quantity, and it differs from the requested volume
        # under IOC partial execution.
        filled = float(_attr(res, "volume", 0.0) or 0.0)
        confirmed_price = float(_attr(res, "price", 0.0) or 0.0)

        if retcode == TRADE_RETCODE_DONE:
            # 10009 means the request completed in full, so an absent volume or
            # price in the result is a gap in the result, not a zero fill.
            # Reporting 0.0 there would corrupt position and PnL accounting.
            if filled <= 0:
                logger.warning(
                    "MT5 RESULT GAP [%s]: retcode 10009 with no confirmed volume; "
                    "falling back to the requested %s lots.", spec.symbol, normalised_volume,
                )
                filled = normalised_volume
            if confirmed_price <= 0:
                logger.warning(
                    "MT5 RESULT GAP [%s]: retcode 10009 with no confirmed price; "
                    "falling back to the requested %s.", spec.symbol, mql_request["price"],
                )
                confirmed_price = float(mql_request["price"])
            status, executed = "MT5_ORDER_EXECUTED_SUCCESS", True
            notes = (
                f"MT5 SUCCESS [{spec.symbol}]: filled {side_upper} {filled} lots at "
                f"{confirmed_price} (order #{order_id}, deal #{deal_id}, retcode=10009)."
            )
            logger.info(notes)
        elif retcode == TRADE_RETCODE_DONE_PARTIAL and filled <= 0:
            # Self-contradictory: "part of the request was completed" with no
            # confirmed volume. The true fill is unknown, so treat it as such.
            return self._ambiguous(
                spec, side_upper, normalised_volume, mql_request,
                "retcode 10010 (partial fill) arrived with no confirmed volume, so the "
                "filled quantity is unknown.",
                retcode=retcode, broker_comment=broker_comment,
            )
        elif retcode == TRADE_RETCODE_DONE_PARTIAL:
            status, executed = "MT5_ORDER_PARTIALLY_FILLED", True
            notes = (
                f"MT5 PARTIAL [{spec.symbol}]: retcode=10010, broker confirmed {filled} of "
                f"{normalised_volume} lots {side_upper} at {confirmed_price} (order #{order_id}, "
                f"deal #{deal_id}). A position IS open - size any follow-up from the shortfall, "
                "never by resending the original volume."
            )
            logger.warning(notes)
        elif retcode == TRADE_RETCODE_PLACED:
            status, executed = "MT5_ORDER_PLACED", False
            notes = (
                f"MT5 PLACED [{spec.symbol}]: retcode=10008, order #{order_id} accepted but not "
                "yet filled. Track it to a terminal state before assuming any exposure."
            )
            logger.warning(notes)
        elif disposition == DISPOSITION_AMBIGUOUS:
            return self._ambiguous(
                spec, side_upper, normalised_volume, mql_request,
                f"retcode {retcode} ('{broker_comment}') leaves the outcome unknown.",
                retcode=retcode, broker_comment=broker_comment,
            )
        else:
            status, executed = "MT5_EXECUTION_FAILED", False
            notes = (
                f"MT5 FAILED [{spec.symbol}]: retcode {retcode} ('{broker_comment}'), "
                f"disposition={disposition}."
            )
            logger.error(notes)

        return MT5OrderReport(
            order_id=order_id,
            symbol=spec.symbol,
            order_type=side_upper,
            volume_lots=normalised_volume,
            execution_price=confirmed_price if executed else 0.0,
            retcode=retcode,
            mql_trade_request=mql_request,
            is_executed=executed,
            status=status,
            audit_notes=notes,
            deal_id=deal_id,
            filled_volume_lots=filled if executed else 0.0,
            retry_disposition=disposition,
            requires_reconciliation=False,
            broker_comment=broker_comment,
        )

    def _ambiguous(
        self,
        spec: MT5SymbolSpec,
        side_upper: str,
        volume: float,
        mql_request: Dict[str, Any],
        reason: str,
        retcode: int = TRADE_RETCODE_CONNECTION,
        broker_comment: str = "",
    ) -> MT5OrderReport:
        """Build the report for an outcome the client cannot determine."""
        notes = (
            f"MT5 AMBIGUOUS [{spec.symbol}]: {reason} Reconcile magic="
            f"{self.config.magic_number} via history_deals_get/positions_get BEFORE any resend."
        )
        logger.error(notes)
        return MT5OrderReport(
            order_id=0,
            symbol=spec.symbol,
            order_type=side_upper,
            volume_lots=volume,
            execution_price=0.0,
            retcode=retcode,
            mql_trade_request=mql_request,
            is_executed=False,
            status="MT5_EXECUTION_AMBIGUOUS",
            audit_notes=notes,
            retry_disposition=DISPOSITION_AMBIGUOUS,
            requires_reconciliation=True,
            broker_comment=broker_comment,
        )

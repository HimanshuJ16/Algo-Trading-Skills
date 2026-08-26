"""
mifid-ii-algo-trading-compliance-eu: reference implementation of the RTS 6 pre-trade
control surface for an algorithmic trading system connected to an EU trading venue.

Legal basis for each control implemented here (Commission Delegated Regulation (EU)
2017/589, "RTS 6", supplementing Directive 2014/65/EU):

  * Article 15(1)(a) price collars           -> ``check_price_collar``
  * Article 15(1)(b) maximum order values    -> ``check_order_value``
  * Article 15(1)(c) maximum order volumes   -> ``check_volume``
  * Article 15(1)(d) maximum messages limits -> ``record_message`` / ``check_message_rate``
  * Article 12 kill functionality (immediate cancellation of unexecuted orders)
                                             -> ``trigger_rts6_kill_switch``
  * Article 15(3) disabled until re-enabled by a designated staff member
                                             -> ``reset_kill_switch``
  * Article 9 / Annex I self-assessment evidence -> the audit trail sent to ``audit_sink``

IMPORTANT — RTS 6 prescribes no numeric thresholds. Article 15 requires that these
control *categories* exist and are applied; the values (collar percentage, order value
cap, volume cap, message rate) must be calibrated by the firm to its instruments,
capital base, clearing arrangements and risk tolerance (Article 15(4)) and evidenced in
the Article 9 self-assessment. The defaults below are placeholders, not requirements.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import datetime
import logging
import math
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

#: RTS 22 (Commission Delegated Regulation (EU) 2017/590) Field 29 "Trading capacity".
#: DEAL = dealing on own account, MTCH = matched principal, AOTC = any other capacity.
VALID_TRADING_CAPACITIES = frozenset({"DEAL", "MTCH", "AOTC"})

#: Message kinds counted against the Article 15(1)(d) maximum messages limit, which
#: covers "the submission, modification or cancellation of an order".
VALID_MESSAGE_KINDS = frozenset({"NEW", "AMEND", "CANCEL"})


class KillSwitchCancellationError(RuntimeError):
    """Raised when the Article 12 order-cancellation callback failed.

    The halt itself is always applied before the callback runs, so this exception
    means "order flow is stopped but resting orders may still be live on the venue" —
    an operator must confirm cancellation with the venue out of band.
    """


@dataclass
class MiFID2OrderTag:
    """Identifiers attached to an outbound order.

    RTS 6 Article 12(3) requires the firm to be able to identify which trading
    algorithm and which trader, trading desk or client is responsible for each order
    sent to a trading venue. Venues carry the matching obligation via MiFID II
    Article 48 (flagging of algorithmically generated orders) and record the fields
    under RTS 24 (Commission Delegated Regulation (EU) 2017/580).

    The exact wire representation is venue-specific: verify each venue's rulebook and
    FIX/native specification rather than assuming this shape is accepted as-is.

    ``short_selling_flag`` is a firm-internal boolean. It is NOT the MiFIR short
    selling indicator, which is a coded value (SESH/SSEX/SELL/UNDI, RTS 22 Field 62)
    applicable only to instruments in scope of Regulation (EU) No 236/2012; a boolean
    cannot express "UNDI" (undetermined). Map it explicitly at the reporting boundary.

    ``timestamp_ns`` is taken from the host wall clock. It is NOT by itself evidence of
    compliance with RTS 25 (Commission Delegated Regulation (EU) 2017/574), which for
    high-frequency algorithmic trading requires a maximum divergence of 100 microseconds
    from UTC and a granularity of 1 microsecond or better. Meeting RTS 25 needs a
    disciplined, traceable time source (e.g. PTP) and is a host/infrastructure concern.
    """

    algo_id: str
    client_id: str
    trading_capacity: str  # RTS 22 Field 29: "DEAL", "MTCH" or "AOTC"
    short_selling_flag: bool
    timestamp_ns: int

    def __post_init__(self) -> None:
        if not self.algo_id:
            raise ValueError("algo_id must be a non-empty identifier (RTS 6 Art. 12(3))")
        if not self.client_id:
            raise ValueError("client_id must be a non-empty identifier")
        if self.trading_capacity not in VALID_TRADING_CAPACITIES:
            raise ValueError(
                f"trading_capacity {self.trading_capacity!r} is not a valid RTS 22 Field 29 "
                f"value; expected one of {sorted(VALID_TRADING_CAPACITIES)}"
            )


@dataclass
class RTS6PreTradeResult:
    """Outcome of the Article 15(1) pre-trade control set for a single order."""

    approved: bool
    price_collar_pass: bool
    order_value_pass: bool
    volume_pass: bool
    message_rate_pass: bool
    rejection_reasons: List[str]


@dataclass
class KillSwitchResult:
    """Outcome of an Article 12 kill-functionality invocation."""

    halted: bool
    cancellation_confirmed: bool
    operator_id: str
    reason: str
    timestamp: str


@dataclass
class _Limits:
    """Firm-calibrated Article 15 limits. No value here is mandated by RTS 6."""

    max_order_value: float
    max_volume: float
    max_msgs_per_sec: int
    price_collar_pct: float
    price_collar_pct_by_symbol: Dict[str, float] = field(default_factory=dict)


class MiFID2ComplianceManager:
    """RTS 6 Article 15 pre-trade control gate with Article 12 kill functionality.

    Thread-safe: every method that reads or mutates the message-rate window, the kill
    state or the audit buffer holds a re-entrant lock, so one instance may be shared by
    concurrent order-submission threads.

    This class is a *control gate*, not a retention system. The in-memory ``audit_log``
    is a bounded ring buffer for inspection and testing only; durable retention (RTS 6
    Article 28 requires five years for firms using a high-frequency algorithmic trading
    technique) must be provided by the ``audit_sink`` callable writing to durable,
    tamper-evident storage.
    """

    def __init__(
        self,
        max_order_value: float = 100_000.0,
        max_volume: float = 10_000.0,
        max_msgs_per_sec: int = 10,
        price_collar_pct: float = 0.05,
        algo_id: str = "ALGO_EUR_VOL_01",
        cancel_resting_orders_fn: Optional[Callable[[], None]] = None,
        price_collar_pct_by_symbol: Optional[Mapping[str, float]] = None,
        audit_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        max_audit_records: int = 10_000,
        allow_non_positive_reference_price: bool = False,
    ) -> None:
        """
        Args:
            max_order_value: Article 15(1)(b) cap on absolute notional per order.
            max_volume: Article 15(1)(c) cap on units per order.
            max_msgs_per_sec: Article 15(1)(d) cap on order messages per rolling second.
            price_collar_pct: Article 15(1)(a) default fractional deviation cap
                (0.05 == 5%). A placeholder, not an RTS 6 threshold.
            algo_id: Algorithm identifier stamped on outbound orders (Art. 12(3)).
            cancel_resting_orders_fn: Callable performing the Article 12 mass cancel.
                Must be idempotent — it may be invoked more than once.
            price_collar_pct_by_symbol: Per-instrument collar overrides. Article
                15(1)(a) requires collars to differentiate between different financial
                instruments; one global percentage does not satisfy that for a
                multi-instrument universe.
            audit_sink: Called with each audit record for durable retention. Sink
                failures are logged and counted, never allowed to block the risk gate.
            max_audit_records: Ring-buffer size for the in-memory ``audit_log``.
            allow_non_positive_reference_price: When False (default, fail-closed) an
                order whose reference price is zero or negative is rejected, because a
                percentage collar is undefined there. Instruments that legitimately
                trade at or below zero (power, gas, some commodity futures) need an
                absolute-tick collar instead; only enable this when such a collar is
                enforced upstream.
        """
        if not math.isfinite(max_order_value) or max_order_value <= 0:
            raise ValueError("max_order_value must be a finite positive number")
        if not math.isfinite(max_volume) or max_volume <= 0:
            raise ValueError("max_volume must be a finite positive number")
        if max_msgs_per_sec <= 0:
            raise ValueError("max_msgs_per_sec must be a positive integer")
        if not math.isfinite(price_collar_pct) or not 0 < price_collar_pct <= 1:
            raise ValueError("price_collar_pct must be a fraction in (0, 1], e.g. 0.05 for 5%")
        if max_audit_records <= 0:
            raise ValueError("max_audit_records must be a positive integer")

        overrides: Dict[str, float] = dict(price_collar_pct_by_symbol or {})
        for sym, pct in overrides.items():
            if not math.isfinite(pct) or not 0 < pct <= 1:
                raise ValueError(
                    f"price collar override for {sym!r} must be a fraction in (0, 1], got {pct!r}"
                )

        self._limits = _Limits(
            max_order_value=float(max_order_value),
            max_volume=float(max_volume),
            max_msgs_per_sec=int(max_msgs_per_sec),
            price_collar_pct=float(price_collar_pct),
            price_collar_pct_by_symbol=overrides,
        )
        self.algo_id = algo_id
        self.cancel_resting_orders_fn: Callable[[], None] = cancel_resting_orders_fn or (
            lambda: logger.info("Cancelled all outstanding resting orders on venue.")
        )
        self.audit_sink = audit_sink
        self.allow_non_positive_reference_price = bool(allow_non_positive_reference_price)

        self._lock = threading.RLock()
        self.message_timestamps: Deque[float] = deque()
        self.kill_switch_active = False
        self.audit_log: Deque[Dict[str, Any]] = deque(maxlen=int(max_audit_records))
        self.audit_sink_failures = 0

    # -- Article 15 limits: read-only views preserving the original attribute names --

    @property
    def max_order_value(self) -> float:
        return self._limits.max_order_value

    @property
    def max_volume(self) -> float:
        return self._limits.max_volume

    @property
    def max_msgs_per_sec(self) -> int:
        return self._limits.max_msgs_per_sec

    @property
    def price_collar_pct(self) -> float:
        return self._limits.price_collar_pct

    def collar_pct_for(self, symbol: Optional[str]) -> float:
        """Return the Article 15(1)(a) collar applicable to ``symbol``."""
        if symbol is None:
            return self._limits.price_collar_pct
        return self._limits.price_collar_pct_by_symbol.get(symbol, self._limits.price_collar_pct)

    # ------------------------------ Article 15(1) checks ------------------------------

    def check_price_collar(
        self, order_price: float, reference_price: float, symbol: Optional[str] = None
    ) -> bool:
        """Article 15(1)(a). True when the order price is inside the collar.

        Rejects (returns False) on any non-finite input, and — unless
        ``allow_non_positive_reference_price`` is set — on a reference price of zero or
        below, where a percentage collar has no meaning.
        """
        if not (math.isfinite(order_price) and math.isfinite(reference_price)):
            return False
        if reference_price == 0:
            return False
        if reference_price < 0 and not self.allow_non_positive_reference_price:
            return False
        deviation = abs(order_price - reference_price) / abs(reference_price)
        return deviation <= self.collar_pct_for(symbol)

    def check_order_value(self, price: float, quantity: float) -> bool:
        """Article 15(1)(b). Compares |price x quantity| against the cap.

        The absolute value matters: a signed quantity or a negatively-priced
        instrument would otherwise produce a negative notional that slips under any
        positive cap.
        """
        if not (math.isfinite(price) and math.isfinite(quantity)):
            return False
        return abs(price * quantity) <= self._limits.max_order_value

    def check_volume(self, quantity: float) -> bool:
        """Article 15(1)(c). Rejects non-finite, zero, negative and oversized sizes."""
        if not math.isfinite(quantity) or quantity <= 0:
            return False
        return quantity <= self._limits.max_volume

    def record_message(self, kind: str = "NEW") -> bool:
        """Article 15(1)(d). Count one order message against the rolling-second cap.

        Article 15(1)(d) covers messages "pertaining to the submission, modification or
        cancellation of an order", so amend and cancel traffic must be counted here too
        — it does not pass through :meth:`validate_pretrade_order`.

        Returns True and records the message when it fits inside the cap; returns False
        and records nothing when the cap is already reached.
        """
        if kind not in VALID_MESSAGE_KINDS:
            raise ValueError(f"message kind {kind!r} is not one of {sorted(VALID_MESSAGE_KINDS)}")
        with self._lock:
            now = time.monotonic()
            while self.message_timestamps and now - self.message_timestamps[0] > 1.0:
                self.message_timestamps.popleft()

            if len(self.message_timestamps) >= self._limits.max_msgs_per_sec:
                return False

            self.message_timestamps.append(now)
            return True

    def check_message_rate(self) -> bool:
        """Article 15(1)(d) for a new-order message. See :meth:`record_message`."""
        return self.record_message("NEW")

    # ------------------------------- The pre-trade gate -------------------------------

    def validate_pretrade_order(
        self, price: float, quantity: float, reference_price: float, symbol: str
    ) -> RTS6PreTradeResult:
        """Run the full Article 15(1) control set for one prospective order.

        ``symbol`` is required: every audit record must name the instrument the decision
        applied to, and the per-instrument collar of Article 15(1)(a) is selected from it.

        Rate-limit accounting note: the Article 15(1)(d) budget is consumed at validation
        time, before the other checks are known to pass, so an order rejected by a collar
        still consumes a message slot. This over-counts relative to Article 15(2) ("all
        orders sent to a trading venue"), which is the safe direction — it throttles
        earlier, never later, than required.
        """
        if not symbol:
            raise ValueError("symbol is required for a compliant Article 15 audit record")

        with self._lock:
            rejections: List[str] = []
            if self.kill_switch_active:
                # Short-circuit: no order may be sent, so consume no rate budget and run
                # no further checks. The decision is still audited.
                rejections.append("RTS 6 Art. 12 kill switch ACTIVE — all order placement halted.")
                result = RTS6PreTradeResult(
                    approved=False,
                    price_collar_pass=False,
                    order_value_pass=False,
                    volume_pass=False,
                    message_rate_pass=False,
                    rejection_reasons=rejections,
                )
                self._log_audit_event(symbol, price, quantity, result)
                return result

            collar_pct = self.collar_pct_for(symbol)
            pass_collar = self.check_price_collar(price, reference_price, symbol)
            if not pass_collar:
                if not math.isfinite(reference_price) or reference_price <= 0:
                    rejections.append(
                        f"Price collar not evaluable: reference price {reference_price!r} for "
                        f"{symbol} is not usable for a percentage collar"
                    )
                elif not math.isfinite(price):
                    rejections.append(f"Price collar breach: order price {price!r} is not finite")
                else:
                    rejections.append(
                        f"Price collar breach: price {price} deviates >{collar_pct * 100:g}% "
                        f"from reference price {reference_price} for {symbol}"
                    )

            pass_val = self.check_order_value(price, quantity)
            if not pass_val:
                rejections.append(
                    f"Order value breach: |{price} x {quantity}| exceeds max allowed "
                    f"{self._limits.max_order_value:.2f}"
                )

            pass_vol = self.check_volume(quantity)
            if not pass_vol:
                rejections.append(
                    f"Volume breach: quantity {quantity!r} is not a positive finite size within "
                    f"max allowed {self._limits.max_volume}"
                )

            pass_rate = self.check_message_rate()
            if not pass_rate:
                rejections.append(f"Message rate breach: > {self._limits.max_msgs_per_sec} msgs/sec")

            result = RTS6PreTradeResult(
                approved=not rejections,
                price_collar_pass=pass_collar,
                order_value_pass=pass_val,
                volume_pass=pass_vol,
                message_rate_pass=pass_rate,
                rejection_reasons=rejections,
            )
            self._log_audit_event(symbol, price, quantity, result)
            return result

    def tag_order(
        self, client_id: str, short_selling: bool = False, trading_capacity: str = "DEAL"
    ) -> MiFID2OrderTag:
        """Build the identifiers required to attribute an order (RTS 6 Art. 12(3)).

        See :class:`MiFID2OrderTag` for the limits of ``short_selling`` and of the
        timestamp with respect to RTS 22 and RTS 25.
        """
        return MiFID2OrderTag(
            algo_id=self.algo_id,
            client_id=client_id,
            trading_capacity=trading_capacity,
            short_selling_flag=short_selling,
            timestamp_ns=time.time_ns(),
        )

    # -------------------------- Article 12 kill functionality --------------------------

    def trigger_rts6_kill_switch(self, operator_id: str, reason: str) -> KillSwitchResult:
        """RTS 6 Article 12 kill functionality: halt order flow and cancel resting orders.

        The halt flag is set *before* the cancellation callback runs, so a failing
        cancellation can never leave the gate open. If the callback raises, the failure
        is logged at CRITICAL, written to the audit trail with
        ``cancellation_confirmed=False``, and re-raised as
        :class:`KillSwitchCancellationError` — resting orders may still be live on the
        venue and an operator must confirm cancellation out of band.

        Callable more than once; ``cancel_resting_orders_fn`` must be idempotent.
        """
        if not operator_id:
            raise ValueError("operator_id is required: Art. 12 invocations must be attributable")

        with self._lock:
            self.kill_switch_active = True
            logger.critical(
                "RTS 6 Art. 12 EMERGENCY KILL SWITCH TRIGGERED by %r: %s", operator_id, reason
            )
            try:
                self.cancel_resting_orders_fn()
            except Exception as exc:  # noqa: BLE001 - re-raised below, never swallowed
                logger.critical(
                    "RTS 6 Art. 12 order cancellation FAILED after halt (operator=%r): %s",
                    operator_id,
                    exc,
                )
                self._append_audit_record(
                    {
                        "event": "KILL_SWITCH_TRIGGERED",
                        "operator_id": operator_id,
                        "reason": reason,
                        "cancellation_confirmed": False,
                        "cancellation_error": repr(exc),
                    }
                )
                raise KillSwitchCancellationError(
                    "order flow halted but resting-order cancellation failed; confirm "
                    "cancellation with the venue out of band"
                ) from exc

            record = self._append_audit_record(
                {
                    "event": "KILL_SWITCH_TRIGGERED",
                    "operator_id": operator_id,
                    "reason": reason,
                    "cancellation_confirmed": True,
                }
            )
            return KillSwitchResult(
                halted=True,
                cancellation_confirmed=True,
                operator_id=operator_id,
                reason=reason,
                timestamp=record["timestamp"],
            )

    def reset_kill_switch(self, operator_id: str, reason: str) -> None:
        """Re-enable order flow after a halt.

        RTS 6 Article 15(3) requires a disabled trading system to stay disabled "until
        re-enabled by a designated staff member", so re-enabling is deliberately a
        manual, attributed and audited action — never an automatic recovery path.
        """
        if not operator_id:
            raise ValueError("operator_id is required: Art. 15(3) re-enabling must be attributable")

        with self._lock:
            was_active = self.kill_switch_active
            self.kill_switch_active = False
            logger.warning(
                "RTS 6 Art. 15(3) kill switch RESET by %r (was_active=%s): %s",
                operator_id,
                was_active,
                reason,
            )
            self._append_audit_record(
                {
                    "event": "KILL_SWITCH_RESET",
                    "operator_id": operator_id,
                    "reason": reason,
                    "was_active": was_active,
                }
            )

    # ------------------------------ Audit trail (Art. 9) ------------------------------

    def _log_audit_event(
        self, symbol: str, price: float, quantity: float, result: RTS6PreTradeResult
    ) -> None:
        self._append_audit_record(
            {
                "event": "PRETRADE_DECISION",
                "symbol": symbol,
                "price": price,
                "quantity": quantity,
                "approved": result.approved,
                "rejections": list(result.rejection_reasons),
            }
        )

    def _append_audit_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Buffer one audit record and forward it to the durable sink.

        A failing sink is logged and counted but never propagated: losing an audit
        record is a compliance gap, while letting a logging fault take down the
        pre-trade gate would be a trading incident. Monitor ``audit_sink_failures``.
        """
        record: Dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "algo_id": self.algo_id,
            **payload,
        }
        with self._lock:
            self.audit_log.append(record)
            if self.audit_sink is not None:
                try:
                    self.audit_sink(dict(record))
                except Exception as exc:  # noqa: BLE001 - must not break the risk gate
                    self.audit_sink_failures += 1
                    logger.critical(
                        "RTS 6 audit sink failed (failures=%d), record retained in memory only: %s",
                        self.audit_sink_failures,
                        exc,
                    )
        return record


class PreTradeRiskControls:
    """Backward-compatible wrapper around :class:`MiFID2ComplianceManager`."""

    def __init__(
        self,
        max_order_value: float,
        max_volume: float,
        max_msgs_per_sec: int,
        price_collar_pct: float,
    ) -> None:
        self.mgr = MiFID2ComplianceManager(
            max_order_value=max_order_value,
            max_volume=max_volume,
            max_msgs_per_sec=max_msgs_per_sec,
            price_collar_pct=price_collar_pct,
        )
        self.max_order_value = max_order_value
        self.max_volume = max_volume
        self.max_msgs_per_sec = max_msgs_per_sec
        self.price_collar_pct = price_collar_pct

    def check_price_collar(self, order_price: float, reference_price: float) -> bool:
        return self.mgr.check_price_collar(order_price, reference_price)

    def check_order_value(self, price: float, quantity: float) -> bool:
        return self.mgr.check_order_value(price, quantity)

    def check_volume(self, quantity: float) -> bool:
        return self.mgr.check_volume(quantity)

    def check_message_rate(self) -> bool:
        return self.mgr.check_message_rate()

    def all_checks(
        self,
        price: float,
        quantity: float,
        reference_price: float,
        symbol: str = "UNSPECIFIED",
    ) -> Dict[str, Any]:
        """Legacy entry point.

        ``symbol`` defaults to the explicit sentinel ``"UNSPECIFIED"`` rather than a
        plausible-looking instrument, so an audit record produced through this path is
        never mistaken for one attributed to a real instrument. Pass the real symbol.
        """
        return self.mgr.validate_pretrade_order(price, quantity, reference_price, symbol).__dict__

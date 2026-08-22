"""
canary-releases-for-strategy-code-changes:
Gates a newly deployed (or materially changed) trading strategy through
SHADOW -> CANARY -> PRODUCTION, capping how much live exposure the strategy can
create while it is still unproven.

The router owns exactly one thing: **how much of a strategy's requested order
quantity is allowed to reach the execution gateway right now**. It does not
submit orders, cancel them, track fills, or decide when to promote. Those belong
to the caller.

Three phases, and the difference between them is the size of the mistake a bad
build can make:

  - ``SHADOW``     : nothing is routed. The decision still reports the quantity
                     the strategy *wanted*, so the caller can record a
                     hypothetical fill and compare it against production later.
  - ``CANARY``     : the quantity is scaled down by ``canary_scale_factor``,
                     floored to the venue's lot step, and checked against the
                     venue's minimums and against this strategy's absolute
                     notional limits.
  - ``PRODUCTION`` : the quantity passes through unscaled. The router applies no
                     canary limits here by design — see "What this is not".

Five properties distinguish this from ``quantity * factor``, and each exists
because the naive version costs money in live trading:

  1. **The caller's signal is never mutated.** ``route()`` returns a *new*
     ``OrderSignal``. Scaling in place means a retry through the router scales an
     already-scaled order again (5% of 5% = 0.25%), and it silently rewrites the
     order record the strategy is holding a reference to.
  2. **A percentage is not an exposure cap.** 5% of a runaway 10,000,000-share
     order is still 500,000 shares. ``max_canary_order_notional`` and
     ``canary_notional_budget`` bound the absolute value a canary can put at
     risk, per order and cumulatively.
  3. **Scaling is done in ``Decimal``.** ``int(100 * 0.29)`` is 28, not 29,
     because 100 * 0.29 is 28.999999999999996 in binary floating point. An
     off-by-one lot is not material; an unexplained off-by-one in a risk control
     that operators are trying to trust is.
  4. **Every outcome is distinguishable.** A shadow suppression, a sub-lot drop,
     an exhausted budget and an *unregistered strategy* are four very different
     events; returning ``None`` for all four means the dangerous one (an unknown
     strategy hitting the order path) cannot be alerted on.
  5. **Phase changes are attributable.** ``set_phase()`` requires a named
     authoriser, refuses to jump SHADOW -> PRODUCTION without an explicit
     override, and records refusals as well as successes. See
     ``references/standards.md`` for the MiFID II RTS 6 obligations this is
     shaped around and for their jurisdictional limits.

What this is not:

  - **Not a pre-trade risk layer.** For a US broker-dealer, SEC Rule 15c3-5
    requires pre-trade financial and regulatory controls under the *broker-
    dealer's* direct and exclusive control. This router lives in strategy space,
    trusts its own configuration, and *reduces* quantities rather than blocking.
    It sits in front of that layer, never instead of it.
  - **Not a kill switch.** Demoting a strategy to ``SHADOW`` stops new orders
    from being routed; it does not cancel the working orders already at the
    venue, and it does not flatten a position. See
    ``kill-switch-and-drawdown-circuit-breakers``.
  - **Not fill-aware.** The cumulative budget is consumed by *submitted* order
    notional. The router never sees fills, cancels or rejects, so a caller that
    wants rejected orders credited back must call ``release_notional()``.
  - **Not fractional-quantity aware.** Quantities are integers (shares, lots,
    contracts). For instruments quoted in fractional size — crypto ``stepSize``
    of 0.001, FX notional — the same rules apply but the arithmetic must be done
    in ``Decimal`` end to end; do not cast such quantities to ``int`` to reuse
    this class.
"""
import dataclasses
import logging
import math
import numbers
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DeploymentPhase(Enum):
    """
    Lifecycle phase of a strategy. Values are preserved from v1 for callers that
    persisted them.

    - ``SHADOW``     : signals generated, nothing routed.
    - ``CANARY``     : scaled-down live orders, bounded absolutely.
    - ``PRODUCTION`` : full size, no canary limits applied.
    """
    SHADOW = 1
    CANARY = 2
    PRODUCTION = 3


class RoutingAction(Enum):
    """
    What the router did with a signal.

    - ``ROUTED``     : passed through at full requested size (PRODUCTION).
    - ``SCALED``     : routed at a reduced size (CANARY).
    - ``SUPPRESSED`` : deliberately not routed because the strategy is in
                       SHADOW. Expected, not an error.
    - ``REJECTED``   : not routed because something failed a check — unknown
                       strategy, invalid signal, below venue minimum, or an
                       exhausted exposure budget. Always worth an alert.
    """
    ROUTED = "ROUTED"
    SCALED = "SCALED"
    SUPPRESSED = "SUPPRESSED"
    REJECTED = "REJECTED"


class CanaryConfigError(ValueError):
    """Raised for a configuration the router refuses to operate with."""


@dataclass(frozen=True)
class StrategyRegistration:
    """
    Deployment configuration for one strategy.

    Field order is preserved from v1: ``strategy_id``, ``phase``,
    ``canary_scale_factor``, ``min_lot_size``.

    - ``canary_scale_factor`` : fraction of the requested quantity routed while
      in CANARY. Must be strictly between 0 and 1.
    - ``min_lot_size``        : the venue's quantity *step* (board lot, contract
      multiple). Scaled quantities are floored to a multiple of it.
    - ``min_quantity``        : the venue's minimum *quantity*, which is not the
      same thing as the step (Binance publishes ``minQty`` and ``stepSize``
      separately). Defaults to ``min_lot_size``.
    - ``min_notional``        : venue minimum order value, if any. A canary
      order is exactly the order most likely to fall under it.
    - ``max_canary_order_notional`` : hard ceiling on the value of a single
      canary order. When the scaled order exceeds it, the quantity is reduced
      further to fit (never increased) and the decision names the cap.
    - ``canary_notional_budget``    : cumulative ceiling on submitted canary
      notional for this strategy. Once exhausted, canary orders are rejected
      until an operator calls ``reset_canary_budget()``.

    Notional limits require a usable reference price on the signal. A signal
    with a non-positive price (a market order that carries no price) is
    *rejected* while any notional limit is configured, rather than being routed
    with the limit silently unenforced.
    """
    strategy_id: str
    phase: DeploymentPhase
    canary_scale_factor: float = 0.05  # Default 5%
    min_lot_size: int = 1
    min_quantity: Optional[int] = None
    min_notional: Optional[float] = None
    max_canary_order_notional: Optional[float] = None
    canary_notional_budget: Optional[float] = None

    @property
    def effective_min_quantity(self) -> int:
        """Venue minimum quantity, defaulting to one lot step."""
        return self.min_lot_size if self.min_quantity is None else self.min_quantity

    @property
    def has_notional_limit(self) -> bool:
        return any(
            limit is not None
            for limit in (self.min_notional,
                          self.max_canary_order_notional,
                          self.canary_notional_budget)
        )


@dataclass
class OrderSignal:
    """
    An order the strategy wants to send. Field order is preserved from v1.

    ``price`` is a reference price used only for notional limits and for the
    hypothetical-fill record; the router does not price the order. Set it to the
    expected execution price for a market order, or leave it at 0 and configure
    no notional limits.
    """
    strategy_id: str
    symbol: str
    quantity: int
    price: float
    side: str
    client_order_id: Optional[str] = None


@dataclass(frozen=True)
class RoutingDecision:
    """
    Outcome of routing one signal.

    ``signal`` is non-``None`` only for ``ROUTED`` and ``SCALED``, and is always
    a *new* object — never the one that was passed in.

    ``requested_quantity`` is what the strategy asked for and is populated in
    every outcome, including ``SUPPRESSED``: that is the number a shadow-mode
    hypothetical fill must be recorded against.
    """
    action: RoutingAction
    strategy_id: str
    phase: Optional[DeploymentPhase]
    requested_quantity: int
    routed_quantity: int
    notional: float
    reason: str
    signal: Optional[OrderSignal] = None
    binding_constraint: str = ""

    @property
    def is_live(self) -> bool:
        """True when a real order is to be submitted to the venue."""
        return self.action in (RoutingAction.ROUTED, RoutingAction.SCALED)


class StrategyCanaryRouter:
    """
    Routes strategy signals according to each strategy's deployment phase.

    Thread-safe: every registry read, phase change, routing decision and budget
    update happens under one re-entrant lock, because the trading thread calling
    ``route()`` and the operator thread calling ``set_phase()`` are not the same
    thread, and the cumulative budget is read-modify-write state.

    Typical use::

        router = StrategyCanaryRouter()
        router.register_strategy(StrategyRegistration(
            "momentum_v3", DeploymentPhase.SHADOW, min_lot_size=1))
        ...
        decision = router.route(signal)
        if decision.is_live:
            gateway.submit(decision.signal)
        elif decision.action is RoutingAction.REJECTED:
            alert(decision.reason)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.strategies: Dict[str, StrategyRegistration] = {}
        self._consumed_notional: Dict[str, float] = {}
        self._history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration and phase management
    # ------------------------------------------------------------------
    def register_strategy(self,
                          registration: StrategyRegistration,
                          authorised_by: str = "") -> None:
        """
        Register (or re-register) a strategy.

        Re-registering resets the strategy's consumed canary budget, because the
        configuration it was measured against has changed. Registering directly
        into CANARY or PRODUCTION is allowed — an already-live strategy has to be
        describable — but it is logged as a warning, since skipping SHADOW is the
        thing this skill exists to discourage.
        """
        self._validate_registration(registration)
        with self._lock:
            previous = self.strategies.get(registration.strategy_id)
            self.strategies[registration.strategy_id] = registration
            self._consumed_notional[registration.strategy_id] = 0.0
            self._record(
                action="register",
                strategy_id=registration.strategy_id,
                from_phase=previous.phase if previous else None,
                to_phase=registration.phase,
                authorised_by=authorised_by,
                detail=(f"scale={registration.canary_scale_factor}, "
                        f"lot_step={registration.min_lot_size}"),
            )
        if registration.phase is not DeploymentPhase.SHADOW:
            logger.warning(
                f"Registered strategy {registration.strategy_id} directly in "
                f"{registration.phase.name}, bypassing SHADOW validation.")
        else:
            logger.info(
                f"Registered strategy {registration.strategy_id} in SHADOW.")

    def set_phase(self,
                  strategy_id: str,
                  phase: DeploymentPhase,
                  authorised_by: str,
                  force: bool = False) -> None:
        """
        Move a strategy between deployment phases.

        ``authorised_by`` must name a person: for an EU/UK investment firm in
        scope of RTS 6 an unattributed deployment change does not satisfy
        Art. 5(2), and in every jurisdiction an unattributed one is unanswerable
        in a post-incident review.

        Promotions advance one step at a time (SHADOW -> CANARY -> PRODUCTION).
        A SHADOW -> PRODUCTION jump is refused unless ``force=True``, which is
        recorded as a forced transition. Demotions are always permitted without
        force: reducing a strategy's exposure must never be blocked by this
        router.
        """
        if not isinstance(phase, DeploymentPhase):
            raise TypeError(f"phase must be a DeploymentPhase, got {type(phase).__name__}")
        if not isinstance(authorised_by, str) or not authorised_by.strip():
            raise CanaryConfigError(
                "authorised_by must name the person authorising this phase change")

        with self._lock:
            reg = self.strategies.get(strategy_id)
            if reg is None:
                raise KeyError(f"Unregistered strategy: {strategy_id}")

            current = reg.phase
            if phase is current:
                return

            is_promotion = phase.value > current.value
            skips_a_phase = phase.value - current.value > 1
            if is_promotion and skips_a_phase and not force:
                self._record(action="set_phase_refused", strategy_id=strategy_id,
                             from_phase=current, to_phase=phase,
                             authorised_by=authorised_by,
                             detail="promotion skips a phase")
                raise CanaryConfigError(
                    f"Refusing to promote {strategy_id} from {current.name} straight to "
                    f"{phase.name}. Promote through {DeploymentPhase.CANARY.name} first, "
                    f"or pass force=True to record an explicit override.")

            self.strategies[strategy_id] = dataclasses.replace(reg, phase=phase)
            if phase is DeploymentPhase.CANARY:
                # A fresh canary run gets a fresh budget; otherwise a strategy
                # demoted and re-promoted would start with the budget already
                # spent by its previous run.
                self._consumed_notional[strategy_id] = 0.0
            self._record(action="set_phase", strategy_id=strategy_id,
                         from_phase=current, to_phase=phase,
                         authorised_by=authorised_by,
                         forced=bool(is_promotion and skips_a_phase and force))
        logger.info(
            f"Strategy {strategy_id}: {current.name} -> {phase.name} "
            f"(authorised_by={authorised_by}{', FORCED' if is_promotion and skips_a_phase else ''}).")

    def get_phase(self, strategy_id: str) -> DeploymentPhase:
        with self._lock:
            reg = self.strategies.get(strategy_id)
            if reg is None:
                raise KeyError(f"Unregistered strategy: {strategy_id}")
            return reg.phase

    # ------------------------------------------------------------------
    # Exposure budget
    # ------------------------------------------------------------------
    def consumed_notional(self, strategy_id: str) -> float:
        """Canary notional submitted so far under the current canary run."""
        with self._lock:
            return self._consumed_notional.get(strategy_id, 0.0)

    def release_notional(self, strategy_id: str, amount: float) -> None:
        """
        Credit budget back after an order the router counted never became live
        exposure — a venue rejection, or a cancel before any fill.

        The router cannot detect these itself; it never sees execution reports.
        Do **not** call this for a *filled* order: that notional was real.
        """
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
            raise CanaryConfigError(f"amount must be a finite, non-negative number, got {amount!r}")
        with self._lock:
            if strategy_id not in self.strategies:
                raise KeyError(f"Unregistered strategy: {strategy_id}")
            consumed = self._consumed_notional.get(strategy_id, 0.0)
            self._consumed_notional[strategy_id] = max(0.0, consumed - float(amount))

    def reset_canary_budget(self, strategy_id: str, authorised_by: str) -> None:
        """
        Deliberately restore a strategy's full canary budget. This is an
        operator decision to put more capital at risk on an unproven build, so it
        is attributed and recorded like a phase change.
        """
        if not isinstance(authorised_by, str) or not authorised_by.strip():
            raise CanaryConfigError("authorised_by must name the person authorising the reset")
        with self._lock:
            if strategy_id not in self.strategies:
                raise KeyError(f"Unregistered strategy: {strategy_id}")
            spent = self._consumed_notional.get(strategy_id, 0.0)
            self._consumed_notional[strategy_id] = 0.0
            self._record(action="reset_budget", strategy_id=strategy_id,
                         from_phase=self.strategies[strategy_id].phase,
                         to_phase=self.strategies[strategy_id].phase,
                         authorised_by=authorised_by,
                         detail=f"released consumed notional {spent}")
        logger.warning(
            f"Canary budget for {strategy_id} reset by {authorised_by} "
            f"(was {spent} consumed).")

    @property
    def phase_history(self) -> List[Dict[str, Any]]:
        """
        Ordered record of registrations, phase changes (including refused and
        forced ones) and budget resets. List order is authoritative; the
        wall-clock timestamps are for human reading and can move backwards
        across a clock correction.
        """
        with self._lock:
            return [dict(entry) for entry in self._history]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def route(self, signal: OrderSignal) -> RoutingDecision:
        """
        Decide what, if anything, to send to the gateway for this signal.

        Never mutates ``signal``. Fails closed: anything it cannot positively
        justify routing is ``REJECTED``.
        """
        if not isinstance(signal, OrderSignal):
            raise TypeError(f"signal must be an OrderSignal, got {type(signal).__name__}")

        with self._lock:
            reg = self.strategies.get(signal.strategy_id)
            if reg is None:
                logger.error(
                    f"Unregistered strategy {signal.strategy_id!r} reached the order "
                    f"path. Rejecting {signal.quantity} {signal.symbol}.")
                return self._reject(signal, None,
                                    "strategy is not registered with the canary router",
                                    "registration")

            invalid = self._signal_problem(signal, reg)
            if invalid:
                logger.error(f"Rejecting signal for {signal.strategy_id}: {invalid}")
                return self._reject(signal, reg.phase, invalid, "signal_validation")

            if reg.phase is DeploymentPhase.SHADOW:
                logger.info(
                    f"SHADOW: suppressed {signal.quantity} {signal.symbol} for "
                    f"{signal.strategy_id}; record as hypothetical fill.")
                return RoutingDecision(
                    action=RoutingAction.SUPPRESSED,
                    strategy_id=signal.strategy_id,
                    phase=reg.phase,
                    requested_quantity=signal.quantity,
                    routed_quantity=0,
                    notional=self._notional(signal.quantity, signal.price),
                    reason="strategy is in SHADOW; no order routed",
                )

            if reg.phase is DeploymentPhase.CANARY:
                return self._route_canary(signal, reg)

            # PRODUCTION: full size. Canary limits deliberately do not apply —
            # bounding production exposure is the pre-trade risk layer's job.
            logger.info(
                f"PRODUCTION: routing full {signal.quantity} {signal.symbol} for "
                f"{signal.strategy_id}.")
            return RoutingDecision(
                action=RoutingAction.ROUTED,
                strategy_id=signal.strategy_id,
                phase=reg.phase,
                requested_quantity=signal.quantity,
                routed_quantity=signal.quantity,
                notional=self._notional(signal.quantity, signal.price),
                reason="strategy is in PRODUCTION; routed at full size",
                signal=dataclasses.replace(signal),
            )

    def route_order(self, signal: OrderSignal) -> Optional[OrderSignal]:
        """
        v1-compatible wrapper: returns the order to send, or ``None``.

        Prefer ``route()``. This form cannot distinguish a deliberate shadow
        suppression from an unregistered strategy or an exhausted budget, and
        that distinction is what you need at 09:31.

        Unlike v1 this does **not** modify ``signal`` in place; the returned
        object is a new ``OrderSignal``.
        """
        return self.route(signal).signal

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _route_canary(self,
                      signal: OrderSignal,
                      reg: StrategyRegistration) -> RoutingDecision:
        step = reg.min_lot_size
        # Decimal, not float: int(100 * 0.29) == 28 in binary floating point.
        scaled = int((Decimal(int(signal.quantity)) * Decimal(str(reg.canary_scale_factor)))
                     .to_integral_value(rounding=ROUND_FLOOR))
        final_qty = (scaled // step) * step
        binding = "scale_factor"

        # Absolute per-order ceiling: reduce further to fit, never increase.
        if reg.max_canary_order_notional is not None and final_qty > 0:
            price = Decimal(str(signal.price))
            max_qty = int((Decimal(str(reg.max_canary_order_notional)) / price)
                          .to_integral_value(rounding=ROUND_FLOOR))
            capped = (max_qty // step) * step
            if capped < final_qty:
                logger.warning(
                    f"CANARY: {signal.strategy_id} order for {signal.symbol} reduced "
                    f"{final_qty} -> {capped} by max_canary_order_notional "
                    f"{reg.max_canary_order_notional}.")
                final_qty = capped
                binding = "max_canary_order_notional"

        if final_qty < reg.effective_min_quantity or final_qty <= 0:
            reason = (f"canary quantity {final_qty} is below the venue minimum "
                      f"{reg.effective_min_quantity} (requested {signal.quantity}, "
                      f"scale {reg.canary_scale_factor}, lot step {step})")
            logger.warning(f"CANARY: dropping {signal.symbol} for {signal.strategy_id}: {reason}")
            return self._reject(signal, reg.phase, reason, "min_quantity")

        notional = self._notional(final_qty, signal.price)

        if reg.min_notional is not None and notional < reg.min_notional:
            reason = (f"canary order notional {notional} is below the venue minimum "
                      f"{reg.min_notional}; the venue would reject it")
            logger.warning(f"CANARY: dropping {signal.symbol} for {signal.strategy_id}: {reason}")
            return self._reject(signal, reg.phase, reason, "min_notional")

        if reg.canary_notional_budget is not None:
            consumed = self._consumed_notional.get(signal.strategy_id, 0.0)
            if consumed + notional > reg.canary_notional_budget:
                reason = (f"canary notional budget exhausted: {consumed} consumed of "
                          f"{reg.canary_notional_budget}, this order needs {notional}")
                logger.warning(
                    f"CANARY: refusing {signal.symbol} for {signal.strategy_id}: {reason}")
                return self._reject(signal, reg.phase, reason, "canary_notional_budget")
            self._consumed_notional[signal.strategy_id] = consumed + notional

        logger.info(
            f"CANARY: scaling {signal.quantity} -> {final_qty} {signal.symbol} for "
            f"{signal.strategy_id} (binding constraint: {binding}).")
        return RoutingDecision(
            action=RoutingAction.SCALED,
            strategy_id=signal.strategy_id,
            phase=reg.phase,
            requested_quantity=signal.quantity,
            routed_quantity=final_qty,
            notional=notional,
            reason=f"canary-scaled by {binding}",
            signal=dataclasses.replace(signal, quantity=final_qty),
            binding_constraint=binding,
        )

    @staticmethod
    def _reject(signal: OrderSignal,
                phase: Optional[DeploymentPhase],
                reason: str,
                constraint: str) -> RoutingDecision:
        return RoutingDecision(
            action=RoutingAction.REJECTED,
            strategy_id=signal.strategy_id,
            phase=phase,
            requested_quantity=signal.quantity,
            routed_quantity=0,
            notional=0.0,
            reason=reason,
            binding_constraint=constraint,
        )

    @staticmethod
    def _notional(quantity: int, price: float) -> float:
        if not math.isfinite(price) or price <= 0:
            return 0.0
        return float(Decimal(int(quantity)) * Decimal(str(price)))

    @staticmethod
    def _signal_problem(signal: OrderSignal,
                        reg: StrategyRegistration) -> str:
        """Return a description of why this signal must not be routed, or ''."""
        # numbers.Integral, not int: numpy integer types are what a vectorised
        # strategy actually produces, and they are not int subclasses.
        if not isinstance(signal.quantity, numbers.Integral) or isinstance(signal.quantity, bool):
            return (f"quantity must be a whole number of shares/lots/contracts "
                    f"(fractional sizes need Decimal arithmetic end to end), "
                    f"got {signal.quantity!r}")
        if signal.quantity <= 0:
            return f"quantity must be positive, got {signal.quantity}"
        if not isinstance(signal.side, str) or not signal.side.strip():
            return "side must be a non-empty string"
        if not isinstance(signal.price, (int, float)) or isinstance(signal.price, bool):
            return f"price must be a number, got {signal.price!r}"
        if not math.isfinite(signal.price) or signal.price < 0:
            return f"price must be finite and non-negative, got {signal.price}"
        if signal.price <= 0 and reg.has_notional_limit:
            # Routing here would enforce none of the configured value limits.
            return ("notional limits are configured for this strategy but the signal "
                    "carries no usable reference price")
        return ""

    def _validate_registration(self, registration: StrategyRegistration) -> None:
        if not isinstance(registration, StrategyRegistration):
            raise TypeError(
                f"registration must be a StrategyRegistration, "
                f"got {type(registration).__name__}")
        if not isinstance(registration.strategy_id, str) or not registration.strategy_id.strip():
            raise CanaryConfigError("strategy_id must be a non-empty string")
        if not isinstance(registration.phase, DeploymentPhase):
            raise CanaryConfigError("phase must be a DeploymentPhase")

        factor = registration.canary_scale_factor
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            raise CanaryConfigError(
                f"canary_scale_factor must be a number, got {factor!r}")
        if not math.isfinite(factor) or factor <= 0 or factor >= 1.0:
            raise CanaryConfigError(
                f"canary_scale_factor must be finite and strictly between 0 and 1.0, "
                f"got {factor!r}")

        step = registration.min_lot_size
        # A zero step is the dangerous one: v1 reached `scaled % step` at routing
        # time and raised ZeroDivisionError inside the live order path.
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            raise CanaryConfigError(
                f"min_lot_size must be an int >= 1, got {step!r}")

        min_qty = registration.min_quantity
        if min_qty is not None:
            if not isinstance(min_qty, int) or isinstance(min_qty, bool) or min_qty < 1:
                raise CanaryConfigError(
                    f"min_quantity must be an int >= 1 when set, got {min_qty!r}")
            if min_qty % step != 0:
                raise CanaryConfigError(
                    f"min_quantity {min_qty} is not a multiple of min_lot_size {step}; "
                    f"no quantity could satisfy both")

        for name in ("min_notional", "max_canary_order_notional", "canary_notional_budget"):
            value = getattr(registration, name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CanaryConfigError(f"{name} must be a number when set, got {value!r}")
            if not math.isfinite(value) or value <= 0:
                raise CanaryConfigError(
                    f"{name} must be finite and positive when set, got {value!r}")

        if (registration.min_notional is not None
                and registration.max_canary_order_notional is not None
                and registration.max_canary_order_notional < registration.min_notional):
            raise CanaryConfigError(
                f"max_canary_order_notional {registration.max_canary_order_notional} is "
                f"below min_notional {registration.min_notional}; every canary order "
                f"would be rejected")

    def _record(self,
                action: str,
                strategy_id: str,
                from_phase: Optional[DeploymentPhase],
                to_phase: Optional[DeploymentPhase],
                authorised_by: str,
                forced: bool = False,
                detail: str = "") -> None:
        self._history.append({
            "timestamp": time.time(),
            "action": action,
            "strategy_id": strategy_id,
            "from_phase": from_phase.name if from_phase else None,
            "to_phase": to_phase.name if to_phase else None,
            "authorised_by": authorised_by,
            "forced": forced,
            "detail": detail,
        })

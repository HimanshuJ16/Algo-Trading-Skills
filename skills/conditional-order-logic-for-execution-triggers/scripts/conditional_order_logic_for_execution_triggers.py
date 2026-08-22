"""Client-side conditional order trigger engine.

Evaluates a Boolean condition tree (price / volume / time / cross-asset nodes)
against a market-state snapshot and releases a child order payload exactly once
when the tree evaluates to a *definite* TRUE.

Design notes that matter for live trading:

* **Three-valued logic.** Conditions return ``True`` / ``False`` / ``None``
  (UNKNOWN) internally. UNKNOWN means "the data needed to decide is missing or
  stale", which is not the same as FALSE. A trigger fires only on a definite
  TRUE, so missing data can never release an order. Collapsing UNKNOWN to FALSE
  at the leaves would be unsafe under negation: ``NOT(missing quote)`` would
  become TRUE and fire a live order on absent data.
* **Single fire under concurrency.** The DORMANT -> TRIGGERED transition is
  guarded by a lock, so two feed-handler threads delivering ticks concurrently
  cannot both release the same child order.
* **Staleness is opt-in but fail-safe.** When a condition is configured with
  ``max_quote_age_seconds``, a quote with a missing or too-old timestamp
  evaluates to UNKNOWN rather than to a value.

This engine simulates conditional orders on the client side. Native
broker/exchange-resident triggers (see SKILL.md, "When NOT to Use") survive a
client outage; this one does not.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# Market state is {symbol: {field: value}}. The reserved field below carries the
# quote's exchange/receipt timestamp as epoch seconds (UTC) and is required only
# when a condition is configured with ``max_quote_age_seconds``.
TIMESTAMP_FIELD = "timestamp"

MarketState = Mapping[str, Mapping[str, Any]]

# Comparison operators supported by value-comparing conditions. '==' is handled
# separately because exact float equality is not a usable trigger predicate.
_ORDERING_OPERATORS = frozenset({">=", "<=", ">", "<"})
_ALL_OPERATORS = _ORDERING_OPERATORS | {"=="}

STATUS_DORMANT = "DORMANT"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_CANCELLED = "CANCELLED"


def _validate_symbol(symbol: str, label: str = "symbol") -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"{label} must be a non-empty string, got {symbol!r}")
    return symbol


def _validate_field(field_name: str, label: str = "field") -> str:
    if not isinstance(field_name, str) or not field_name.strip():
        raise ValueError(f"{label} must be a non-empty string, got {field_name!r}")
    return field_name


def _validate_finite(value: Any, label: str) -> float:
    """Reject non-numeric, NaN and infinite thresholds at construction time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return numeric


def _validate_max_age(max_quote_age_seconds: Optional[float]) -> Optional[float]:
    if max_quote_age_seconds is None:
        return None
    age = _validate_finite(max_quote_age_seconds, "max_quote_age_seconds")
    if age <= 0:
        raise ValueError(f"max_quote_age_seconds must be > 0, got {max_quote_age_seconds!r}")
    return age


def _validate_operator(operator: str, allowed: frozenset = _ALL_OPERATORS) -> str:
    if operator not in allowed:
        raise ValueError(
            f"unsupported operator {operator!r}; supported operators are "
            f"{sorted(allowed)}"
        )
    return operator


def _compare(value: float, operator: str, target: float, tolerance: float) -> bool:
    if operator == ">=":
        return value >= target
    if operator == "<=":
        return value <= target
    if operator == ">":
        return value > target
    if operator == "<":
        return value < target
    # '==' — band comparison; ``tolerance`` is validated > 0 at construction.
    return abs(value - target) <= tolerance


def _now_epoch(now: Optional[float]) -> float:
    if now is None:
        return time.time()
    return _validate_finite(now, "now")


class BaseCondition(ABC):
    """Abstract base class for all conditional order trigger nodes.

    Subclasses implement :meth:`evaluate_tristate`. :meth:`evaluate` is the
    fail-safe boolean projection used by the trigger: UNKNOWN becomes ``False``
    so that absent or stale data never releases an order.
    """

    @abstractmethod
    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        """Return True / False, or None when the inputs are missing or stale."""

    def evaluate(self, market_state: MarketState, now: Optional[float] = None) -> bool:
        """Fail-safe evaluation: UNKNOWN is reported as ``False``."""
        return self.evaluate_tristate(market_state, now) is True


class _QuoteReadingCondition(BaseCondition):
    """Shared quote lookup with optional staleness enforcement."""

    def __init__(self, max_quote_age_seconds: Optional[float] = None) -> None:
        self.max_quote_age_seconds = _validate_max_age(max_quote_age_seconds)

    def _read(
        self,
        market_state: MarketState,
        symbol: str,
        field_name: str,
        now: Optional[float],
    ) -> Optional[float]:
        """Return the numeric quote value, or ``None`` when it is unusable."""
        if not isinstance(market_state, Mapping):
            raise ValueError(
                f"market_state must be a mapping of symbol -> fields, got "
                f"{type(market_state).__name__}"
            )
        quote = market_state.get(symbol)
        if not isinstance(quote, Mapping) or field_name not in quote:
            logger.debug("condition input unavailable: %s.%s", symbol, field_name)
            return None

        raw = quote[field_name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            logger.warning(
                "non-numeric value for %s.%s (%r); treating condition as UNKNOWN",
                symbol, field_name, raw,
            )
            return None
        value = float(raw)
        if not math.isfinite(value):
            logger.warning(
                "non-finite value for %s.%s (%r); treating condition as UNKNOWN",
                symbol, field_name, raw,
            )
            return None

        if self.max_quote_age_seconds is not None:
            timestamp = quote.get(TIMESTAMP_FIELD)
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                logger.warning(
                    "staleness checking enabled but %s has no numeric %r field; "
                    "treating condition as UNKNOWN",
                    symbol, TIMESTAMP_FIELD,
                )
                return None
            age = _now_epoch(now) - float(timestamp)
            if age < -self.max_quote_age_seconds:
                # A timestamp far in the future is never "old", so it would defeat
                # the staleness check entirely. The usual cause is a unit error
                # (milliseconds parsed as seconds), the second is host clock skew.
                logger.warning(
                    "quote timestamp for %s.%s is %.3fs in the future (unit error "
                    "or clock skew?); treating condition as UNKNOWN",
                    symbol, field_name, -age,
                )
                return None
            if age > self.max_quote_age_seconds:
                logger.warning(
                    "stale quote for %s.%s: age %.3fs exceeds %.3fs; "
                    "treating condition as UNKNOWN",
                    symbol, field_name, age, self.max_quote_age_seconds,
                )
                return None
        return value


class PriceCondition(_QuoteReadingCondition):
    """Compares one symbol's price field against a fixed threshold.

    ``field`` selects the trigger price type ('last', 'bid', 'ask', ...). This
    choice is a real execution decision, not a formatting detail: FIX
    ``TriggerPriceType(1107)`` and broker trigger-method settings expose the same
    switch, and a last-trade trigger and a bid trigger fire at different moments
    on the same tape. See references/standards.md.

    ``'=='`` requires an explicit positive ``tolerance``; exact float equality on
    a price feed is a trigger that effectively never fires.
    """

    def __init__(
        self,
        symbol: str,
        field: str,
        operator: str,
        target_value: float,
        max_quote_age_seconds: Optional[float] = None,
        tolerance: Optional[float] = None,
    ) -> None:
        super().__init__(max_quote_age_seconds)
        self.symbol = _validate_symbol(symbol)
        self.field = _validate_field(field)
        self.operator = _validate_operator(operator)
        self.target_value = _validate_finite(target_value, "target_value")
        if self.operator == "==":
            if tolerance is None:
                raise ValueError(
                    "operator '==' requires an explicit positive tolerance: exact "
                    "float equality against a price feed will effectively never "
                    "match. Use a band (e.g. tolerance=0.005) or an ordering "
                    "operator such as '>='."
                )
            self.tolerance = _validate_finite(tolerance, "tolerance")
            if self.tolerance <= 0:
                raise ValueError(f"tolerance must be > 0, got {tolerance!r}")
        else:
            if tolerance is not None:
                raise ValueError("tolerance is only meaningful for operator '=='")
            self.tolerance = 0.0

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        value = self._read(market_state, self.symbol, self.field, now)
        if value is None:
            return None
        return _compare(value, self.operator, self.target_value, self.tolerance)


class VolumeCondition(_QuoteReadingCondition):
    """Evaluates a size/volume field against a minimum threshold."""

    def __init__(
        self,
        symbol: str,
        field: str,
        min_volume: float,
        max_quote_age_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(max_quote_age_seconds)
        self.symbol = _validate_symbol(symbol)
        self.field = _validate_field(field)
        self.min_volume = _validate_finite(min_volume, "min_volume")
        if self.min_volume < 0:
            raise ValueError(f"min_volume must be >= 0, got {min_volume!r}")

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        value = self._read(market_state, self.symbol, self.field, now)
        if value is None:
            return None
        return value >= self.min_volume


class CrossAssetCondition(_QuoteReadingCondition):
    """Compares one instrument against a scaled reference instrument.

    Evaluates ``value(symbol.field) OP (ratio * value(reference.reference_field)
    + offset)``. Both quotes must be present (and fresh, when staleness checking
    is enabled); if either is missing the condition is UNKNOWN, so a dropped
    benchmark feed cannot silently turn a relative-value trigger into an
    outright one.
    """

    def __init__(
        self,
        symbol: str,
        field: str,
        operator: str,
        reference_symbol: str,
        reference_field: str,
        ratio: float = 1.0,
        offset: float = 0.0,
        max_quote_age_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(max_quote_age_seconds)
        self.symbol = _validate_symbol(symbol)
        self.field = _validate_field(field)
        self.operator = _validate_operator(operator, _ORDERING_OPERATORS)
        self.reference_symbol = _validate_symbol(reference_symbol, "reference_symbol")
        self.reference_field = _validate_field(reference_field, "reference_field")
        self.ratio = _validate_finite(ratio, "ratio")
        self.offset = _validate_finite(offset, "offset")

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        value = self._read(market_state, self.symbol, self.field, now)
        if value is None:
            return None
        reference = self._read(
            market_state, self.reference_symbol, self.reference_field, now
        )
        if reference is None:
            return None
        target = self.ratio * reference + self.offset
        if not math.isfinite(target):
            logger.warning(
                "cross-asset target for %s vs %s is non-finite; UNKNOWN",
                self.symbol, self.reference_symbol,
            )
            return None
        return _compare(value, self.operator, target, 0.0)


class TimeCondition(BaseCondition):
    """Wall-clock condition against a timezone-aware target instant.

    A naive ``datetime`` is rejected: interpreting one in the host's local zone
    is how a trigger meant for 15:50 New York fires at 15:50 UTC.
    """

    def __init__(self, operator: str, target_time: datetime) -> None:
        self.operator = _validate_operator(operator, frozenset({">=", "<=", ">", "<"}))
        if not isinstance(target_time, datetime):
            raise ValueError(f"target_time must be a datetime, got {target_time!r}")
        if target_time.tzinfo is None or target_time.utcoffset() is None:
            raise ValueError(
                "target_time must be timezone-aware (e.g. "
                "datetime(2026, 1, 2, 15, 50, tzinfo=ZoneInfo('America/New_York')))"
            )
        self.target_time = target_time
        self.target_epoch = target_time.timestamp()

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        return _compare(_now_epoch(now), self.operator, self.target_epoch, 0.0)


class _CompositeCondition(BaseCondition):
    def __init__(self, conditions: Sequence[BaseCondition]) -> None:
        if not isinstance(conditions, (list, tuple)):
            raise ValueError("conditions must be a list or tuple of BaseCondition")
        if not conditions:
            # all([]) is True: an empty AND gate would fire the child order on the
            # very first tick with no condition ever having been checked.
            raise ValueError(
                f"{type(self).__name__} requires at least one child condition"
            )
        for cond in conditions:
            if not isinstance(cond, BaseCondition):
                raise ValueError(f"child condition must be a BaseCondition, got {cond!r}")
        self.conditions: List[BaseCondition] = list(conditions)


class AndCondition(_CompositeCondition):
    """Kleene AND: FALSE dominates, otherwise any UNKNOWN yields UNKNOWN."""

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        unknown = False
        for cond in self.conditions:
            result = cond.evaluate_tristate(market_state, now)
            if result is False:
                return False
            if result is None:
                unknown = True
        return None if unknown else True


class OrCondition(_CompositeCondition):
    """Kleene OR: TRUE dominates, otherwise any UNKNOWN yields UNKNOWN."""

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        unknown = False
        for cond in self.conditions:
            result = cond.evaluate_tristate(market_state, now)
            if result is True:
                return True
            if result is None:
                unknown = True
        return None if unknown else False


class NotCondition(BaseCondition):
    """Kleene NOT: UNKNOWN stays UNKNOWN, so absent data cannot fire an order."""

    def __init__(self, condition: BaseCondition) -> None:
        if not isinstance(condition, BaseCondition):
            raise ValueError(f"condition must be a BaseCondition, got {condition!r}")
        self.condition = condition

    def evaluate_tristate(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[bool]:
        result = self.condition.evaluate_tristate(market_state, now)
        if result is None:
            return None
        return not result


@dataclass(frozen=True)
class ChildOrderPayload:
    """Order specification released when a trigger fires.

    Frozen so a payload handed to the OMS cannot be mutated behind the engine's
    back, and validated at construction so a sizing bug surfaces at registration
    rather than at the moment the trigger fires.
    """

    symbol: str
    side: str                          # 'BUY' or 'SELL'
    quantity: float
    order_type: str                    # 'LIMIT' or 'MARKET'
    price: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {self.side!r}")
        quantity = _validate_finite(self.quantity, "quantity")
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity!r}")
        if self.order_type not in ("LIMIT", "MARKET"):
            raise ValueError(
                f"order_type must be 'LIMIT' or 'MARKET', got {self.order_type!r}"
            )
        if self.order_type == "LIMIT":
            if self.price is None:
                raise ValueError("a LIMIT child order requires a price")
            _validate_finite(self.price, "price")
            if self.price <= 0:
                raise ValueError(f"price must be > 0, got {self.price!r}")


class ConditionalOrderTrigger:
    """A child order coupled with a Boolean condition tree.

    Enforces a single-fire DORMANT -> TRIGGERED transition. Evaluation and the
    state transition happen under one lock, so concurrent feed-handler threads
    cannot both release the child order.
    """

    def __init__(
        self,
        trigger_id: str,
        condition_tree: BaseCondition,
        child_order: ChildOrderPayload,
    ) -> None:
        self.trigger_id = _validate_symbol(trigger_id, "trigger_id")
        if not isinstance(condition_tree, BaseCondition):
            raise ValueError(
                f"condition_tree must be a BaseCondition, got {condition_tree!r}"
            )
        if not isinstance(child_order, ChildOrderPayload):
            raise ValueError(
                f"child_order must be a ChildOrderPayload, got {child_order!r}"
            )
        self.condition_tree = condition_tree
        self.child_order = child_order
        self._status = STATUS_DORMANT
        self._lock = threading.Lock()

    @property
    def status(self) -> str:
        return self._status

    def process_tick(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> Optional[ChildOrderPayload]:
        """Evaluate the tree; release the child order on the first definite TRUE."""
        with self._lock:
            if self._status != STATUS_DORMANT:
                return None
            if not self.condition_tree.evaluate(market_state, now):
                return None
            self._status = STATUS_TRIGGERED
            logger.info(
                "conditional order trigger %s FIRED: releasing %s %s %s",
                self.trigger_id,
                self.child_order.side,
                self.child_order.quantity,
                self.child_order.symbol,
            )
            return self.child_order

    def cancel(self) -> bool:
        """Cancel a dormant trigger. Returns False if it already fired."""
        with self._lock:
            if self._status != STATUS_DORMANT:
                logger.info(
                    "cancel ignored for trigger %s: status is %s",
                    self.trigger_id, self._status,
                )
                return False
            self._status = STATUS_CANCELLED
            logger.info("conditional order trigger %s CANCELLED", self.trigger_id)
            return True


class ConditionalOrderEngine:
    """Registry of conditional triggers with optional OCO grouping.

    ``process_tick`` evaluates every dormant trigger in registration order and
    returns the child orders released by this tick. Triggers registered with the
    same ``oco_group`` are one-cancels-the-other: the first to fire cancels its
    still-dormant siblings, so a bracket's stop and target cannot both reach the
    venue.
    """

    def __init__(self) -> None:
        self._triggers: Dict[str, ConditionalOrderTrigger] = {}
        self._oco_groups: Dict[str, List[str]] = {}
        self._group_of: Dict[str, str] = {}
        self._lock = threading.Lock()

    def register(
        self, trigger: ConditionalOrderTrigger, oco_group: Optional[str] = None
    ) -> None:
        if not isinstance(trigger, ConditionalOrderTrigger):
            raise ValueError(
                f"trigger must be a ConditionalOrderTrigger, got {trigger!r}"
            )
        with self._lock:
            if trigger.trigger_id in self._triggers:
                raise ValueError(
                    f"trigger_id {trigger.trigger_id!r} is already registered; "
                    "duplicate identifiers would make cancellation ambiguous"
                )
            self._triggers[trigger.trigger_id] = trigger
            if oco_group is not None:
                _validate_symbol(oco_group, "oco_group")
                self._oco_groups.setdefault(oco_group, []).append(trigger.trigger_id)
                self._group_of[trigger.trigger_id] = oco_group

    def get(self, trigger_id: str) -> Optional[ConditionalOrderTrigger]:
        with self._lock:
            return self._triggers.get(trigger_id)

    def cancel(self, trigger_id: str) -> bool:
        trigger = self.get(trigger_id)
        if trigger is None:
            return False
        return trigger.cancel()

    def process_tick(
        self, market_state: MarketState, now: Optional[float] = None
    ) -> List[ChildOrderPayload]:
        """Evaluate all dormant triggers against one market-state snapshot."""
        if not isinstance(market_state, Mapping):
            raise ValueError(
                f"market_state must be a mapping of symbol -> fields, got "
                f"{type(market_state).__name__}"
            )
        # Pin the evaluation instant so every trigger in this tick sees one clock.
        evaluation_time = _now_epoch(now)
        with self._lock:
            ordered = list(self._triggers.values())

        released: List[ChildOrderPayload] = []
        for trigger in ordered:
            payload = trigger.process_tick(market_state, evaluation_time)
            if payload is None:
                continue
            released.append(payload)
            self._cancel_oco_siblings(trigger.trigger_id)
        return released

    def _cancel_oco_siblings(self, trigger_id: str) -> None:
        with self._lock:
            group = self._group_of.get(trigger_id)
            siblings = list(self._oco_groups.get(group, [])) if group else []
        for sibling_id in siblings:
            if sibling_id == trigger_id:
                continue
            sibling = self.get(sibling_id)
            if sibling is not None and sibling.cancel():
                logger.info(
                    "OCO group %s: cancelled %s because %s fired",
                    group, sibling_id, trigger_id,
                )


__all__ = [
    "TIMESTAMP_FIELD",
    "STATUS_DORMANT",
    "STATUS_TRIGGERED",
    "STATUS_CANCELLED",
    "BaseCondition",
    "PriceCondition",
    "VolumeCondition",
    "CrossAssetCondition",
    "TimeCondition",
    "AndCondition",
    "OrCondition",
    "NotCondition",
    "ChildOrderPayload",
    "ConditionalOrderTrigger",
    "ConditionalOrderEngine",
]

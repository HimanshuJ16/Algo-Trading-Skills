"""
backtest-determinism-and-reproducibility: Master seed injection, deterministic
event-stream ordering, a simulated clock, and exact trade-log audit checksums.

What "bit-identical" means here
-------------------------------
Checksums are computed over the **exact IEEE-754 bit pattern** of every float,
via ``float.hex()``. This matters more than it sounds: rounding before hashing
silently erases the very signal this module exists to detect. The classic
signature of non-determinism is a summation-order difference —
``sum([0.1] * 10)`` is ``0.9999999999999999`` while ``0.1 * 10`` is ``1.0`` —
and any rounding to 6 decimal places reports those two runs as identical.

Set ``float_precision`` if you deliberately want tolerant comparison, but know
that you are trading away the ability to detect real divergence.

What this module cannot promise
-------------------------------
It removes the *controllable* sources of non-determinism and *detects*
divergence. It does not guarantee bit-identical results in general — that is
not achievable. PyTorch's own reproducibility guidance states that "completely
reproducible results are not guaranteed across PyTorch releases, individual
commits, or different platforms", and that results may differ between CPU and
GPU "even when using identical seeds". The same applies to BLAS thread counts,
SIMD paths, and library versions.

Determinism is therefore a property of a **pinned environment**: same platform,
same library versions, same thread configuration. Pin those, then use the audit
here to prove the runs actually matched.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence
import hashlib
import json
import logging
import math
import os
import random

logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_NUMPY = False

try:
    import torch
    HAS_TORCH = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_TORCH = False

__all__ = [
    "DeterminismError",
    "TradeSide",
    "TradeExecutionRecord",
    "DeterministicAuditReport",
    "SimulatedClock",
    "BacktestDeterminismEngine",
    "HAS_NUMPY",
    "HAS_TORCH",
]

_SORT_KEYS = ("timestamp", "symbol", "sequence_id")


class DeterminismError(ValueError):
    """Raised when input cannot be canonicalised deterministically."""


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def _exact_float(value: float, label: str, precision: Optional[int] = None) -> str:
    """Renders a float exactly and round-trippably.

    ``float.hex()`` preserves every bit, so two runs differing in the last ulp
    produce different strings. Decimal rounding would not.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeterminismError(f"{label} must be a real number, got {type(value).__name__}")
    if not math.isfinite(value):
        # NaN never equals itself, yet json.dumps renders every NaN as the same
        # token - so two corrupted runs would hash identically and be declared
        # reproducible. Reject rather than launder.
        raise DeterminismError(
            f"{label} is {value!r}; non-finite values cannot be audited "
            "(two NaN runs would hash identically and appear reproducible)"
        )
    if precision is not None:
        return format(round(float(value), precision), f".{precision}f")
    return float(value).hex()


@dataclass(frozen=True)
class TradeExecutionRecord:
    timestamp: float
    symbol: str
    action: str
    quantity: float
    price: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise DeterminismError("symbol must be a non-empty string")
        if not isinstance(self.action, str) or not self.action.strip():
            raise DeterminismError("action must be a non-empty string")
        for name in ("timestamp", "quantity", "price"):
            _exact_float(getattr(self, name), name)

    @property
    def side(self) -> TradeSide:
        """Parses the action into a side.

        Raises:
            DeterminismError: If the action is neither BUY nor SELL. Guessing
                would silently flip the sign of a cash flow.
        """
        normalised = self.action.strip().upper()
        try:
            return TradeSide(normalised)
        except ValueError as exc:
            raise DeterminismError(
                f"action {self.action!r} is not BUY or SELL; cannot determine cash-flow sign"
            ) from exc

    @property
    def signed_cash_flow(self) -> float:
        """Cash effect of the trade: negative for BUY, positive for SELL."""
        magnitude = self.quantity * self.price
        return -magnitude if self.side is TradeSide.BUY else magnitude


@dataclass(frozen=True)
class DeterministicAuditReport:
    seed: int
    total_trades: int
    net_cash_flow: float
    trade_log_checksum: str
    is_bit_identical: bool
    message: str
    first_divergence_index: Optional[int] = None


class SimulatedClock:
    """A monotonic clock driven by event timestamps, not the wall clock.

    Backtest logic that calls ``time.time()`` is non-reproducible by
    construction. Inject this instead of monkey-patching the ``time`` module —
    patching a global affects every library in the process, including ones whose
    correctness depends on real time.
    """

    def __init__(self, start_time: float = 0.0) -> None:
        _exact_float(start_time, "start_time")
        self._now = float(start_time)

    def now(self) -> float:
        return self._now

    def advance_to(self, timestamp: float) -> None:
        """Moves the clock forward to an event timestamp.

        Raises:
            DeterminismError: On a backwards jump, which means the event stream
                was not sorted and results would depend on arrival order.
        """
        _exact_float(timestamp, "timestamp")
        if timestamp < self._now:
            raise DeterminismError(
                f"clock cannot move backwards: {timestamp} < {self._now}. "
                "Sort the event stream before replaying it."
            )
        self._now = float(timestamp)


class BacktestDeterminismEngine:
    """Eliminates controllable non-determinism and audits runs for divergence.

    Args:
        master_seed: Seed applied to ``random``, NumPy's legacy global RNG, and
            torch when present.
        apply_seeds_on_init: Seeding mutates process-global RNG state. Left True
            for backwards compatibility; pass False to construct an audit-only
            engine without touching global state.
        float_precision: ``None`` (default) hashes exact float bits. An integer
            rounds to that many decimals first, which re-introduces false
            "identical" verdicts — see the module docstring.
    """

    def __init__(
        self,
        master_seed: int = 42,
        apply_seeds_on_init: bool = True,
        float_precision: Optional[int] = None,
    ) -> None:
        if isinstance(master_seed, bool) or not isinstance(master_seed, int):
            raise DeterminismError("master_seed must be an int")
        if float_precision is not None:
            if isinstance(float_precision, bool) or not isinstance(float_precision, int):
                raise DeterminismError("float_precision must be an int or None")
            if float_precision < 0:
                raise DeterminismError("float_precision must be non-negative")
            logger.warning(
                "float_precision=%s rounds before hashing; divergences smaller than "
                "1e-%s will be reported as bit-identical.",
                float_precision, float_precision,
            )
        self.master_seed = master_seed
        self.float_precision = float_precision
        if apply_seeds_on_init:
            self.apply_master_seeds(master_seed)

    # -------------------------------------------------------------- seeding

    @staticmethod
    def check_hash_seed(seed: int) -> bool:
        """Warns if PYTHONHASHSEED will not give reproducible set iteration.

        Hash randomisation is fixed at interpreter startup, so this can only be
        checked, never applied. Setting ``os.environ['PYTHONHASHSEED']`` from
        running code does nothing — a trap worth stating plainly, because the
        call looks like it works.

        Returns:
            True if the current setting yields reproducible str/bytes hashing.
        """
        current = os.environ.get("PYTHONHASHSEED")
        if current is None or current == "random":
            logger.warning(
                "Determinism warning: PYTHONHASHSEED is unset, so str/bytes hashing "
                "is randomised per process and set iteration order will vary between "
                "runs. Restart with PYTHONHASHSEED=%s (or 0). It cannot be set from "
                "inside a running interpreter.", seed,
            )
            return False
        logger.debug("PYTHONHASHSEED=%s: str/bytes hashing is reproducible.", current)
        return True

    @staticmethod
    def apply_master_seeds(seed: int = 42) -> None:
        """Seeds the process-global RNGs and reports what is NOT covered."""
        BacktestDeterminismEngine.check_hash_seed(seed)

        random.seed(seed)

        if HAS_NUMPY:
            # Legacy singleton RandomState only. NumPy documents this function as
            # legacy and recommends a dedicated Generator; code using
            # np.random.default_rng() is NOT affected by this call.
            np.random.seed(seed)

        if HAS_TORCH:  # pragma: no cover - optional dependency
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

        logger.info("Determinism engine: master seed set to %s.", seed)

    def make_numpy_generator(self) -> Any:
        """Returns a freshly seeded NumPy ``Generator``.

        ``apply_master_seeds`` reaches only NumPy's legacy global RandomState.
        Modern code calling ``np.random.default_rng()`` picks up OS entropy and
        stays non-deterministic no matter how often the global is seeded. Use
        this and thread it through explicitly.

        Raises:
            DeterminismError: If NumPy is not installed.
        """
        if not HAS_NUMPY:
            raise DeterminismError("NumPy is not installed")
        return np.random.default_rng(self.master_seed)

    # ---------------------------------------------------------------- sorting

    @staticmethod
    def sort_event_stream(raw_events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sorts events by ``(timestamp, symbol, sequence_id)``.

        Every key must be present. Defaulting a missing timestamp to 0.0 — as an
        earlier version did — silently moves that event to the front of the
        stream, corrupting the ordering this function exists to guarantee.

        Raises:
            DeterminismError: On a missing key, an unorderable value, or a
                duplicate sort key. Duplicates matter because Python's sort is
                stable: tied events would keep their input order, making the
                result depend on how the file happened to be read.
        """
        decorated = []
        for index, event in enumerate(raw_events):
            if not isinstance(event, dict):
                raise DeterminismError(
                    f"event {index} must be a dict, got {type(event).__name__}"
                )
            missing = [k for k in _SORT_KEYS if k not in event]
            if missing:
                raise DeterminismError(
                    f"event {index} is missing sort key(s) {missing}; refusing to "
                    "default them, which would silently reorder the stream"
                )
            timestamp = event["timestamp"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise DeterminismError(f"event {index} timestamp must be numeric")
            if not math.isfinite(timestamp):
                raise DeterminismError(f"event {index} timestamp is {timestamp!r}")
            sequence_id = event["sequence_id"]
            if isinstance(sequence_id, bool) or not isinstance(sequence_id, int):
                raise DeterminismError(f"event {index} sequence_id must be an int")
            decorated.append(
                ((float(timestamp), str(event["symbol"]).upper(), sequence_id), event)
            )

        keys = [d[0] for d in decorated]
        if len(set(keys)) != len(keys):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            raise DeterminismError(
                f"duplicate sort keys {duplicates}; ties would resolve by input order, "
                "which is not deterministic. Assign distinct sequence_id values."
            )

        decorated.sort(key=lambda pair: pair[0])
        return [event for _, event in decorated]

    # --------------------------------------------------------------- checksum

    def canonical_trade_payload(self, trades: Iterable[TradeExecutionRecord]) -> List[Dict[str, str]]:
        """Canonical, exactly round-trippable representation of a trade log."""
        payload = []
        for index, trade in enumerate(trades):
            if not isinstance(trade, TradeExecutionRecord):
                raise DeterminismError(
                    f"trade {index} must be a TradeExecutionRecord, "
                    f"got {type(trade).__name__}"
                )
            payload.append({
                "ts": _exact_float(trade.timestamp, f"trade[{index}].timestamp", self.float_precision),
                "sym": trade.symbol.strip().upper(),
                "act": trade.side.value,
                "qty": _exact_float(trade.quantity, f"trade[{index}].quantity", self.float_precision),
                "px": _exact_float(trade.price, f"trade[{index}].price", self.float_precision),
            })
        return payload

    def generate_trade_checksum(self, trades: Iterable[TradeExecutionRecord]) -> str:
        """SHA256 over the canonical trade log.

        This detects *accidental* divergence between runs. It is an unkeyed
        digest, so it proves nothing against deliberate tampering — anyone can
        recompute it. For an authenticated audit record see
        ``backtest-audit-trail-for-regulatory-review``.
        """
        dumped = json.dumps(
            self.canonical_trade_payload(trades),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ audit

    def audit_reproducibility(
        self,
        trades_run1: Sequence[TradeExecutionRecord],
        trades_run2: Sequence[TradeExecutionRecord],
    ) -> DeterministicAuditReport:
        """Compares two runs and locates the first point of divergence."""
        # Materialise first: the run is consumed twice (payload + cash flow), and
        # a generator would silently yield a zero cash flow on the second pass.
        trades_run1 = list(trades_run1)
        trades_run2 = list(trades_run2)
        payload1 = self.canonical_trade_payload(trades_run1)
        payload2 = self.canonical_trade_payload(trades_run2)
        checksum1 = hashlib.sha256(
            json.dumps(payload1, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        checksum2 = hashlib.sha256(
            json.dumps(payload2, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False).encode("utf-8")
        ).hexdigest()

        is_identical = checksum1 == checksum2
        first_divergence = None
        if not is_identical:
            # Point at the first differing trade so the failure is debuggable
            # rather than just a pair of mismatched hex strings.
            for index, (a, b) in enumerate(zip(payload1, payload2)):
                if a != b:
                    first_divergence = index
                    break
            if first_divergence is None:
                first_divergence = min(len(payload1), len(payload2))

        if is_identical:
            msg = (
                f"Reproducibility verified: both runs produced identical trade logs "
                f"(SHA256 {checksum1[:12]}..., {len(payload1)} trades)."
            )
            logger.info(msg)
        else:
            msg = (
                f"REPRODUCIBILITY BREACH: run1 {checksum1[:12]}... != run2 "
                f"{checksum2[:12]}...; first divergence at trade index {first_divergence} "
                f"(run1 has {len(payload1)} trades, run2 has {len(payload2)})."
            )
            logger.error(msg)

        return DeterministicAuditReport(
            seed=self.master_seed,
            total_trades=len(payload1),
            net_cash_flow=sum(t.signed_cash_flow for t in trades_run1),
            trade_log_checksum=checksum1,
            is_bit_identical=is_identical,
            message=msg,
            first_divergence_index=first_divergence,
        )

"""Queue position modelling for passive (resting) limit orders.

Estimates how much volume still rests *ahead* of our own order at its price
level in a **strict price-time (FIFO)** limit order book, converts that estimate
into a queue rank, and prices the probability that the order fills inside a
forward horizon.

The model is deliberately small and its assumptions are stated rather than
buried:

* **Fills consume the queue from the front.** Every share executed at our limit
  price is subtracted from the volume ahead of us. This is exactly true under
  price-time priority and false under pro-rata or priority-allocation matching
  (see ``When NOT to Use`` in ``SKILL.md``).
* **Cancellations are allocated by an assumed-uniform share, haircut.** A
  cancellation drawn uniformly at random from the *other* participants' resting
  volume sits ahead of us with probability ``Q_ahead / (Q_total - Q_our)``. The
  uniform assumption is known to be optimistic — empirically, orders later in
  the queue are cancelled more often — so the share is multiplied by
  ``Config.cancellation_share_alpha`` (< 1 credits fewer cancellations ahead,
  i.e. a more pessimistic queue estimate). See ``references/standards.md``.
* **Fill probability is a Poisson trade-count model, not a coverage ratio.**
  Trades at the level are modelled as a Poisson process of average trade size
  ``Config.average_trade_size``; the order fills completely once enough trades
  have arrived to clear ``Q_ahead + our_quantity``. A deterministic
  "expected volume / required volume" ratio is *not* a probability and reaches
  certainty for ordinary inputs; this module reports the required and expected
  volumes separately so that quantity is still available, without labelling it
  a probability.

Every numeric input is validated before any arithmetic. Non-finite values are
rejected rather than clamped: ``max(0.0, float('nan'))`` returns ``0.0`` in
CPython, so an unvalidated ``NaN`` volume-ahead would otherwise be reported as
front-of-queue with a certain fill — the most aggressive signal this module can
emit, produced by corrupt data.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")

# Above this expected trade count the exact Poisson summation is replaced by a
# normal approximation with a continuity correction. math.exp(-mu) underflows to
# 0.0 near mu = 745, which would silently turn the survival function into a
# constant 1.0; switching well below that keeps the recurrence in range.
_NORMAL_APPROXIMATION_MU = 500.0

# Beyond this many standard deviations above the mean the Poisson upper tail is
# not representable in float64. Short-circuiting also bounds the summation loop.
_TAIL_SIGMA_CUTOFF = 40.0


class QueuePositionError(ValueError):
    """Base class for every error raised by this module."""


class QueuePositionConfigurationError(QueuePositionError):
    """Raised when ``Config`` holds a value the model cannot be run with."""


class QueuePositionValidationError(QueuePositionError):
    """Raised when an order tracker or an observation is not usable."""


def _check_finite_number(value: object, field: str, error: type) -> float:
    """Coerce ``value`` to a finite float or raise ``error``.

    ``bool`` is rejected explicitly: it is a subclass of ``int``, so ``True``
    would otherwise be silently accepted as ``1.0``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error(f"{field} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise error(f"{field} must be finite, got {numeric!r}")
    return numeric


def _require_finite_derived(value: float, description: str) -> float:
    """Guard a derived quantity against overflow to +/-inf.

    Individually valid inputs can still produce a non-finite product or
    quotient — a fill rate and a horizon that are each finite may multiply to
    ``inf``, which would then divide to ``NaN`` and be reported as a
    probability. Raised rather than clamped, for the same reason the inputs are.
    """
    if not math.isfinite(value):
        raise QueuePositionValidationError(
            f"{description} overflowed to {value!r}; the supplied magnitudes "
            "cannot be combined in double precision"
        )
    return value


def _standard_normal_cdf(z: float) -> float:
    """Phi(z) for the standard normal, via the complementary error function."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def poisson_survival(k: int, mu: float) -> float:
    """Return ``P(N >= k)`` for ``N ~ Poisson(mu)``.

    Exact summation of the probability mass function for moderate ``mu``; a
    normal approximation with continuity correction above
    ``_NORMAL_APPROXIMATION_MU``, where the exact recurrence would underflow.
    The far upper tail short-circuits to ``0.0``, which also bounds the loop.
    """
    if k <= 0:
        return 1.0
    if mu <= 0.0:
        return 0.0
    if k > mu + _TAIL_SIGMA_CUTOFF * math.sqrt(mu) + _TAIL_SIGMA_CUTOFF:
        return 0.0
    if mu > _NORMAL_APPROXIMATION_MU:
        return _standard_normal_cdf((mu - k + 0.5) / math.sqrt(mu))

    term = math.exp(-mu)
    cumulative = term
    for i in range(1, k):
        term *= mu / i
        cumulative += term
    return min(1.0, max(0.0, 1.0 - cumulative))


@dataclass
class Config:
    """Model parameters. Every one of these is a calibration input, not a fact.

    Attributes:
        cancellation_share_alpha: Haircut in ``[0, 1]`` applied to the assumed
            uniform share of cancellations falling ahead of us. ``1.0`` is the
            pure uniform-cancellation model; the ``0.5`` default is an
            uncalibrated pessimistic prior reflecting that cancellations skew
            toward the back of the queue. Calibrate per venue and instrument.
        average_order_size: Mean resting order size at the level, in the
            instrument's quantity units. Used only to turn a volume-ahead
            figure into a queue *rank*.
        average_trade_size: Mean executed trade size at the level, in the same
            units. Sets the Poisson trade-count scale for fill probability.
        front_of_queue_tolerance: Volume ahead at or below which the order is
            treated as front-of-queue, absorbing sub-unit floating-point
            residue from the subtraction chain.
    """

    cancellation_share_alpha: float = 0.5
    average_order_size: float = 100.0
    average_trade_size: float = 100.0
    front_of_queue_tolerance: float = 1e-9

    def __post_init__(self) -> None:
        alpha = _check_finite_number(
            self.cancellation_share_alpha,
            "cancellation_share_alpha",
            QueuePositionConfigurationError,
        )
        if not 0.0 <= alpha <= 1.0:
            raise QueuePositionConfigurationError(
                f"cancellation_share_alpha must lie in [0.0, 1.0], got {alpha!r}"
            )
        self.cancellation_share_alpha = alpha

        for field in ("average_order_size", "average_trade_size"):
            value = _check_finite_number(
                getattr(self, field), field, QueuePositionConfigurationError
            )
            if value <= 0.0:
                raise QueuePositionConfigurationError(
                    f"{field} must be strictly positive, got {value!r}"
                )
            setattr(self, field, value)

        tolerance = _check_finite_number(
            self.front_of_queue_tolerance,
            "front_of_queue_tolerance",
            QueuePositionConfigurationError,
        )
        if tolerance < 0.0:
            raise QueuePositionConfigurationError(
                f"front_of_queue_tolerance must be non-negative, got {tolerance!r}"
            )
        self.front_of_queue_tolerance = tolerance


@dataclass
class PassiveOrderTracker:
    """One resting limit order and the state of its price level at entry.

    ``initial_queue_ahead`` and ``total_level_volume`` are both measured *at the
    moment the order joined the queue*. ``total_level_volume`` includes our own
    ``our_quantity``, so it must be at least ``initial_queue_ahead +
    our_quantity``; anything less describes a book that cannot exist.
    """

    order_id: str
    side: str
    price: float
    our_quantity: float
    initial_queue_ahead: float
    total_level_volume: float


@dataclass
class QueuePositionReport:
    """Estimated queue state for one order at one point in time.

    ``fill_probability`` is the probability of a **complete** fill within the
    horizon; ``partial_fill_probability`` is the probability of at least one
    share executing. Both are model outputs under the assumptions in the module
    docstring, not measured frequencies.
    """

    order_id: str
    side: str
    price: float
    our_quantity: float
    current_queue_ahead: float
    estimated_queue_rank: int
    fill_probability: float
    is_front_of_queue: bool
    status: str
    audit_notes: str
    # Appended after audit_notes so that positional construction of the fields
    # above keeps working for any existing caller.
    partial_fill_probability: float = 0.0
    cancellations_credited_ahead: float = 0.0
    expected_level_volume: float = 0.0
    volume_required_for_full_fill: float = 0.0


class QueuePositionModelEngine:
    """Estimates FIFO queue position and fill probability for resting orders.

    The engine holds no per-order state. ``update_queue_position`` recomputes
    the estimate from the tracker's entry-time snapshot given **cumulative**
    volumes observed since entry — it does not accumulate across calls.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        if config is not None and not isinstance(config, Config):
            raise QueuePositionConfigurationError(
                f"config must be a Config instance, got {type(config).__name__}"
            )
        self.config = config or Config()

    @staticmethod
    def _validate_tracker(tracker: PassiveOrderTracker) -> str:
        if not isinstance(tracker, PassiveOrderTracker):
            raise QueuePositionValidationError(
                f"tracker must be a PassiveOrderTracker, got {type(tracker).__name__}"
            )
        if not isinstance(tracker.order_id, str) or not tracker.order_id.strip():
            raise QueuePositionValidationError("order_id must be a non-empty string")

        if not isinstance(tracker.side, str):
            raise QueuePositionValidationError(
                f"side must be a string, got {type(tracker.side).__name__}"
            )
        side = tracker.side.strip().upper()
        if side not in VALID_SIDES:
            raise QueuePositionValidationError(
                f"side must be one of {VALID_SIDES}, got {tracker.side!r}"
            )

        price = _check_finite_number(
            tracker.price, "price", QueuePositionValidationError
        )
        if price <= 0.0:
            raise QueuePositionValidationError(
                f"price must be strictly positive, got {price!r}"
            )

        our_quantity = _check_finite_number(
            tracker.our_quantity, "our_quantity", QueuePositionValidationError
        )
        if our_quantity <= 0.0:
            raise QueuePositionValidationError(
                f"our_quantity must be strictly positive, got {our_quantity!r}"
            )

        queue_ahead = _check_finite_number(
            tracker.initial_queue_ahead,
            "initial_queue_ahead",
            QueuePositionValidationError,
        )
        if queue_ahead < 0.0:
            raise QueuePositionValidationError(
                f"initial_queue_ahead must be non-negative, got {queue_ahead!r}"
            )

        total_level_volume = _check_finite_number(
            tracker.total_level_volume,
            "total_level_volume",
            QueuePositionValidationError,
        )
        # total_level_volume includes our own resting quantity by definition.
        minimum_total = queue_ahead + our_quantity
        if total_level_volume < minimum_total * (1.0 - 1e-9):
            raise QueuePositionValidationError(
                f"total_level_volume ({total_level_volume!r}) is below "
                f"initial_queue_ahead + our_quantity ({minimum_total!r}); the "
                "level snapshot is internally inconsistent"
            )
        return side

    def update_queue_position(
        self,
        tracker: PassiveOrderTracker,
        accumulated_fills: float,
        accumulated_cancellations: float,
        time_horizon_sec: float = 5.0,
        historical_fill_rate_per_sec: float = 50.0,
    ) -> QueuePositionReport:
        """Re-estimate volume ahead, queue rank and fill probability.

        Args:
            tracker: Entry-time snapshot of the order and its price level.
            accumulated_fills: Volume executed at ``tracker.price`` **since the
                order joined the queue**, cumulative — not the increment since
                the previous call. Passing per-tick increments understates queue
                progress on every call after the first.
            accumulated_cancellations: Volume cancelled at ``tracker.price``
                since entry, cumulative, on the same basis.
            time_horizon_sec: Forward horizon for the fill probabilities.
            historical_fill_rate_per_sec: Expected executed volume per second at
                this price level, in the instrument's quantity units.

        Returns:
            A :class:`QueuePositionReport`.

        Raises:
            QueuePositionValidationError: If the tracker or any observation is
                unusable. Nothing is estimated from unvalidated input.
        """
        side = self._validate_tracker(tracker)

        fills = _check_finite_number(
            accumulated_fills, "accumulated_fills", QueuePositionValidationError
        )
        if fills < 0.0:
            raise QueuePositionValidationError(
                f"accumulated_fills must be non-negative, got {fills!r}"
            )

        cancellations = _check_finite_number(
            accumulated_cancellations,
            "accumulated_cancellations",
            QueuePositionValidationError,
        )
        if cancellations < 0.0:
            raise QueuePositionValidationError(
                f"accumulated_cancellations must be non-negative, got {cancellations!r}"
            )

        horizon = _check_finite_number(
            time_horizon_sec, "time_horizon_sec", QueuePositionValidationError
        )
        if horizon <= 0.0:
            raise QueuePositionValidationError(
                f"time_horizon_sec must be strictly positive, got {horizon!r}"
            )

        fill_rate = _check_finite_number(
            historical_fill_rate_per_sec,
            "historical_fill_rate_per_sec",
            QueuePositionValidationError,
        )
        if fill_rate < 0.0:
            raise QueuePositionValidationError(
                f"historical_fill_rate_per_sec must be non-negative, got {fill_rate!r}"
            )

        config = self.config

        # 1. Executions at our limit price consume the queue strictly from the
        #    front under price-time priority.
        queue_after_fills = max(0.0, tracker.initial_queue_ahead - fills)

        # 2. Cancellations are split by the assumed-uniform share of the *other*
        #    participants' resting volume that sits ahead of us, then haircut.
        #    The denominator is the entry-time snapshot: it ignores both the
        #    depletion caused by the fills already credited (which would raise
        #    the ahead-share) and any new volume joining behind us (which would
        #    lower it), so the sign of the approximation error is not fixed.
        other_resting_volume = tracker.total_level_volume - tracker.our_quantity
        if other_resting_volume > 0.0:
            uniform_share = min(1.0, queue_after_fills / other_resting_volume)
        else:
            uniform_share = 0.0
        cancellations_ahead = min(
            queue_after_fills,
            cancellations * uniform_share * config.cancellation_share_alpha,
        )

        current_queue_ahead = max(0.0, queue_after_fills - cancellations_ahead)
        is_front_of_queue = current_queue_ahead <= config.front_of_queue_tolerance

        # 3. Rank: a partially-consumed order ahead of us still blocks us, so the
        #    count of orders ahead rounds up, not down.
        orders_ahead = math.ceil(
            _require_finite_derived(
                current_queue_ahead / config.average_order_size,
                "queue ahead divided by average_order_size",
            )
        )
        estimated_rank = orders_ahead + 1

        # 4. Fill probability under a Poisson trade-count model.
        expected_level_volume = _require_finite_derived(
            fill_rate * horizon, "historical_fill_rate_per_sec * time_horizon_sec"
        )
        expected_trades = _require_finite_derived(
            expected_level_volume / config.average_trade_size,
            "expected volume divided by average_trade_size",
        )
        volume_required_for_full_fill = _require_finite_derived(
            current_queue_ahead + tracker.our_quantity,
            "queue ahead plus our_quantity",
        )

        trades_for_full_fill = math.ceil(
            _require_finite_derived(
                volume_required_for_full_fill / config.average_trade_size,
                "volume required for a full fill divided by average_trade_size",
            )
        )
        trades_for_first_share = (
            math.floor(
                _require_finite_derived(
                    current_queue_ahead / config.average_trade_size,
                    "queue ahead divided by average_trade_size",
                )
            )
            + 1
        )

        fill_probability = poisson_survival(trades_for_full_fill, expected_trades)
        partial_fill_probability = poisson_survival(
            trades_for_first_share, expected_trades
        )

        status = "FRONT_OF_QUEUE" if is_front_of_queue else "QUEUE_PRIORITY_TRACKING"
        notes = (
            f"QUEUE POSITION REPORT [{tracker.order_id} ({side} @ "
            f"{tracker.price:.4f}) - {status}]: Init Ahead = "
            f"{tracker.initial_queue_ahead:,.0f}, Current Ahead = "
            f"{current_queue_ahead:,.0f} (fills {fills:,.0f}, cancels credited "
            f"ahead {cancellations_ahead:,.0f} of {cancellations:,.0f}), "
            f"Est Rank = #{estimated_rank}, P(full fill | {horizon:g}s) = "
            f"{fill_probability * 100.0:.1f}%, P(any fill) = "
            f"{partial_fill_probability * 100.0:.1f}%."
        )
        logger.info(notes)

        return QueuePositionReport(
            order_id=tracker.order_id,
            side=side,
            price=float(tracker.price),
            our_quantity=float(tracker.our_quantity),
            current_queue_ahead=current_queue_ahead,
            estimated_queue_rank=estimated_rank,
            fill_probability=fill_probability,
            is_front_of_queue=is_front_of_queue,
            status=status,
            audit_notes=notes,
            partial_fill_probability=partial_fill_probability,
            cancellations_credited_ahead=cancellations_ahead,
            expected_level_volume=expected_level_volume,
            volume_required_for_full_fill=volume_required_for_full_fill,
        )

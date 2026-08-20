"""
broker-side-order-throttle-detection:
Detection of *undeclared* broker-side order throttling from order-acknowledgment
round-trip time (ACK RTT), with AIMD-style adaptive dispatch backoff.

What "silent" throttling means here
-----------------------------------
Some venues signal congestion explicitly and some do not, and the two cases need
opposite handling:

  - **Explicit.** Binance returns HTTP 429 when a request rate limit is broken and
    HTTP 418 once an IP is auto-banned for continuing to send after 429s, with a
    ``Retry-After`` header giving the wait in seconds. Interactive Brokers' Web API
    likewise returns 429 and may place the offending IP in a 10-minute penalty box.
    When the venue states the answer, obey the venue. Do not infer a backoff from
    latency and do not let this detector override ``Retry-After``.
  - **Silent.** The TWS API "is designed to accept up to fifty messages per second
    coming from the client side"; beyond that, messages are queued and delayed
    rather than rejected, and the ``+PACEAPI`` connect option makes TWS pace the
    client at 50/s instead of disconnecting it. Nothing is returned to the caller to
    say this is happening - it surfaces only as ACK RTT rising.

This module targets the second case. It is an inference from a latency signal, not a
reading of a documented limit, and it is therefore always weaker evidence than an
explicit venue response.

Scope limits the caller must respect
------------------------------------
  - **It observes; it does not enforce.** ``recommended_backoff_ms`` is advice for the
    dispatch loop. It is not a maximum-message-limit control and must not be used as
    one. MiFID II RTS 6 Article 15(1)(d) requires "maximum messages limits, which
    prevent sending an excessive number of messages to order books" as a *pre-trade*
    control; that is a hard counter against a known limit, which is the job of
    ``matching-engine-throttle-and-message-gapping-detection``. Both are needed;
    neither substitutes for the other.
  - **An ACK that never arrives produces no sample.** A detector fed only by completed
    ACKs cannot see the worst throttle, which is silence. Callers must use
    ``register_order_submission`` + ``sweep_pending_acks`` for that; RTT statistics
    alone are survivorship-biased. See ``ThrottleState.ACK_TIMEOUT``.
  - **Latency conflates every hop.** A rise in ACK RTT is consistent with broker
    queuing, but also with local GC pauses, NIC saturation, a congested uplink or a
    venue-side matching engine slowdown. This module cannot attribute the delay; it
    establishes only that dispatch should slow down until it clears.
  - The thresholds are deployment-calibrated defaults, not standards. No regulator,
    exchange or broker defines a "500 ms means throttled" rule. Calibrate
    ``max_absolute_rtt_ms`` against your own measured ACK distribution before relying
    on it. See ``references/standards.md``.

Statistical method
------------------
The baseline is an exponentially weighted mean and variance using the recurrence in
Finch (2009), "Incremental calculation of weighted mean and variance", equation 143
and its accompanying code form::

    diff := x - mean
    incr := alpha * diff
    mean := mean + incr
    variance := (1 - alpha) * (variance + diff * incr)

This is the *exponentially weighted* estimator, not Welford's algorithm; Welford
(Knuth, TAOCP vol. 2, section 4.2.2) is the equal-weight incremental variance and has
no forgetting factor.

Samples classified ``SILENT_THROTTLE`` are deliberately **excluded** from the
baseline. Admitting them lets a sustained throttle train the baseline onto itself,
after which the anomaly test goes quiet while the throttle is still in force - see
``rebaseline_after_consecutive`` for the trade-off this creates and how to opt out.

Backoff follows the AIMD control law of Chiu & Jain (1989), "Analysis of the increase
and decrease algorithms for congestion avoidance in computer networks", Computer
Networks and ISDN Systems 17(1): the *dispatch rate* is decreased multiplicatively on
a congestion signal and increased additively otherwise, expressed here as the inverse
movement of a delay.
"""
import logging
import math
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ThrottleDataError(ValueError):
    """
    Raised when a latency sample or configuration value is unusable.

    NaN is the specific hazard this guards against. Every comparison against NaN is
    False, so a NaN RTT walks past the absolute-floor test *and* the z-score test and
    lands in the healthy branch, and one NaN folded into the baseline makes the EWMA
    NaN permanently - the detector then reports NORMAL for the rest of the process
    lifetime no matter how badly the broker is throttling. A detector that is silently
    dead is worse than one that raises, so unusable input raises rather than defaulting.
    """
    pass


def _require_finite(name: str, value: float, *, minimum: Optional[float] = None,
                    maximum: Optional[float] = None) -> float:
    """Validate a numeric input, rejecting NaN/Inf and out-of-range values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThrottleDataError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ThrottleDataError(
            f"{name} must be finite, got {value!r}. Refusing to update the latency "
            f"baseline from an unusable timestamp; treat this as a clock or feed fault."
        )
    if minimum is not None and value < minimum:
        raise ThrottleDataError(f"{name} must be >= {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise ThrottleDataError(f"{name} must be <= {maximum}, got {value!r}")
    return float(value)


class ThrottleState(str, Enum):
    """
    Ordered by severity. ``WARMUP`` and ``ACK_TIMEOUT`` are additions to the original
    three-state model; callers switching on this enum must handle them.
    """

    WARMUP = "WARMUP"
    NORMAL = "NORMAL"
    ELEVATED_LATENCY = "ELEVATED_LATENCY"
    SILENT_THROTTLE = "SILENT_THROTTLE"
    ACK_TIMEOUT = "ACK_TIMEOUT"


@dataclass(frozen=True)
class OrderACKSample:
    """One completed submit/acknowledge pair."""

    order_id: str
    submission_time: float
    ack_time: float

    def __post_init__(self) -> None:
        _require_finite("submission_time", self.submission_time)
        _require_finite("ack_time", self.ack_time)
        if self.ack_time < self.submission_time:
            raise ThrottleDataError(
                f"ack_time ({self.ack_time!r}) precedes submission_time "
                f"({self.submission_time!r}) for order {self.order_id!r}. A negative RTT "
                f"means a non-monotonic clock or an out-of-order callback, not a fast "
                f"broker. Clamping it to 0 ms would drag the baseline down and make "
                f"subsequent healthy ACKs look anomalous, so it is rejected instead. "
                f"Timestamp both ends with time.monotonic() from a single process."
            )
        # Two individually finite timestamps can still overflow to inf once scaled to
        # milliseconds. An inf RTT would classify as a throttle (fail-safe) but would
        # also leak into the report and the log line, where it is not valid JSON.
        _require_finite("rtt_ms", self.rtt_ms)

    @property
    def rtt_ms(self) -> float:
        return (self.ack_time - self.submission_time) * 1000.0


@dataclass
class ThrottleStatusReport:
    """
    Outcome of classifying one sample.

    ``ewma_rtt_ms`` / ``ewmsd_rtt_ms`` are the baseline **the decision was taken
    against** (i.e. before this sample was admitted, if it was admitted at all), so
    that ``z_score`` is reproducible from the reported values. The original
    implementation reported a post-update mean alongside a pre-update deviation, which
    could not be reconciled.
    """

    state: ThrottleState
    latest_rtt_ms: float
    ewma_rtt_ms: float
    ewmsd_rtt_ms: float
    recommended_backoff_ms: float
    is_throttled: bool
    summary: str
    z_score: float = 0.0
    order_id: str = ""
    baseline_sample_count: int = 0
    baseline_admitted: bool = False


@dataclass
class _Baseline:
    """Exponentially weighted mean/variance state (Finch 2009, eq. 143)."""

    alpha: float
    mean: float = 0.0
    variance: float = 0.0
    count: int = 0

    def update(self, x: float) -> None:
        if self.count == 0:
            self.mean = x
            self.variance = 0.0
        else:
            diff = x - self.mean
            incr = self.alpha * diff
            self.mean += incr
            self.variance = (1.0 - self.alpha) * (self.variance + diff * incr)
        self.count += 1

    def sigma(self, min_variance: float) -> float:
        """
        Standard deviation with the variance floor applied *before* the square root.

        ``sqrt(max(var, floor))`` is what ``references/workflows.md`` documents and what
        the ``min_variance_clamp`` name implies. The previous code computed
        ``max(sqrt(var), floor)``, which agrees only at the default floor of 1.0 and
        diverges for every other value an operator might configure.
        """
        return math.sqrt(max(self.variance, min_variance))


class OrderThrottleDetector:
    """
    Classifies order ACK latency and recommends a dispatch backoff.

    Thread-safe: broker SDKs deliver acknowledgments on their own callback threads, so
    every mutation of the baseline, the backoff and the pending-order table is taken
    under a single re-entrant lock. Without it, two concurrent ACKs read-modify-write
    ``current_backoff_ms`` and one update is lost - on a live order path that silently
    discards a congestion response.
    """

    def __init__(
        self,
        alpha: float = 0.1,
        z_score_threshold: float = 3.0,
        max_absolute_rtt_ms: float = 500.0,
        min_variance_clamp: float = 1.0,
        min_backoff_ms: float = 10.0,
        max_backoff_ms: float = 2000.0,
        elevated_z_threshold: float = 1.0,
        backoff_multiplier: float = 2.0,
        backoff_additive_decrease_ms: float = 20.0,
        elevated_increment_ms: float = 10.0,
        min_samples_for_detection: int = 20,
        ack_timeout_ms: float = 5000.0,
        rebaseline_after_consecutive: int = 0,
    ):
        """
        :param alpha: EWMA smoothing factor, 0 < alpha <= 1. Higher adapts faster and
            tolerates more jitter as "normal"; lower holds a longer memory.
        :param z_score_threshold: Deviations above baseline that mark SILENT_THROTTLE.
        :param max_absolute_rtt_ms: Baseline-independent ceiling. A deployment-calibrated
            default, not a standard - see the module docstring.
        :param min_variance_clamp: Floor on the *variance* in ms^2, applied before the
            square root. Prevents a deterministic network driving sigma to ~0, where 1 ms
            of jitter would score an unbounded z.
        :param min_backoff_ms: Floor applied when a penalty is active. Backoff is 0 when
            healthy; this bounds it from below only once something has been detected.
        :param max_backoff_ms: Ceiling on the recommended delay.
        :param elevated_z_threshold: Deviations above baseline that mark ELEVATED_LATENCY.
        :param backoff_multiplier: AIMD multiplicative factor on a congestion signal.
        :param backoff_additive_decrease_ms: AIMD additive decay per healthy ACK.
        :param elevated_increment_ms: Small additive penalty on ELEVATED_LATENCY.
        :param min_samples_for_detection: Admitted samples required before the z-test is
            trusted. Until then the state is WARMUP and only ``max_absolute_rtt_ms`` and
            ACK timeouts can raise an alarm - a z-score computed against a one-sample
            baseline is noise, and if that one sample landed inside a throttle the
            poisoned baseline would otherwise be the reference for the whole session.
        :param ack_timeout_ms: Age at which a registered, unacknowledged order is reported
            ACK_TIMEOUT by ``sweep_pending_acks``.
        :param rebaseline_after_consecutive: If > 0, re-anchor the baseline onto the new
            latency level after this many consecutive throttled samples, on the theory
            that the shift is permanent (a re-route, a venue migration) rather than
            congestion. **Disabled by default**: a risk control should keep flagging and
            keep backing off until a human decides the new level is acceptable. Enabling
            it restores automatic recovery at the cost of eventually going quiet during a
            genuine sustained throttle. Every re-anchor is logged at WARNING.
        """
        self.alpha = _require_finite("alpha", alpha, minimum=0.0, maximum=1.0)
        if self.alpha == 0.0:
            raise ThrottleDataError("alpha must be > 0; alpha=0 freezes the baseline forever.")
        self.z_score_threshold = _require_finite(
            "z_score_threshold", z_score_threshold, minimum=0.0)
        self.max_absolute_rtt_ms = _require_finite(
            "max_absolute_rtt_ms", max_absolute_rtt_ms, minimum=0.0)
        self.min_variance_clamp = _require_finite(
            "min_variance_clamp", min_variance_clamp, minimum=0.0)
        if self.min_variance_clamp == 0.0:
            raise ThrottleDataError(
                "min_variance_clamp must be > 0; a zero floor allows division by a zero "
                "standard deviation on a deterministic network."
            )
        self.min_backoff_ms = _require_finite("min_backoff_ms", min_backoff_ms, minimum=0.0)
        self.max_backoff_ms = _require_finite("max_backoff_ms", max_backoff_ms, minimum=0.0)
        if self.max_backoff_ms < self.min_backoff_ms:
            raise ThrottleDataError(
                f"max_backoff_ms ({self.max_backoff_ms}) must be >= min_backoff_ms "
                f"({self.min_backoff_ms})."
            )
        self.elevated_z_threshold = _require_finite(
            "elevated_z_threshold", elevated_z_threshold, minimum=0.0)
        if self.elevated_z_threshold > self.z_score_threshold:
            raise ThrottleDataError(
                f"elevated_z_threshold ({self.elevated_z_threshold}) must be <= "
                f"z_score_threshold ({self.z_score_threshold}); otherwise the "
                f"ELEVATED_LATENCY band is empty and never reported."
            )
        self.backoff_multiplier = _require_finite(
            "backoff_multiplier", backoff_multiplier, minimum=1.0)
        self.backoff_additive_decrease_ms = _require_finite(
            "backoff_additive_decrease_ms", backoff_additive_decrease_ms, minimum=0.0)
        self.elevated_increment_ms = _require_finite(
            "elevated_increment_ms", elevated_increment_ms, minimum=0.0)
        if not isinstance(min_samples_for_detection, int) or isinstance(
                min_samples_for_detection, bool) or min_samples_for_detection < 1:
            raise ThrottleDataError(
                f"min_samples_for_detection must be an int >= 1, got "
                f"{min_samples_for_detection!r}"
            )
        self.min_samples_for_detection = min_samples_for_detection
        self.ack_timeout_ms = _require_finite("ack_timeout_ms", ack_timeout_ms, minimum=0.0)
        if not isinstance(rebaseline_after_consecutive, int) or isinstance(
                rebaseline_after_consecutive, bool) or rebaseline_after_consecutive < 0:
            raise ThrottleDataError(
                f"rebaseline_after_consecutive must be an int >= 0, got "
                f"{rebaseline_after_consecutive!r}"
            )
        self.rebaseline_after_consecutive = rebaseline_after_consecutive

        self._lock = threading.RLock()
        self._baseline = _Baseline(alpha=self.alpha)
        self.current_backoff_ms = 0.0
        self._consecutive_throttled = 0
        self._throttled_sum_ms = 0.0
        self._pending: Dict[str, float] = {}

    # ------------------------------------------------------------------ properties

    @property
    def initialized(self) -> bool:
        """True once at least one sample has been admitted to the baseline."""
        with self._lock:
            return self._baseline.count > 0

    @property
    def ewma_rtt(self) -> float:
        with self._lock:
            return self._baseline.mean

    @property
    def ewmvar_rtt(self) -> float:
        with self._lock:
            return self._baseline.variance

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._baseline.count

    @property
    def pending_order_ids(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._pending)

    # ------------------------------------------------------------------ public API

    def register_order_submission(self, order_id: str, submission_time: float) -> None:
        """
        Record that an order was dispatched and is awaiting acknowledgment.

        Optional but strongly recommended: without it the detector only ever sees orders
        that *were* acknowledged, which is precisely the population that was not throttled
        into silence. ``sweep_pending_acks`` needs this table to see that silence.
        """
        _require_finite("submission_time", submission_time)
        if not order_id:
            raise ThrottleDataError("order_id must be a non-empty string.")
        with self._lock:
            self._pending[order_id] = float(submission_time)

    def record_order_ack(self, order_id: str, submission_time: float,
                         ack_time: float) -> ThrottleStatusReport:
        """
        Classify one acknowledged order and return the resulting status.

        Raises ``ThrottleDataError`` on a non-finite or time-reversed timestamp rather
        than silently absorbing it into the baseline.
        """
        sample = OrderACKSample(order_id, submission_time, ack_time)
        rtt = sample.rtt_ms

        with self._lock:
            self._pending.pop(order_id, None)
            return self._classify(order_id, rtt, timed_out=False)

    def sweep_pending_acks(self, now: float) -> List[ThrottleStatusReport]:
        """
        Report every registered order whose acknowledgment is overdue at ``now``.

        A silent throttle severe enough to stall acknowledgments entirely generates no
        RTT samples at all, so ``record_order_ack`` can never see it; this sweep is the
        only path that does. Call it from the dispatch loop or a timer at least as often
        as ``ack_timeout_ms``.

        Each order is reported once and then dropped from the pending table, so repeated
        sweeps do not re-escalate the backoff for the same stalled order. A late ACK
        arriving afterwards is still classified normally (and will read as a large RTT).
        """
        _require_finite("now", now)
        reports: List[ThrottleStatusReport] = []
        with self._lock:
            overdue = []
            for oid, sub in self._pending.items():
                age_ms = (now - sub) * 1000.0
                if age_ms < 0.0:
                    # The sweep clock is behind an order's own submission stamp, so ages
                    # cannot be trusted and this order would never mature. Warn rather
                    # than raise: one bad entry must not abort the whole sweep.
                    logger.warning(
                        "Pending order %s has submission_time %r ahead of sweep time %r; "
                        "its ACK timeout cannot be evaluated. Timestamp submissions and "
                        "sweeps from the same monotonic clock.", oid, sub, now,
                    )
                    continue
                if age_ms >= self.ack_timeout_ms:
                    overdue.append((oid, sub))
            for order_id, submitted_at in overdue:
                del self._pending[order_id]
                age_ms = (now - submitted_at) * 1000.0
                reports.append(self._classify(order_id, age_ms, timed_out=True))
        return reports

    def reset(self) -> None:
        """Clear all state. Use at session boundaries, where a baseline is not carried over."""
        with self._lock:
            self._baseline = _Baseline(alpha=self.alpha)
            self.current_backoff_ms = 0.0
            self._consecutive_throttled = 0
            self._throttled_sum_ms = 0.0
            self._pending.clear()

    # ------------------------------------------------------------------ internals

    def _classify(self, order_id: str, rtt_ms: float, *, timed_out: bool) -> ThrottleStatusReport:
        """Caller must hold ``self._lock``."""
        baseline_mean = self._baseline.mean
        baseline_sigma = (
            self._baseline.sigma(self.min_variance_clamp) if self._baseline.count else 0.0
        )
        warm = self._baseline.count >= self.min_samples_for_detection

        # Every threshold below is evaluated against the pre-update baseline, so the
        # reported z_score is reproducible from the reported mean and sigma. Mixing a
        # post-update mean with a pre-update sigma (as the original did) made the
        # ELEVATED band an effective 1/(1-alpha) sigma test rather than the documented one.
        z_score = ((rtt_ms - baseline_mean) / baseline_sigma) if baseline_sigma > 0.0 else 0.0

        if timed_out:
            state = ThrottleState.ACK_TIMEOUT
        elif rtt_ms >= self.max_absolute_rtt_ms:
            state = ThrottleState.SILENT_THROTTLE
        elif warm and z_score >= self.z_score_threshold:
            state = ThrottleState.SILENT_THROTTLE
        elif not warm:
            state = ThrottleState.WARMUP
        elif z_score >= self.elevated_z_threshold:
            state = ThrottleState.ELEVATED_LATENCY
        else:
            state = ThrottleState.NORMAL

        is_throttled = state in (ThrottleState.SILENT_THROTTLE, ThrottleState.ACK_TIMEOUT)

        # A throttled sample is never admitted: training the baseline on the anomaly is
        # what lets a sustained throttle silence its own detector within a few samples.
        admitted = not is_throttled
        if admitted:
            self._baseline.update(rtt_ms)
            self._consecutive_throttled = 0
            self._throttled_sum_ms = 0.0
        else:
            self._consecutive_throttled += 1
            self._throttled_sum_ms += rtt_ms

        self._apply_backoff(state, rtt_ms)

        if is_throttled:
            logger.warning(
                "%s on order %s: RTT %.1fms vs baseline %.1fms +/- %.1fms (z=%.2f, n=%d). "
                "Recommended dispatch backoff %.0fms.",
                state.value, order_id, rtt_ms, baseline_mean, baseline_sigma,
                z_score, self._baseline.count, self.current_backoff_ms,
            )
            self._maybe_rebaseline()

        summary = (
            f"ACK RTT: {rtt_ms:.1f}ms | State: {state.value} | "
            f"Baseline: {baseline_mean:.1f}ms +/- {baseline_sigma:.1f}ms "
            f"(n={self._baseline.count}) | Z: {z_score:.2f} | "
            f"Backoff: {self.current_backoff_ms:.0f}ms"
        )

        return ThrottleStatusReport(
            state=state,
            latest_rtt_ms=rtt_ms,
            ewma_rtt_ms=baseline_mean,
            ewmsd_rtt_ms=baseline_sigma,
            recommended_backoff_ms=self.current_backoff_ms,
            is_throttled=is_throttled,
            summary=summary,
            z_score=z_score,
            order_id=order_id,
            baseline_sample_count=self._baseline.count,
            baseline_admitted=admitted,
        )

    def _apply_backoff(self, state: ThrottleState, rtt_ms: float) -> None:
        """AIMD on the dispatch rate, expressed as the inverse movement of a delay."""
        if state in (ThrottleState.SILENT_THROTTLE, ThrottleState.ACK_TIMEOUT):
            # Multiplicative decrease of dispatch rate.
            if self.current_backoff_ms <= 0.0:
                seed = max(self.min_backoff_ms, rtt_ms * 0.5)
            else:
                seed = self.current_backoff_ms * self.backoff_multiplier
            self.current_backoff_ms = min(self.max_backoff_ms, max(self.min_backoff_ms, seed))
        elif state is ThrottleState.ELEVATED_LATENCY:
            self.current_backoff_ms = min(
                self.max_backoff_ms,
                max(self.min_backoff_ms, self.current_backoff_ms + self.elevated_increment_ms),
            )
        else:
            # Additive increase of dispatch rate. Decays to exactly 0 when healthy;
            # min_backoff_ms floors an *active* penalty, it is not a resting delay.
            self.current_backoff_ms = max(
                0.0, self.current_backoff_ms - self.backoff_additive_decrease_ms)

    def _maybe_rebaseline(self) -> None:
        """Caller must hold ``self._lock``. No-op unless explicitly enabled."""
        if self.rebaseline_after_consecutive <= 0:
            return
        if self._consecutive_throttled < self.rebaseline_after_consecutive:
            return
        new_mean = self._throttled_sum_ms / self._consecutive_throttled
        logger.warning(
            "Re-anchoring latency baseline from %.1fms to %.1fms after %d consecutive "
            "throttled samples; further samples at this level will read as NORMAL. "
            "Confirm this is a permanent latency shift and not ongoing congestion.",
            self._baseline.mean, new_mean, self._consecutive_throttled,
        )
        self._baseline = _Baseline(alpha=self.alpha)
        for _ in range(self.min_samples_for_detection):
            self._baseline.update(new_mean)
        self._consecutive_throttled = 0
        self._throttled_sum_ms = 0.0

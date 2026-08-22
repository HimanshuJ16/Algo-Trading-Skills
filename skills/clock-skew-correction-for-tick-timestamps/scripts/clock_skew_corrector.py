"""
clock-skew-correction-for-tick-timestamps:
Estimates the *rate* difference (skew) between an exchange's clock and the local
recording host's clock from paired (exchange_ts, local_ts) tick observations, and
re-expresses the local receive timestamps on the exchange clock's timescale while
guaranteeing that the output is strictly increasing.

What the estimator can and cannot recover
-----------------------------------------
A one-way delay measurement decomposes as

    local_ts - exchange_ts  =  d_min  +  q(t)  +  offset(t)

where ``d_min`` is the fixed minimum one-way transit (propagation + serialisation +
fixed processing), ``q(t) >= 0`` is variable queueing/scheduling noise, and
``offset(t) = alpha + beta * t`` is the relative clock offset with skew ``beta``.

From one-way data alone **the skew is identifiable but the constant term is not**:
``d_min`` and the constant part of the clock offset enter the measurement only as a
sum, and no amount of one-way data separates them (Moon, Skelly & Towsley,
INFOCOM'99). Everything this module reports as ``alpha`` is therefore
``d_min + clock_offset_at_reference``, not a clock offset. Two consequences that
matter in production:

  * ``transform(..., remove_constant_offset=True)`` (the default) removes the whole
    constant term, so the output is aligned to the exchange clock's *scale and
    origin* but the minimum transit has been absorbed. ``corrected - exchange_ts``
    is then *excess* delay above the session minimum, **not** one-way latency. Do
    not feed it to a latency SLA.
  * ``transform(..., remove_constant_offset=False)`` removes only the drift, leaving
    delays positive and comparable over time. That is the right mode for latency
    analysis.

This is a research / data-engineering tool. It does **not** discipline a clock and
it does **not** create UTC traceability: under MiFID II RTS 25 the timestamps a firm
records for reportable events must come from a clock that is synchronised and
traceable to UTC. Never present regression-corrected timestamps as RTS 25 compliant
records. See ``references/standards.md``.

Estimator
---------
Windowed minimum filtering plus least squares on the per-window minima, in the
spirit of Paxson (SIGMETRICS'98). Two deliberate deviations from a naive version,
each because the naive version is wrong in ways that cost real money:

  1. **Regression is on window minima, never on all points.** Queueing noise is
     one-sided and positive, so least squares over every delay sample is biased
     upward by the mean queueing delay and reacts to bursts of congestion as if
     they were clock drift.
  2. **Windows with too few samples are dropped** (``min_points_per_window``). The
     minimum of *k* draws falls as *k* grows, so a window holding one tick
     contributes a full jitter draw as if it were a lower bound. Under a U-shaped
     intraday volume profile that turns tick density into fake skew.

The per-window minima are still an upper bound on the true lower envelope, so this
is not the linear-programming envelope fit of Moon et al.; it is the cheaper,
widely used approximation. ``diagnostics`` exposes the residual spread so a caller
can see when that approximation is not holding.

Precision
---------
Float64 POSIX seconds have a ULP of ~238 ns near 1.7e9, so a float64 second cannot
represent nanosecond timestamps and cannot hold two ticks 100 ns apart. Pass
``time_unit="ns"`` with int64 arrays for tick data: differences are then computed
exactly, the output is int64, and the strict-monotonicity guarantee holds to 1 ns.
With float seconds the guarantee is still enforced, but the smallest achievable
separation is one ULP of the timestamp magnitude, and a warning is emitted when
that binds.
"""
import dataclasses
import logging
from typing import Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

#: Ticks per second for each accepted input unit.
_UNIT_SCALE = {"s": 1.0, "ms": 1e3, "us": 1e6, "ns": 1e9}

#: Sanity ceiling on |skew|, in parts per million. The Unix kernel discipline used
#: by ntpd slews at a maximum of 500 ppm, so a *sustained* apparent rate offset
#: beyond ~1000 ppm is not a clock: it is unsorted input, mixed units, an NTP step
#: inside the calibration window, or a venue timestamp problem.
DEFAULT_MAX_DRIFT_PPM = 1000.0


@dataclasses.dataclass(frozen=True)
class SkewFitDiagnostics:
    """Evidence about a fit, so a caller can decide whether to trust it.

    Attributes:
        n_samples: Paired observations supplied to ``fit``.
        n_windows_used: Windows that met ``min_points_per_window`` and entered the
            regression.
        n_windows_dropped: Windows discarded for holding too few samples.
        drift_ppm: Estimated skew in parts per million (``beta * 1e6``).
        min_delay_sec: Smallest observed ``local_ts - exchange_ts``. Includes the
            minimum one-way transit; it is not a clock offset.
        residual_std_sec: Standard deviation of the window minima about the fitted
            line. Large values mean the lower envelope is not linear -- typically a
            clock step or a congestion regime change inside the window.
        max_abs_residual_sec: Largest absolute residual. Note that a clock step in
            the middle of the series inflates the residual *spread* too, so this
            alone will not reveal one -- ``fit`` detects steps from jumps between
            consecutive window minima and logs a warning instead.
        r_squared: Coefficient of determination of the fit on the window minima.
            ``nan`` when the minima are constant (a legitimately drift-free clock).
        span_sec: Exchange-time span the calibration covers.
        reliable: False when the fit fell back to a constant offset because too few
            windows survived. A False here means ``beta`` is 0 by default, not by
            measurement.
    """

    n_samples: int
    n_windows_used: int
    n_windows_dropped: int
    drift_ppm: float
    min_delay_sec: float
    residual_std_sec: float
    max_abs_residual_sec: float
    r_squared: float
    span_sec: float
    reliable: bool


class ClockSkewCorrector:
    """Fits a linear clock-skew model and applies it to local receive timestamps.

    The corrector is stateful in one way that matters: ``fit`` records the exchange
    timestamp of its first sample as the reference epoch, and ``transform`` measures
    against *that* epoch. This is what makes the causal usage -- calibrate on a
    warm-up window, apply to later ticks -- produce the right answer.

    Args:
        window_size_sec: Width of the minimum-filtering windows, in seconds of
            exchange time. Wide enough that most windows contain a quiet moment,
            narrow enough to give several windows across the calibration span.
        min_epsilon_sec: Separation forced between consecutive output timestamps
            when monotonicity has to be repaired. Clamped up to one output tick in
            integer mode.
        min_points_per_window: Windows with fewer samples are excluded from the
            regression. Guards against sparse windows contributing a raw jitter
            draw as a lower bound.
        max_drift_ppm: ``fit`` raises ``ValueError`` if the estimated skew exceeds
            this. Pass ``float("inf")`` to disable.
        time_unit: Unit of the supplied timestamps -- ``"s"``, ``"ms"``, ``"us"`` or
            ``"ns"``. Integer arrays keep integer arithmetic end to end.

    Raises:
        ValueError: On an unknown ``time_unit`` or a non-positive window size.
    """

    def __init__(
        self,
        window_size_sec: float = 10.0,
        min_epsilon_sec: float = 1e-6,
        min_points_per_window: int = 10,
        max_drift_ppm: float = DEFAULT_MAX_DRIFT_PPM,
        time_unit: str = "s",
    ) -> None:
        if time_unit not in _UNIT_SCALE:
            raise ValueError(
                f"time_unit must be one of {sorted(_UNIT_SCALE)}, got {time_unit!r}")
        if not np.isfinite(window_size_sec) or window_size_sec <= 0:
            raise ValueError(f"window_size_sec must be positive, got {window_size_sec!r}")
        if min_epsilon_sec <= 0:
            raise ValueError(f"min_epsilon_sec must be positive, got {min_epsilon_sec!r}")
        if min_points_per_window < 1:
            raise ValueError(
                f"min_points_per_window must be >= 1, got {min_points_per_window!r}")

        self.window_size_sec = float(window_size_sec)
        self.min_epsilon_sec = float(min_epsilon_sec)
        self.min_points_per_window = int(min_points_per_window)
        self.max_drift_ppm = float(max_drift_ppm)
        self.time_unit = time_unit
        self._scale = _UNIT_SCALE[time_unit]

        #: Constant term of the fitted line, in seconds. This is
        #: ``minimum_transit + clock_offset``; the two are not separable from
        #: one-way data. Do not report it as a clock offset.
        self.alpha: float = 0.0
        #: Skew: seconds of local clock gained per second of exchange time.
        self.beta: float = 0.0

        self._fitted: bool = False
        # Held in the input's native type: an int64 nanosecond epoch (~1.7e18)
        # exceeds float64's 53-bit integer range, where the ULP is already 256 ns.
        self._reference_epoch: Optional[Union[int, float]] = None
        self._integer_mode: bool = False
        self._fit_span_sec: float = 0.0
        self.diagnostics: Optional[SkewFitDiagnostics] = None

    # ------------------------------------------------------------------ helpers

    @property
    def drift_ppm(self) -> float:
        """Estimated skew in parts per million."""
        return self.beta * 1e6

    def _validate_pair(
        self, exchange_ts: np.ndarray, local_ts: np.ndarray, context: str
    ) -> Tuple[np.ndarray, np.ndarray, bool]:
        """Validates the paired arrays.

        Returns:
            ``(exchange_ts, local_ts, integer_mode)``, dtypes preserved.
        """
        ex = np.asarray(exchange_ts)
        loc = np.asarray(local_ts)

        if ex.ndim != 1 or loc.ndim != 1:
            raise ValueError(f"{context}: timestamps must be 1-D arrays.")
        if ex.size != loc.size:
            raise ValueError(
                f"{context}: exchange_ts and local_ts must be the same length "
                f"({ex.size} vs {loc.size}).")
        if ex.size == 0:
            raise ValueError(f"{context}: timestamps must be non-empty.")
        for name, arr in (("exchange_ts", ex), ("local_ts", loc)):
            if not (np.issubdtype(arr.dtype, np.floating)
                    or np.issubdtype(arr.dtype, np.integer)):
                raise ValueError(f"{context}: {name} must be a numeric array, "
                                 f"got dtype {arr.dtype}.")
            if np.issubdtype(arr.dtype, np.floating) and not np.all(np.isfinite(arr)):
                raise ValueError(f"{context}: {name} contains NaN or Inf.")
        # Mixing an integer series with a float one would run the exact integer
        # path for one array and the lossy float path for the other.
        integer_mode = bool(np.issubdtype(ex.dtype, np.integer))
        if integer_mode != bool(np.issubdtype(loc.dtype, np.integer)):
            raise ValueError(
                f"{context}: exchange_ts and local_ts must both be integer or both "
                f"be floating point, got {ex.dtype} and {loc.dtype}.")
        # Windowing and the monotonicity repair both assume arrival order. Silently
        # accepting shuffled input is how a corrector ends up reporting beta = 0.
        if np.any(np.diff(ex) < 0):
            raise ValueError(
                f"{context}: exchange_ts must be non-decreasing. Sort the feed by "
                "exchange timestamp first, and calibrate each venue/host pair "
                "separately -- skew is a property of one clock pair.")
        if np.any(np.diff(loc) < 0):
            raise ValueError(
                f"{context}: local_ts must be non-decreasing (it is a receive-order "
                "series). Out-of-order local timestamps indicate a capture problem "
                "that skew correction cannot repair.")
        return ex, loc, integer_mode

    def _delays_sec(
        self, ex: np.ndarray, loc: np.ndarray, integer_mode: bool
    ) -> np.ndarray:
        """``local - exchange`` in float seconds, differencing before scaling."""
        if integer_mode:
            # Exact in int64, then scaled: avoids the ~238 ns float64 ULP at epoch
            # magnitudes swallowing the very quantity being measured.
            diff = loc.astype(np.int64) - ex.astype(np.int64)
            return diff.astype(np.float64) / self._scale
        return (loc.astype(np.float64) - ex.astype(np.float64)) / self._scale

    def _relative_sec(
        self, ts: np.ndarray, reference: Union[int, float], integer_mode: bool
    ) -> np.ndarray:
        """Seconds elapsed from ``reference``, differenced before scaling.

        In integer mode ``reference`` is a Python ``int`` and the subtraction stays
        exact. Routing it through a float would lose up to 256 ns at a nanosecond
        epoch, which is past float64's 53-bit exact-integer range.
        """
        if integer_mode:
            diff = ts.astype(np.int64) - np.int64(reference)
            return diff.astype(np.float64) / self._scale
        return (ts.astype(np.float64) - float(reference)) / self._scale

    # ---------------------------------------------------------------------- fit

    def fit(self, exchange_ts: np.ndarray, local_ts: np.ndarray) -> "ClockSkewCorrector":
        """Estimates the skew from windowed minimum one-way delays.

        Args:
            exchange_ts: Venue-stamped send times, non-decreasing.
            local_ts: Locally stamped receive times for the same events.

        Returns:
            self, so ``fit`` chains.

        Raises:
            ValueError: On malformed input, or when the estimated skew exceeds
                ``max_drift_ppm`` -- which indicates a data problem, not a clock.
        """
        ex, loc, integer_mode = self._validate_pair(exchange_ts, local_ts, "fit")

        reference: Union[int, float] = int(ex[0]) if integer_mode else float(ex[0])
        rel = self._relative_sec(ex, reference, integer_mode)
        delays = self._delays_sec(ex, loc, integer_mode)
        span = float(rel[-1])

        # O(n) windowing: rel is non-decreasing, so the window index is
        # non-decreasing and every window is one contiguous run. The previous
        # per-window boolean mask was O(n * n_windows) -- ~34 s for 2M ticks over a
        # 6.5 h session, which makes a full tick day unusable.
        widx = np.floor(rel / self.window_size_sec).astype(np.int64)
        starts = np.flatnonzero(np.concatenate(([True], np.diff(widx) != 0)))
        counts = np.diff(np.concatenate((starts, [rel.size])))
        win_min = np.minimum.reduceat(delays, starts)

        # x-coordinate is the exchange time of the sample that *achieved* the
        # minimum, not the window's start edge. Using the edge displaces every
        # point by up to one window width along x.
        positions = np.arange(rel.size)
        is_min = delays == np.repeat(win_min, counts)
        argmins = np.minimum.reduceat(np.where(is_min, positions, rel.size), starts)
        win_x = rel[argmins]

        keep = counts >= self.min_points_per_window
        n_dropped = int(np.count_nonzero(~keep))
        x, y = win_x[keep], win_min[keep]

        if x.size < 2:
            self.alpha = float(np.min(delays))
            self.beta = 0.0
            self._finalise(reference, integer_mode, span, ex.size, int(x.size),
                           n_dropped, float(np.min(delays)), np.array([]),
                           np.array([]), reliable=False)
            logger.warning(
                "Clock skew fit unreliable: only %d usable window(s) from %d sample(s) "
                "spanning %.3fs (min_points_per_window=%d, window_size_sec=%.3f). "
                "Falling back to a constant offset with beta=0 -- no drift was "
                "measured, and none has been corrected.",
                x.size, ex.size, span, self.min_points_per_window, self.window_size_sec)
            return self

        beta, alpha = (float(v) for v in np.polyfit(x, y, deg=1))
        drift_ppm = beta * 1e6
        if abs(drift_ppm) > self.max_drift_ppm:
            raise ValueError(
                f"fit: estimated skew {drift_ppm:.1f} ppm exceeds max_drift_ppm="
                f"{self.max_drift_ppm:.1f}. A disciplined clock does not drift this "
                "fast (the Unix kernel discipline ntpd relies on slews at most "
                "500 ppm); check for mixed time units, an NTP step inside the "
                "calibration window, or ticks from more than one host mixed into "
                "one series.")

        self.alpha, self.beta = alpha, beta
        self._finalise(reference, integer_mode, span, ex.size, int(x.size), n_dropped,
                       float(np.min(delays)), y, alpha + beta * x, reliable=True)

        diag = self.diagnostics
        logger.info(
            "Clock skew fit: drift=%.3f ppm, constant term=%.6fs (transit+offset, "
            "not separable), windows used=%d dropped=%d, residual sd=%.3e s, R2=%.4f",
            self.drift_ppm, self.alpha, x.size, n_dropped,
            diag.residual_std_sec, diag.r_squared)
        self._warn_on_step(y, x)
        return self

    def _warn_on_step(self, y: np.ndarray, x: np.ndarray) -> None:
        """Flags a discontinuity in the lower envelope (a clock step).

        A step is not an outlier about the fitted line -- with a step in the middle
        of the series half the residuals are large, which inflates the residual
        spread and hides the event. What a step *does* leave is one enormous jump
        between consecutive window minima once the fitted drift is removed, so that
        is what is tested.
        """
        if y.size < 4:
            return
        detrended_jumps = np.abs(np.diff(y) - self.beta * np.diff(x))
        typical = float(np.median(detrended_jumps))
        largest = float(np.max(detrended_jumps))
        if typical <= 0 or largest <= 20 * typical:
            return
        logger.warning(
            "Clock skew fit: the minimum-delay envelope jumps %.3e s between two "
            "consecutive windows, %.0fx the typical %.3e s. That is a step, not "
            "linear drift -- ntpd steps rather than slews the clock once an offset "
            "exceeds a 128 ms threshold by default. A single line fitted across a "
            "step is wrong on both sides of it: split the series at the step and "
            "fit each segment separately.",
            largest, largest / typical, typical)

    def _finalise(
        self, reference: Union[int, float], integer_mode: bool, span: float,
        n_samples: int, n_used: int, n_dropped: int, min_delay: float,
        y: np.ndarray, y_hat: np.ndarray, reliable: bool,
    ) -> None:
        """Records fit state and diagnostics."""
        if y.size >= 2:
            resid = y - y_hat
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            r2 = float(1.0 - np.sum(resid ** 2) / ss_tot) if ss_tot > 0 else float("nan")
            resid_std = float(np.std(resid))
            max_resid = float(np.max(np.abs(resid)))
        else:
            r2, resid_std, max_resid = float("nan"), 0.0, 0.0

        self._reference_epoch = reference
        self._integer_mode = integer_mode
        self._fit_span_sec = span
        self._fitted = True
        self.diagnostics = SkewFitDiagnostics(
            n_samples=n_samples, n_windows_used=n_used, n_windows_dropped=n_dropped,
            drift_ppm=self.drift_ppm, min_delay_sec=min_delay,
            residual_std_sec=resid_std, max_abs_residual_sec=max_resid,
            r_squared=r2, span_sec=span, reliable=reliable,
        )

    # ---------------------------------------------------------------- transform

    def transform(
        self,
        exchange_ts: np.ndarray,
        local_ts: np.ndarray,
        remove_constant_offset: bool = True,
    ) -> np.ndarray:
        """Applies the fitted model to local timestamps and enforces monotonicity.

        Args:
            exchange_ts: Venue send times for the events being corrected.
            local_ts: Local receive times to correct.
            remove_constant_offset: When True (default) the whole fitted constant is
                removed, aligning the output to the exchange clock's origin -- but
                absorbing the minimum one-way transit, so the residual is *excess*
                delay, not latency. Set False to remove drift only, which keeps
                delays positive and is the correct mode for latency analysis.

        Returns:
            Strictly increasing corrected timestamps, in the same unit and dtype
            family as the input.

        Raises:
            RuntimeError: If called before ``fit``.
            ValueError: On malformed input.
        """
        if not self._fitted:
            raise RuntimeError("transform() called before fit(); there is no model to "
                               "apply, and returning the input unchanged would look "
                               "like a successful correction.")
        ex, loc, integer_mode = self._validate_pair(exchange_ts, local_ts, "transform")
        if integer_mode != self._integer_mode:
            fitted_kind = "integer" if self._integer_mode else "floating point"
            given_kind = "integer" if integer_mode else "floating point"
            raise ValueError(
                "transform: timestamps must use the same representation as fit() "
                f"(fitted on {fitted_kind} input, given {given_kind}).")
        reference = self._reference_epoch

        # Measured against the *fit's* epoch. Re-basing on this batch's own first
        # tick applies alpha at the wrong origin: 296 s of separation at 100 ppm is
        # a silent 30 ms error.
        rel_ex = self._relative_sec(ex, reference, integer_mode)
        rel_loc = self._relative_sec(loc, reference, integer_mode)
        self._warn_on_extrapolation(rel_ex)

        offset = self.beta * rel_ex + (self.alpha if remove_constant_offset else 0.0)
        # Corrections and the monotonicity walk happen in relative seconds, where
        # the float64 ULP is ~15 fs rather than the ~238 ns it is at POSIX epoch.
        corrected_rel = rel_loc - offset

        eps = (max(self.min_epsilon_sec, 1.0 / self._scale) if integer_mode
               else self.min_epsilon_sec)
        corrected_rel = _enforce_monotonic(corrected_rel, eps)

        if integer_mode:
            out = np.int64(reference) + np.rint(
                corrected_rel * self._scale).astype(np.int64)
            return _repair_ties_int(out)
        out = float(reference) + corrected_rel
        return _repair_ties_float(out)

    def fit_transform(
        self,
        exchange_ts: np.ndarray,
        local_ts: np.ndarray,
        remove_constant_offset: bool = True,
    ) -> np.ndarray:
        """Fits and applies on the same data.

        This is in-sample by construction: every corrected timestamp depends on
        delays observed after it. Fine for offline research and for re-stamping an
        archived capture; **not** a live pipeline, where each tick must be corrected
        with a model fitted only on earlier ticks. See ``references/workflows.md``.
        """
        self.fit(exchange_ts, local_ts)
        return self.transform(exchange_ts, local_ts, remove_constant_offset)

    def _warn_on_extrapolation(self, rel_ex: np.ndarray) -> None:
        """Warns when applying the model far outside the calibrated span."""
        span = max(self._fit_span_sec, self.window_size_sec)
        lead = float(rel_ex[-1]) - self._fit_span_sec
        lag = -float(rel_ex[0])
        overshoot = max(lead, lag)
        if overshoot > span:
            logger.warning(
                "Applying a skew model calibrated over %.1fs to data %.1fs outside "
                "that span. Linear drift extrapolation degrades: at the fitted "
                "%.1f ppm, a 10%% change in the underlying rate over %.1fs is "
                "%.3e s of unmodelled error. Refit on recent data.",
                self._fit_span_sec, overshoot, self.drift_ppm, overshoot,
                0.1 * abs(self.beta) * overshoot)


# ------------------------------------------------------------------ free helpers

def _enforce_monotonic(values: np.ndarray, epsilon: float) -> np.ndarray:
    """Vectorised form of ``out[i] = max(values[i], out[i-1] + epsilon)``.

    Shifting by ``i * epsilon`` turns the recursion into a running maximum, which
    gives the same result as the sequential walk in O(n) without a Python loop.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.size < 2:
        return values.copy()
    ramp = np.arange(values.size, dtype=np.float64) * epsilon
    return np.maximum.accumulate(values - ramp) + ramp


def _repair_ties_float(values: np.ndarray) -> np.ndarray:
    """Restores strict monotonicity lost to float rounding, one ULP at a time."""
    if values.size < 2 or bool(np.all(np.diff(values) > 0)):
        return values
    logger.warning(
        "Corrected timestamps collapsed to equal float64 values and were separated "
        "by one ULP (~%.1f ns at this magnitude). Float64 POSIX seconds cannot hold "
        "sub-microsecond tick separation; pass int64 arrays with time_unit='ns'.",
        float(np.spacing(abs(float(values[0])))) * 1e9)
    out = np.array(values, dtype=np.float64, copy=True)
    for i in range(1, out.size):
        if out[i] <= out[i - 1]:
            out[i] = np.nextafter(out[i - 1], np.inf)
    return out


def _repair_ties_int(values: np.ndarray) -> np.ndarray:
    """Restores strict monotonicity lost to rounding back to integer ticks."""
    if values.size < 2 or bool(np.all(np.diff(values) > 0)):
        return values
    out = np.array(values, dtype=np.int64, copy=True)
    for i in range(1, out.size):
        if out[i] <= out[i - 1]:
            out[i] = out[i - 1] + 1
    return out

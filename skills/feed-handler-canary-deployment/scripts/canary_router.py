"""Symbol-level canary routing, comparative output audit, and auto-rollback
breaker for market data feed handler releases.

The router decides, per instrument, whether the *decoded output* of the
candidate feed handler (``V_canary``) or of the incumbent (``V_stable``) is
published downstream, audits the two decoders against each other tick by tick,
and trips a breaker that reverts every symbol to ``V_stable`` when the canary
diverges.

Scope and assumptions
---------------------
* Both handlers consume the **same** exchange stream and decode the **same**
  message. Nasdaq TotalView-ITCH, for instance, is "a series of sequenced
  messages" delivered over SoupBinTCP/MoldUDP64 with no per-symbol
  subscription, so a symbol-level canary is a decision about whose *output* is
  trusted, not about who receives which packets. Because both handlers see the
  same input, agreement is expected to be **exact**: ITCH prices are integer
  fields with an implied precision, not floating point, so ``price_tolerance``
  defaults to ``0.0``.
* Routing must be reproducible across processes and restarts, so bucketing uses
  a fixed digest (``hashlib.blake2b``). Python's built-in ``hash()`` is salted
  per process (``PYTHONHASHSEED``) and must never be used for this.
* The router is designed to be called from feed handler threads; all mutable
  state is guarded by a lock.

This module is illustrative reference code, not a drop-in production service.
Thresholds are operational policy defaults, not externally mandated values --
see ``references/standards.md``.
"""
import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Allocation buckets. 10,000 buckets give 0.01% granularity, so a 0.5% canary
# is actually 0.5% rather than being rounded up to the nearest whole percent.
_BUCKET_RESOLUTION = 10_000


class CanaryStatus(Enum):
    HEALTHY = "HEALTHY"
    AUDIT_FAIL = "AUDIT_FAIL"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class CanaryRoutingDecision:
    """Which handler's output is published for one symbol.

    ``reason`` names the rule that decided it (``whitelist``, ``hash_bucket``,
    ``rolled_back``) so an operator can tell a deliberate whitelist entry from
    an allocation the hash happened to select.
    """
    symbol: str
    is_canary: bool
    version_tag: str
    reason: str = "hash_bucket"


@dataclass
class CanaryEvent:
    """Timestamped record of a deployment state change.

    Retained so ramps, promotions and rollbacks are auditable after the fact.
    ESMA's supervisory briefing on algorithmic trading (ESMA74-1505669079-10311,
    26 February 2026, paras. 29 and 31) expects firms in scope of MiFID II to
    timestamp, approve and record material changes, and names changes to data
    feeds as a change type warranting retesting. See ``references/standards.md``
    for what is binding and what is guidance.
    """
    timestamp: float
    event_type: str
    detail: str
    authorised_by: Optional[str] = None


@dataclass
class CanaryAuditSummary:
    total_evaluated_ticks: int
    canary_symbols_count: int
    baseline_symbols_count: int
    price_mismatch_count: int
    error_count: int
    status: CanaryStatus
    message: str
    error_rate: float = 0.0
    canary_percentage: float = 0.0
    is_rolled_back: bool = False
    rollback_reason: Optional[str] = None


class FeedHandlerCanaryRouter:
    """Routes symbols between ``V_stable`` and ``V_canary``, audits the two
    decoders against each other, and auto-rolls back on divergence.

    Args:
        canary_percentage: Share of the universe routed to the canary, in
            percent (0-100). Symbols are selected by digest bucket, so ramping
            the percentage up only ever *adds* symbols: a symbol that was canary
            at 10% is still canary at 50%.
        canary_symbols: Symbols pinned to the canary regardless of percentage.
            Pin deliberately unrepresentative instruments here (an illiquid
            name, a sub-dollar quote, a symbol carrying a suffix), not only the
            mega-caps the hash would already cover.
        max_allowed_error_rate: Combined mismatch+exception count as a fraction
            of audited ticks (0-1), above which the breaker trips.
        price_tolerance: Maximum accepted **relative** price difference. Defaults
            to ``0.0`` (exact agreement), which is the correct default when both
            handlers decode the same message. Loosen it only when the two
            handlers legitimately produce different representations.
        min_ticks_before_rollback: Audited ticks required before the *rate*
            breaker can trip, so one mismatch in the first three ticks is not a
            100% error rate. Exception-driven rollback is not gated by this.
        max_allowed_exceptions: Unhandled canary decoder exceptions tolerated
            before immediate rollback. Defaults to ``0``: a decoder that raises
            on live data is not a candidate for promotion.
        clock: Injectable time source for deterministic tests.

    Raises:
        ValueError: on out-of-range or non-finite configuration.
    """

    def __init__(
        self,
        canary_percentage: float = 10.0,
        canary_symbols: Optional[Sequence[str]] = None,
        max_allowed_error_rate: float = 0.01,
        price_tolerance: float = 0.0,
        min_ticks_before_rollback: int = 10,
        max_allowed_exceptions: int = 0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._validate_percentage(canary_percentage)
        if not isinstance(max_allowed_error_rate, (int, float)) or isinstance(max_allowed_error_rate, bool):
            raise ValueError(f"max_allowed_error_rate must be a number, got {max_allowed_error_rate!r}")
        if not math.isfinite(max_allowed_error_rate) or not 0.0 <= max_allowed_error_rate <= 1.0:
            raise ValueError(
                "max_allowed_error_rate must be a finite fraction in [0, 1], "
                f"got {max_allowed_error_rate!r}"
            )
        if not isinstance(price_tolerance, (int, float)) or isinstance(price_tolerance, bool):
            raise ValueError(f"price_tolerance must be a number, got {price_tolerance!r}")
        if not math.isfinite(price_tolerance) or price_tolerance < 0.0:
            raise ValueError(f"price_tolerance must be finite and >= 0, got {price_tolerance!r}")
        if min_ticks_before_rollback < 1:
            raise ValueError(f"min_ticks_before_rollback must be >= 1, got {min_ticks_before_rollback!r}")
        if max_allowed_exceptions < 0:
            raise ValueError(f"max_allowed_exceptions must be >= 0, got {max_allowed_exceptions!r}")

        self.canary_percentage = float(canary_percentage)
        self.canary_symbols_whitelist = {self._normalise_symbol(s) for s in (canary_symbols or [])}
        self.max_allowed_error_rate = float(max_allowed_error_rate)
        self.price_tolerance = float(price_tolerance)
        self.min_ticks_before_rollback = int(min_ticks_before_rollback)
        self.max_allowed_exceptions = int(max_allowed_exceptions)
        self._clock = clock if clock is not None else time.time

        self._lock = threading.RLock()
        self.is_rolled_back = False
        self.rollback_reason: Optional[str] = None
        self.total_ticks_processed = 0
        self.price_mismatches = 0
        self.canary_errors = 0
        self._events: List[CanaryEvent] = []

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_percentage(percentage: float) -> None:
        if not isinstance(percentage, (int, float)) or isinstance(percentage, bool):
            raise ValueError(f"canary_percentage must be a number, got {percentage!r}")
        if not math.isfinite(percentage) or not 0.0 <= percentage <= 100.0:
            raise ValueError(f"canary_percentage must be a finite value in [0, 100], got {percentage!r}")

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError(f"symbol must be a string, got {type(symbol).__name__}")
        normalised = symbol.strip().upper()
        if not normalised:
            raise ValueError("symbol must be a non-empty string")
        return normalised

    @staticmethod
    def _coerce_price(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a number, got {value!r}")
        return float(value)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route_symbol(self, symbol: str) -> CanaryRoutingDecision:
        """Returns which handler's output is published for ``symbol``.

        Deterministic across processes and restarts: the bucket comes from a
        fixed digest of the symbol, never from Python's salted ``hash()``.
        """
        sym = self._normalise_symbol(symbol)

        with self._lock:
            if self.is_rolled_back:
                return CanaryRoutingDecision(sym, False, "V_stable", "rolled_back")
            percentage = self.canary_percentage
            whitelisted = sym in self.canary_symbols_whitelist

        if whitelisted:
            return CanaryRoutingDecision(sym, True, "V_canary", "whitelist")

        is_canary = self._bucket_of(sym) < percentage * (_BUCKET_RESOLUTION / 100.0)
        return CanaryRoutingDecision(
            sym, is_canary, "V_canary" if is_canary else "V_stable", "hash_bucket"
        )

    @staticmethod
    def _bucket_of(symbol: str) -> int:
        """Stable allocation bucket in ``[0, _BUCKET_RESOLUTION)`` for a symbol.

        ``blake2b`` is used rather than ``md5`` because it is always available:
        ``hashlib.md5`` raises on FIPS-enforcing builds, and this hash has no
        security purpose.
        """
        digest = hashlib.blake2b(symbol.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % _BUCKET_RESOLUTION

    # ------------------------------------------------------------------
    # Comparative audit
    # ------------------------------------------------------------------

    def audit_tick_pair(
        self,
        symbol: str,
        price_stable: float,
        price_canary: float,
        sequence_number: Optional[int] = None,
        canary_sequence_number: Optional[int] = None,
    ) -> bool:
        """Audits one *aligned* pair of decoded prices for the same message.

        The two prices must come from the same source message. Pass both
        sequence numbers whenever the handlers expose them: comparing tick *N*
        of the canary against tick *M* of the baseline manufactures mismatches
        when the market moves and hides real divergence when it does not.

        A mismatch is recorded when either price is non-finite or non-positive,
        or when the relative difference exceeds ``price_tolerance``.

        Returns:
            ``True`` while the canary deployment may continue, ``False`` once
            the breaker has tripped. **A single mismatch still returns**
            ``True`` -- this is a deployment-level verdict, not a per-tick one.
            Read ``get_audit_summary()`` for tick-level counts.

        Raises:
            ValueError: if the symbol is invalid, a price is not a number, or
                the two sequence numbers disagree (a caller alignment bug, not
                a feed defect, so it is never counted as a canary error).
        """
        sym = self._normalise_symbol(symbol)
        stable = self._coerce_price(price_stable, "price_stable")
        canary = self._coerce_price(price_canary, "price_canary")

        if (
            sequence_number is not None
            and canary_sequence_number is not None
            and sequence_number != canary_sequence_number
        ):
            raise ValueError(
                f"Unaligned audit pair for {sym}: baseline sequence {sequence_number} "
                f"vs canary sequence {canary_sequence_number}. Align the streams by "
                "message identity before auditing."
            )

        with self._lock:
            if self.is_rolled_back:
                # Already tripped: stop counting and stop logging against a
                # deployment that is no longer routing traffic to the canary.
                return False

            self.total_ticks_processed += 1
            mismatch_reason = self._classify_mismatch(stable, canary)
            if mismatch_reason is not None:
                self.price_mismatches += 1
                logger.warning(
                    "Canary price audit breach on %s (%s): stable=%r canary=%r seq=%s",
                    sym, mismatch_reason, price_stable, price_canary, sequence_number,
                )

            return self._evaluate_breaker(context=f"price audit on {sym}")

    def _classify_mismatch(self, stable: float, canary: float) -> Optional[str]:
        """Names why a pair disagrees, or ``None`` when it agrees."""
        if not math.isfinite(stable) or not math.isfinite(canary):
            # NaN never compares greater than a tolerance, so a naive relative
            # difference check scores a NaN price as perfect agreement.
            return "non_finite_price"
        if stable <= 0.0 or canary <= 0.0:
            # A zero or negative decoded price is a decode defect, not merely a
            # divisor to be guarded around and then ignored.
            return "non_positive_price"
        if canary == stable:
            return None
        if self.price_tolerance == 0.0:
            return "exact_mismatch"
        if abs(canary - stable) / stable > self.price_tolerance:
            return "tolerance_breach"
        return None

    def record_canary_exception(self, symbol: str, err_msg: str) -> None:
        """Records an unhandled exception raised inside the canary decoder.

        With the default ``max_allowed_exceptions=0`` this trips the breaker
        immediately: a decoder that raises on live data has already failed the
        canary, whatever the tick count.
        """
        sym = self._normalise_symbol(symbol)
        with self._lock:
            if self.is_rolled_back:
                return
            self.canary_errors += 1
            logger.error("Canary exception on %s: %s", sym, err_msg)

            if self.canary_errors > self.max_allowed_exceptions:
                self._trigger_auto_rollback(
                    f"Canary decoder raised {self.canary_errors} exception(s), "
                    f"limit {self.max_allowed_exceptions}. Last: {err_msg}"
                )
                return
            self._evaluate_breaker(context=f"exception on {sym}")

    def _evaluate_breaker(self, context: str) -> bool:
        """Trips the rate breaker if the error rate is over threshold.

        Caller must hold the lock. Returns whether the canary may continue.
        """
        if self.total_ticks_processed < self.min_ticks_before_rollback:
            return not self.is_rolled_back

        rate = (self.price_mismatches + self.canary_errors) / self.total_ticks_processed
        if rate > self.max_allowed_error_rate:
            self._trigger_auto_rollback(
                f"Error rate {rate * 100:.2f}% over {self.total_ticks_processed} audited ticks "
                f"breached threshold {self.max_allowed_error_rate * 100:.2f}% ({context})."
            )
            return False
        return not self.is_rolled_back

    # ------------------------------------------------------------------
    # Breaker and ramp control
    # ------------------------------------------------------------------

    def _trigger_auto_rollback(self, reason: str) -> None:
        """Trips the breaker once. Caller must hold the lock."""
        if self.is_rolled_back:
            return
        self.is_rolled_back = True
        self.rollback_reason = reason
        self._record_event("ROLLBACK", reason)
        logger.error("AUTO-ROLLBACK: every symbol reverted to V_stable. Reason: %s", reason)

    def force_rollback(self, reason: str, authorised_by: Optional[str] = None) -> None:
        """Manually reverts all symbols to ``V_stable``.

        Reducing exposure is never blocked; a second call is a no-op.
        """
        with self._lock:
            if self.is_rolled_back:
                return
            self.is_rolled_back = True
            self.rollback_reason = reason
            self._record_event("MANUAL_ROLLBACK", reason, authorised_by)
            logger.error(
                "MANUAL ROLLBACK by %s. Reason: %s", authorised_by or "unspecified", reason
            )

    def set_canary_percentage(
        self, new_percentage: float, authorised_by: Optional[str] = None
    ) -> None:
        """Ramps the canary allocation (e.g. 10 -> 50 -> 100).

        Because allocation is a bucket threshold, ramping up only adds symbols;
        symbols already on the canary stay on it, so the ramp never re-shuffles
        which handler owns an instrument mid-deployment.

        Raises:
            RuntimeError: if the breaker has tripped. Ramping a rolled-back
                deployment would silently route traffic back to the release
                that just failed; start a new deployment instead.
            ValueError: if the percentage is out of range.
        """
        self._validate_percentage(new_percentage)
        with self._lock:
            if self.is_rolled_back:
                raise RuntimeError(
                    "Cannot change canary allocation after rollback "
                    f"(reason: {self.rollback_reason})."
                )
            previous = self.canary_percentage
            self.canary_percentage = float(new_percentage)
            self._record_event(
                "RAMP",
                f"Canary allocation {previous:.2f}% -> {float(new_percentage):.2f}%.",
                authorised_by,
            )
            logger.info(
                "Canary allocation ramped %.2f%% -> %.2f%% (authorised_by=%s)",
                previous, float(new_percentage), authorised_by or "unspecified",
            )

    def promote_to_full(self, authorised_by: Optional[str] = None) -> None:
        """Promotes the canary to 100% of the universe."""
        self.set_canary_percentage(100.0, authorised_by=authorised_by)
        with self._lock:
            self._record_event("PROMOTION", "Canary promoted to 100% of universe.", authorised_by)

    def _record_event(
        self, event_type: str, detail: str, authorised_by: Optional[str] = None
    ) -> None:
        """Appends an audit event. Caller must hold the lock."""
        self._events.append(CanaryEvent(self._clock(), event_type, detail, authorised_by))

    @property
    def events(self) -> Tuple[CanaryEvent, ...]:
        """Immutable snapshot of ramp, promotion and rollback events."""
        with self._lock:
            return tuple(self._events)

    @property
    def error_rate(self) -> float:
        """Combined mismatch+exception count as a fraction of audited ticks."""
        with self._lock:
            if self.total_ticks_processed == 0:
                return 0.0
            return (self.price_mismatches + self.canary_errors) / self.total_ticks_processed

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_audit_summary(self, universe_symbols: Sequence[str]) -> CanaryAuditSummary:
        """Summarises the deployment against the supplied universe.

        Note: the symbol counts describe routing **as it stands now**. After a
        rollback every symbol routes to ``V_stable``, so ``canary_symbols_count``
        is 0 -- that is the current state, not a claim that the canary never
        carried traffic. Check ``is_rolled_back`` before reading the counts.
        """
        canary_cnt = 0
        baseline_cnt = 0
        for s in universe_symbols:
            if self.route_symbol(s).is_canary:
                canary_cnt += 1
            else:
                baseline_cnt += 1

        with self._lock:
            if self.is_rolled_back:
                status = CanaryStatus.ROLLED_BACK
                msg = f"Canary deployment rolled back to V_stable: {self.rollback_reason}"
            elif self.price_mismatches > 0 or self.canary_errors > 0:
                status = CanaryStatus.AUDIT_FAIL
                msg = (
                    f"Audit issues detected: {self.price_mismatches} mismatches, "
                    f"{self.canary_errors} errors."
                )
            else:
                status = CanaryStatus.HEALTHY
                msg = f"Canary deployment healthy across {canary_cnt} canary symbols."

            error_rate = (
                (self.price_mismatches + self.canary_errors) / self.total_ticks_processed
                if self.total_ticks_processed
                else 0.0
            )
            return CanaryAuditSummary(
                total_evaluated_ticks=self.total_ticks_processed,
                canary_symbols_count=canary_cnt,
                baseline_symbols_count=baseline_cnt,
                price_mismatch_count=self.price_mismatches,
                error_count=self.canary_errors,
                status=status,
                message=msg,
                error_rate=error_rate,
                canary_percentage=self.canary_percentage,
                is_rolled_back=self.is_rolled_back,
                rollback_reason=self.rollback_reason,
            )

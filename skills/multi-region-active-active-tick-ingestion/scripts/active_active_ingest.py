"""
multi-region-active-active-tick-ingestion: cross-region tick deduplicator, first-arrival
latency arbitrator, and regional liveness telemetry.

This implements the "redundant copies of one logical stream, arbitrated by identity,
first copy wins" pattern that venues already prescribe for their own A/B multicast
lines -- CME's MDP 3.0 guidance is that "UDP Feed A and UDP Feed B should be used for
arbitration", with duplicates discarded by packet sequence number. The same arbitration
is applied here across *cloud regions* rather than across two lines of one venue feed.

Clock domain contract (load-bearing):
    ``receipt_time`` values passed to a single ingestor instance MUST come from one
    clock domain. ``latency_delta_ms`` is a difference of two receipt timestamps; if
    the two regional ingest nodes each stamp locally on their own host, the difference
    carries their NTP/PTP offset and the forwarding hop to the arbiter, and is not an
    inter-region feed latency measurement at all. Either stamp both copies on the
    arbiter host, or discipline both regional hosts to a common time source and
    validate the residual offset -- see ``cross-datacenter-clock-sync-validation``.

What this component does NOT do:
    - It does not detect sequence gaps. A gap that survives arbitration means the
      message was lost in *every* region, which is precisely the case dedup cannot
      repair; ``emitted_sequence_gap`` on the result surfaces the signal so a real
      gap detector / retransmission path can act on it.
    - It does not re-sequence. Output is in arrival order, which for a sequenced feed
      is not necessarily sequence order. Order-sensitive consumers (order book state
      machines) must re-sequence downstream.
    - It does not reconcile disagreeing prices. Two regions carrying the same feed
      should be bit-identical; two *independent vendors* are a different problem, see
      ``market-data-feed-arbitration-across-vendors``.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import Enum
import hashlib
import logging
import math
import threading
import time
from typing import Any, Deque, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 10.0
DEFAULT_MAX_SIGNATURES = 200_000
DEFAULT_WIN_RATE_WINDOW = 1_000
DEFAULT_SILENCE_THRESHOLD_SECONDS = 5.0


class ArbitrationOutcome(str, Enum):
    """Why a tick was emitted or dropped."""

    #: Signature not seen inside the dedup window -- forwarded to the strategy engine.
    FIRST_ARRIVAL = "FIRST_ARRIVAL"
    #: Same signature already emitted from a *different* region -- redundancy working.
    CROSS_REGION_DUPLICATE = "CROSS_REGION_DUPLICATE"
    #: Same signature already emitted from the *same* region -- a retransmission,
    #: replay-on-reconnect, or a feed with a non-unique identity. Not an arbitration
    #: event, and never evidence that the other region is slow.
    SAME_REGION_DUPLICATE = "SAME_REGION_DUPLICATE"


class RegionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    #: Registered/seen before, but nothing received within the silence threshold.
    SILENT = "SILENT"
    #: Declared in ``expected_regions`` but never delivered a single message.
    NEVER_SEEN = "NEVER_SEEN"


def _canonical_float(value: Any, name: str) -> str:
    """Renders a numeric field as an exact, round-trip-stable signature component.

    ``float.hex()`` is used rather than a fixed-decimal format because any fixed
    precision silently merges distinct ticks: ``f"{p:.4f}"`` renders every price
    below 0.00005 as ``"0.0000"``, so two genuinely different SHIB quotes -- or two
    different 8-decimal crypto sizes -- collide and the second one is dropped as a
    "duplicate" before the strategy engine ever sees it.

    ``Decimal`` and string payloads are rejected rather than coerced: the conversion
    belongs once at the feed-parsing boundary, so that every region converts the venue's
    wire representation identically. Two regions converting differently would produce two
    signatures for one tick and defeat deduplication silently.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, got bool {value!r}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric, got {type(value).__name__} {value!r}")
    as_float = float(value)
    if not math.isfinite(as_float):
        # NaN/inf render identically under every format spec, so they would collapse
        # unrelated ticks onto one signature. Reject at the boundary instead.
        raise ValueError(f"{name} must be finite, got {value!r}")
    return (as_float + 0.0).hex()  # ``+ 0.0`` normalises -0.0 to 0.0


@dataclass
class RegionalTick:
    """One copy of a market data tick as received from one region."""

    region_id: str
    symbol: str
    sequence_id: int
    timestamp: float
    price: float
    volume: float
    local_receipt_time: float


@dataclass
class ActiveActiveIngestResult:
    """Outcome of arbitrating one incoming regional tick copy."""

    symbol: str
    tick: RegionalTick
    is_duplicate: bool
    first_arrived_region: str
    latency_delta_ms: float
    message: str
    outcome: ArbitrationOutcome = ArbitrationOutcome.FIRST_ARRIVAL
    #: True when the "duplicate" copy carries an *earlier* receipt time than the copy
    #: already emitted, i.e. ticks were handed to the ingestor out of arrival order.
    #: ``latency_delta_ms`` is then negative and is not an inter-region latency.
    arrival_order_inverted: bool = False
    #: Missing sequence numbers between the previous emission for this symbol and this
    #: one. Non-zero means the message was lost in *every* region -- arbitration cannot
    #: repair it; escalate to a retransmission / re-snapshot path.
    emitted_sequence_gap: int = 0
    #: True when this emission's sequence_id is not greater than the previous
    #: emission's for the same symbol (arrival order != sequence order, or a
    #: sequence-space reset).
    emitted_out_of_order: bool = False


@dataclass
class RegionHealth:
    """Liveness view of one region. Derived from message flow, never from win rate."""

    region_id: str
    status: RegionStatus
    messages: int
    first_arrivals: int
    duplicates: int
    rolling_win_percentage: float
    last_receipt_time: Optional[float]
    seconds_since_last_message: Optional[float]


@dataclass
class _RegionState:
    messages: int = 0
    first_arrivals: int = 0
    duplicates: int = 0
    last_receipt_time: Optional[float] = None


class MultiRegionActiveActiveIngestor:
    """
    Arbitrates redundant copies of one logical tick stream delivered from two or more
    cloud regions: the first copy of a given signature is forwarded, later copies are
    dropped, and the inter-copy delta is recorded.

    Thread safety: every public method takes an internal re-entrant lock, because the
    active-active topology this skill exists for means two or more feed-handler threads
    call :meth:`ingest_regional_tick` concurrently. Without it the check-then-insert on
    the signature cache races (both regions emit the same tick as a "first arrival")
    and concurrent eviction raises ``RuntimeError: dictionary changed size during
    iteration``. In a single-threaded asyncio feed handler the lock is uncontended.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_signatures: int = DEFAULT_MAX_SIGNATURES,
        expected_regions: Optional[Sequence[str]] = None,
        win_rate_window: int = DEFAULT_WIN_RATE_WINDOW,
        silence_threshold_seconds: float = DEFAULT_SILENCE_THRESHOLD_SECONDS,
    ) -> None:
        if not (isinstance(ttl_seconds, (int, float)) and math.isfinite(ttl_seconds) and ttl_seconds > 0):
            raise ValueError(f"ttl_seconds must be a finite positive number, got {ttl_seconds!r}")
        if not isinstance(max_signatures, int) or isinstance(max_signatures, bool) or max_signatures <= 0:
            raise ValueError(f"max_signatures must be a positive int, got {max_signatures!r}")
        if not isinstance(win_rate_window, int) or isinstance(win_rate_window, bool) or win_rate_window <= 0:
            raise ValueError(f"win_rate_window must be a positive int, got {win_rate_window!r}")
        if not (isinstance(silence_threshold_seconds, (int, float))
                and math.isfinite(silence_threshold_seconds) and silence_threshold_seconds > 0):
            raise ValueError(
                f"silence_threshold_seconds must be a finite positive number, "
                f"got {silence_threshold_seconds!r}")

        self.ttl_seconds = float(ttl_seconds)
        self.max_signatures = max_signatures
        self.silence_threshold_seconds = float(silence_threshold_seconds)

        self._lock = threading.RLock()
        # signature_hash -> (first_receipt_time, first_region_id), in insertion order so
        # expiry is a front-eviction rather than a full scan of the cache on every tick.
        self.seen_signatures: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
        self.region_win_counts: Dict[str, int] = {}
        self._region_state: Dict[str, _RegionState] = {}
        self._recent_winners: Deque[str] = deque(maxlen=win_rate_window)
        self._last_emitted_sequence: Dict[str, int] = {}
        self._capacity_evictions: int = 0

        for region in expected_regions or ():
            self._region_state.setdefault(self._normalise_region(region), _RegionState())

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _normalise_region(region_id: str) -> str:
        if not isinstance(region_id, str) or not region_id.strip():
            raise ValueError(f"region_id must be a non-empty string, got {region_id!r}")
        return region_id.strip().lower()

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        return symbol.strip().upper()

    def compute_tick_signature(self, symbol: str, sequence_id: int, price: float, volume: float) -> str:
        """Computes the deterministic identity of a tick, independent of which region carried it.

        The signature is ``MD5(symbol:sequence_id:price:volume)`` with price and volume
        rendered at full binary precision. The exchange timestamp is deliberately
        excluded: it is only a usable identity component if every region receives it
        bit-identically, and a vendor that re-stamps per region would defeat dedup
        entirely while looking correct in a single-region test.

        MD5 is used as a non-cryptographic content fingerprint only; ``usedforsecurity=False``
        keeps it available on FIPS-mode hosts, where a bare ``hashlib.md5()`` raises.
        """
        sym = self._normalise_symbol(symbol)
        if isinstance(sequence_id, bool) or not isinstance(sequence_id, int):
            raise ValueError(f"sequence_id must be an int, got {sequence_id!r}")
        raw_str = (
            f"{sym}:{sequence_id}:"
            f"{_canonical_float(price, 'price')}:{_canonical_float(volume, 'volume')}"
        )
        return hashlib.md5(raw_str.encode("utf-8"), usedforsecurity=False).hexdigest()

    # ------------------------------------------------------------------ ingest

    def ingest_regional_tick(
        self,
        region_id: str,
        symbol: str,
        sequence_id: int,
        timestamp: float,
        price: float,
        volume: float,
        receipt_time: Optional[float] = None,
    ) -> ActiveActiveIngestResult:
        """
        Arbitrates one incoming regional copy of a tick.

        ``receipt_time`` must come from the same clock domain as every other call on
        this instance (see the module docstring). It defaults to ``time.time()`` only
        when omitted -- ``receipt_time=0.0`` is honoured as a real timestamp, so
        epoch-relative replay harnesses that legitimately start at 0.0 are not silently
        switched onto the wall clock.
        """
        r_id = self._normalise_region(region_id)
        sym = self._normalise_symbol(symbol)
        if receipt_time is None:
            now = time.time()
        else:
            if not isinstance(receipt_time, (int, float)) or isinstance(receipt_time, bool):
                raise ValueError(f"receipt_time must be numeric or None, got {receipt_time!r}")
            now = float(receipt_time)
            if not math.isfinite(now):
                raise ValueError(f"receipt_time must be finite, got {receipt_time!r}")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or not math.isfinite(float(timestamp)):
            raise ValueError(f"timestamp must be a finite number, got {timestamp!r}")

        # Validates price/volume/sequence_id and raises before any state is mutated.
        sig = self.compute_tick_signature(sym, sequence_id, price, volume)

        tick = RegionalTick(
            region_id=r_id,
            symbol=sym,
            sequence_id=sequence_id,
            timestamp=float(timestamp),
            price=float(price),
            volume=float(volume),
            local_receipt_time=now,
        )

        with self._lock:
            state = self._region_state.setdefault(r_id, _RegionState())
            state.messages += 1
            if state.last_receipt_time is None or now > state.last_receipt_time:
                state.last_receipt_time = now

            self._evict_expired_signatures(now)

            existing = self.seen_signatures.get(sig)
            if existing is not None:
                return self._build_duplicate_result(tick, existing, now, state)

            return self._build_first_arrival_result(sig, tick, now, state)

    def _build_duplicate_result(
        self,
        tick: RegionalTick,
        existing: Tuple[float, str],
        now: float,
        state: _RegionState,
    ) -> ActiveActiveIngestResult:
        first_time, first_region = existing
        delta_ms = (now - first_time) * 1000.0
        inverted = now < first_time
        state.duplicates += 1
        same_region = first_region == tick.region_id
        outcome = (ArbitrationOutcome.SAME_REGION_DUPLICATE if same_region
                   else ArbitrationOutcome.CROSS_REGION_DUPLICATE)

        if same_region:
            message = (
                f"Duplicate tick dropped: re-delivered by '{tick.region_id}' "
                f"{delta_ms:.2f}ms after its own first copy (retransmission or replay, "
                f"not a cross-region arbitration win)."
            )
            logger.debug(
                "SAME-REGION DUPLICATE %s seq %s from '%s' (+%.2fms)",
                tick.symbol, tick.sequence_id, tick.region_id, delta_ms,
            )
        elif inverted:
            message = (
                f"Duplicate tick dropped, but '{tick.region_id}' carries an earlier receipt "
                f"time than the already-emitted copy from '{first_region}' "
                f"({delta_ms:.2f}ms). Ticks were processed out of arrival order or the two "
                f"receipt timestamps are not from one clock domain; treat the delta as invalid."
            )
            logger.warning(
                "ARRIVAL ORDER INVERTED %s seq %s: '%s' receipt precedes emitted '%s' by %.2fms",
                tick.symbol, tick.sequence_id, tick.region_id, first_region, -delta_ms,
            )
        else:
            message = (
                f"Duplicate tick dropped. '{first_region}' arrived {delta_ms:.2f}ms "
                f"faster than '{tick.region_id}'."
            )
            logger.debug(
                "CROSS-REGION DUPLICATE %s seq %s: '%s' arrived %.2fms after '%s'",
                tick.symbol, tick.sequence_id, tick.region_id, delta_ms, first_region,
            )

        return ActiveActiveIngestResult(
            symbol=tick.symbol,
            tick=tick,
            is_duplicate=True,
            first_arrived_region=first_region,
            latency_delta_ms=round(delta_ms, 3),
            message=message,
            outcome=outcome,
            arrival_order_inverted=inverted,
        )

    def _build_first_arrival_result(
        self,
        sig: str,
        tick: RegionalTick,
        now: float,
        state: _RegionState,
    ) -> ActiveActiveIngestResult:
        self.seen_signatures[sig] = (now, tick.region_id)
        self._enforce_capacity_bound()
        self.region_win_counts[tick.region_id] = self.region_win_counts.get(tick.region_id, 0) + 1
        self._recent_winners.append(tick.region_id)
        state.first_arrivals += 1

        gap, out_of_order = self._classify_emitted_sequence(tick.symbol, tick.sequence_id)
        self._last_emitted_sequence[tick.symbol] = tick.sequence_id

        message = f"First arrival accepted from '{tick.region_id}'."
        if gap:
            message += (
                f" WARNING: {gap} sequence number(s) missing since the previous emission "
                f"-- lost in every region, arbitration cannot recover them."
            )
            logger.warning(
                "POST-ARBITRATION SEQUENCE GAP %s: %d message(s) missing before seq %s "
                "(lost on all regions; escalate to retransmission/re-snapshot)",
                tick.symbol, gap, tick.sequence_id,
            )
        elif out_of_order:
            message += (
                " WARNING: sequence_id did not advance since the previous emission "
                "(arrival order is not sequence order, or the sequence space reset)."
            )
            logger.warning(
                "OUT-OF-SEQUENCE EMISSION %s: seq %s does not advance previous emission",
                tick.symbol, tick.sequence_id,
            )
        else:
            logger.info(
                "FIRST ARRIVAL %s seq %s from region '%s'",
                tick.symbol, tick.sequence_id, tick.region_id,
            )

        return ActiveActiveIngestResult(
            symbol=tick.symbol,
            tick=tick,
            is_duplicate=False,
            first_arrived_region=tick.region_id,
            latency_delta_ms=0.0,
            message=message,
            outcome=ArbitrationOutcome.FIRST_ARRIVAL,
            emitted_sequence_gap=gap,
            emitted_out_of_order=out_of_order,
        )

    def _classify_emitted_sequence(self, symbol: str, sequence_id: int) -> Tuple[int, bool]:
        """Reports missing/regressed sequence numbers in the *emitted* (post-arbitration) stream."""
        previous = self._last_emitted_sequence.get(symbol)
        if previous is None:
            return 0, False
        if sequence_id <= previous:
            return 0, True
        return sequence_id - previous - 1, False

    # ------------------------------------------------------------------ cache

    def _evict_expired_signatures(self, current_time: float) -> None:
        """Drops signatures older than ``ttl_seconds`` from the front of the cache.

        The cache is insertion-ordered and receipt times are (near-)monotonic, so this
        pops a bounded number of entries per tick instead of rebuilding a list of
        expired keys by scanning the whole cache -- which made per-tick cost grow
        linearly with cache size and, under the concurrent ingest this skill is for,
        raised ``RuntimeError: dictionary changed size during iteration``.
        """
        cutoff = current_time - self.ttl_seconds
        while self.seen_signatures:
            _, (rec_t, _) = next(iter(self.seen_signatures.items()))
            if rec_t > cutoff:
                break
            self.seen_signatures.popitem(last=False)

    def _enforce_capacity_bound(self) -> None:
        """Hard memory bound on the dedup cache.

        Evicting a signature that is still inside its TTL window lets the duplicate
        copy through as a fresh "first arrival", so saturation is a correctness event,
        not just a memory event: ``max_signatures`` must exceed
        ``ttl_seconds x peak messages/second`` across all regions.
        """
        while len(self.seen_signatures) > self.max_signatures:
            self.seen_signatures.popitem(last=False)
            self._capacity_evictions += 1
            if self._capacity_evictions == 1 or self._capacity_evictions % 10_000 == 0:
                logger.warning(
                    "DEDUP CACHE SATURATED at max_signatures=%d (%d in-window evictions): "
                    "duplicates can now leak through as first arrivals. Raise max_signatures "
                    "above ttl_seconds x peak message rate.",
                    self.max_signatures, self._capacity_evictions,
                )

    # ------------------------------------------------------------------ telemetry

    def get_regional_win_statistics(self) -> Dict[str, Any]:
        """Returns per-region arbitration win counts.

        A win rate is an arbitration/latency statistic, **not** a liveness statistic: a
        perfectly healthy region that is consistently 2ms slower wins 0% of the time,
        and a region that has gone completely dark leaves the survivor at 100% -- a
        number that looks identical to normal operation. Use :meth:`get_regional_health`
        to decide whether a region is still delivering.
        """
        with self._lock:
            total = sum(self.region_win_counts.values())
            rolling_total = len(self._recent_winners)
            stats: Dict[str, Any] = {}
            for reg, count in self.region_win_counts.items():
                pct = (count / total * 100.0) if total > 0 else 0.0
                rolling_wins = sum(1 for r in self._recent_winners if r == reg)
                rolling_pct = (rolling_wins / rolling_total * 100.0) if rolling_total > 0 else 0.0
                state = self._region_state.get(reg, _RegionState())
                stats[reg] = {
                    "wins": count,
                    "win_percentage": round(pct, 2),
                    "rolling_wins": rolling_wins,
                    "rolling_win_percentage": round(rolling_pct, 2),
                    "rolling_window": rolling_total,
                    "messages": state.messages,
                    "duplicates": state.duplicates,
                    "last_receipt_time": state.last_receipt_time,
                }
            return stats

    def get_regional_health(self, now: Optional[float] = None) -> Dict[str, RegionHealth]:
        """Classifies each region as ACTIVE / SILENT / NEVER_SEEN by message flow.

        ``now`` must be in the same clock domain as the ``receipt_time`` values passed
        to :meth:`ingest_regional_tick`; it defaults to ``time.time()``, which is only
        correct if receipt times are wall-clock. A region can only be reported
        NEVER_SEEN if it was declared via ``expected_regions`` -- an ingest node that
        never connects at all is otherwise indistinguishable from one that was never
        configured.
        """
        reference = time.time() if now is None else float(now)
        with self._lock:
            rolling_total = len(self._recent_winners)
            health: Dict[str, RegionHealth] = {}
            for reg, state in self._region_state.items():
                if state.last_receipt_time is None:
                    status = RegionStatus.NEVER_SEEN
                    since: Optional[float] = None
                else:
                    since = reference - state.last_receipt_time
                    status = (RegionStatus.SILENT if since > self.silence_threshold_seconds
                              else RegionStatus.ACTIVE)
                rolling_wins = sum(1 for r in self._recent_winners if r == reg)
                health[reg] = RegionHealth(
                    region_id=reg,
                    status=status,
                    messages=state.messages,
                    first_arrivals=state.first_arrivals,
                    duplicates=state.duplicates,
                    rolling_win_percentage=round(
                        (rolling_wins / rolling_total * 100.0) if rolling_total > 0 else 0.0, 2),
                    last_receipt_time=state.last_receipt_time,
                    seconds_since_last_message=None if since is None else round(since, 6),
                )
            return health

    def get_dedup_cache_stats(self) -> Dict[str, Any]:
        """Returns dedup cache occupancy and whether the hard capacity bound has bitten."""
        with self._lock:
            return {
                "size": len(self.seen_signatures),
                "max_signatures": self.max_signatures,
                "ttl_seconds": self.ttl_seconds,
                "capacity_evictions": self._capacity_evictions,
                "saturated": self._capacity_evictions > 0,
            }

    def reset(self) -> None:
        """Clears all arbitration state.

        Call at a session boundary or whenever the venue's sequence space resets (CME
        packet sequence numbers, for example, are per-channel and reset periodically):
        recycled sequence numbers would otherwise collide with cached signatures and
        the continuity flags would report a spurious regression.
        """
        with self._lock:
            self.seen_signatures.clear()
            self.region_win_counts.clear()
            self._recent_winners.clear()
            self._last_emitted_sequence.clear()
            self._capacity_evictions = 0
            for state in self._region_state.values():
                state.messages = 0
                state.first_arrivals = 0
                state.duplicates = 0
                state.last_receipt_time = None
            logger.info("Active-active ingestor state reset.")

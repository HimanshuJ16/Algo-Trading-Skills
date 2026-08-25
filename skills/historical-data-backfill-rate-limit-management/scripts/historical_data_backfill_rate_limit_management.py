"""
historical-data-backfill-rate-limit-management: paced, resumable historical
tick/bar backfill over a rate-limited vendor REST API.

Three mechanisms, each independently useful:

1. **Token bucket pacing** (``TokenBucketLimiter``) — a request is dispatched only
   after a token has actually been *consumed*. When the bucket is empty the caller
   blocks for the computed refill time and then re-checks; it does not proceed on a
   truncated sleep. Capacity is the burst allowance, refill rate is
   ``requests_per_minute / 60`` tokens per second.

2. **Retry with full jitter** (``calculate_backoff_delay``) — AWS Architecture Blog,
   Marc Brooker, "Exponential Backoff And Jitter" (4 Mar 2015)::

       sleep = random(0, min(cap, base * 2 ** attempt))

   Full jitter, not ``min(cap, base * 2**attempt + small_jitter)``: the latter
   degenerates to *exactly* ``cap`` for every client once the exponential term
   exceeds the cap, so a fleet that was throttled together retries together — the
   herd is at its most correlated precisely where the naive form has the least
   jitter left.

3. **Chunk-level checkpointing** (``CheckpointStore``) — chunk state is persisted
   after every terminal chunk outcome, so an interrupted multi-year backfill resumes
   at the first incomplete chunk instead of restarting.

``Retry-After`` handling follows RFC 9110 Sec. 10.2.3: the field is
``HTTP-date / delay-seconds``, so both forms are parsed. A server-supplied value
always overrides the computed backoff — RFC 6585 defines 429 as the rate-limit
signal and Binance, for one, documents that failing to back off after a 429
escalates to an automated IP ban (HTTP 418) scaling "from 2 minutes to 3 days".
A ``Retry-After`` longer than ``max_retry_after_sec`` is deliberately *not* slept
through: the chunk is failed with ``DEFERRED_RETRY_AFTER`` and the checkpoint lets
the operator resume after the ban window, which is the correct action when a vendor
has asked for hours rather than seconds.

Limitations (documented, deliberate):

- **One rate window only.** The limiter models a single requests-per-interval quota.
  Vendors that publish a *daily* cap on top of a per-minute one cannot be satisfied
  by pacing alone — Alpha Vantage's free tier is 25 API requests per day, so a
  multi-year 1-minute-bar backfill is not a pacing problem at all, it is a plan
  problem. Check the daily budget before starting; this engine will not detect it.
- **No request-weight model.** Binance prices each route by a ``weight`` and reports
  consumption in ``X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)``. One token per
  request understates the cost of heavy multi-symbol routes; pass a per-call weight
  through ``TokenBucketLimiter.acquire(tokens=...)`` if the vendor is weight-priced.
- **Per-process.** Limits are enforced per IP or per API key at the vendor, not per
  process. Two backfill processes sharing credentials each believe they hold the
  full quota. Coordinating across processes needs a shared limiter, not this one.
- **The limiter is thread-safe but not fair.** One instance may be shared across a
  worker pool without over-issuing, but there is no queue: a waiting worker can be
  overtaken repeatedly by others. Fine for a backfill, where throughput matters and
  chunk order does not; not a scheduler for latency-sensitive calls.
- **The fetch callback owns all I/O.** No HTTP client, retry-on-connect, or
  authentication is included here; the callback reports outcomes and this engine
  decides pacing, retry, and checkpointing.
"""
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Status codes worth retrying: 429 rate limit (RFC 6585) plus the transient 5xx
#: family. Everything else 4xx is a client defect — a bad symbol, a revoked key, an
#: out-of-entitlement date range — and retrying it only burns quota.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: Binance returns 418 once an IP has been auto-banned for continuing to send
#: requests after 429s. It is retryable only in the sense that the ban expires;
#: in-process retry is never the right response, so it is handled as a deferral.
IP_BAN_STATUS_CODES = frozenset({418})

#: Token comparison tolerance. ``_tokens + deficit`` is not exactly ``deficit``'s
#: target in binary floating point (0.3 + 0.7 == 0.9999999999999999), so an exact
#: ``>=`` test can reject a bucket that has genuinely refilled and spin the acquire
#: loop on vanishing sleeps. One nanosecond of token credit closes that.
_TOKEN_EPSILON = 1e-9

CHUNK_PENDING = "PENDING"
CHUNK_COMPLETED = "COMPLETED"
CHUNK_FAILED = "FAILED"
CHUNK_DEFERRED = "DEFERRED_RETRY_AFTER"


class BackfillConfigurationError(ValueError):
    """Raised when engine or chunk configuration is invalid."""


@dataclass
class BackfillChunk:
    chunk_id: str
    start_date_iso: str
    end_date_iso: str
    status: str = CHUNK_PENDING         # PENDING | COMPLETED | FAILED | DEFERRED_RETRY_AFTER
    retries_count: int = 0
    records_ingested: int = 0
    last_error: str = ""


@dataclass
class FetchOutcome:
    """
    What the fetch callback reports back about one attempt.

    ``status_code`` drives retry classification, so report the vendor's actual HTTP
    status. ``retry_after`` carries the raw header value: per RFC 9110 Sec. 10.2.3 it
    is either delay-seconds or an HTTP-date, and both forms are accepted here.
    ``retryable`` forces a retry for transport-level failures that never produced a
    status code at all (connect timeout, read timeout, reset connection).
    """
    success: bool
    records: int = 0
    status_code: Optional[int] = None
    retry_after: Optional[Union[float, int, str]] = None
    retryable: Optional[bool] = None
    error_message: str = ""

    def is_retryable(self) -> bool:
        if self.success:
            return False
        if self.retryable is not None:
            return bool(self.retryable)
        if self.status_code is None:
            # No status and no explicit flag: treat as permanent rather than
            # hammering a vendor over an error we cannot classify.
            return False
        return self.status_code in RETRYABLE_STATUS_CODES


@dataclass
class BackfillExecutionReport:
    symbol: str
    total_chunks: int
    completed_chunks_count: int
    failed_chunks_count: int
    total_records_ingested: int     # THIS RUN only. Resumed chunks contribute 0 — they
                                    # were ingested by an earlier run. Not a dataset size.
    total_rate_limit_throttles: int
    execution_time_sec: float
    status: str                         # BACKFILL_SUCCESS | BACKFILL_PARTIAL | BACKFILL_FAILED
    audit_notes: str
    resumed_chunks_count: int = 0
    deferred_chunks_count: int = 0
    total_pacing_wait_sec: float = 0.0
    total_backoff_wait_sec: float = 0.0


# --- date chunking ------------------------------------------------------------

def generate_date_chunks(
    start_date_iso: str,
    end_date_iso: str,
    chunk_days: int,
    chunk_id_prefix: str = "CHK",
) -> List[BackfillChunk]:
    """
    Splits ``[start_date_iso, end_date_iso]`` into contiguous, non-overlapping chunks.

    Both bounds are **inclusive**, and consecutive chunks never share a date: chunk
    *n* ends on day *d* and chunk *n+1* starts on day *d+1*. Vendor history endpoints
    are almost universally inclusive of both bounds, so the common hand-rolled version
    — advancing the cursor by ``chunk_days`` and using it as the next start — requests
    the boundary day twice and silently duplicates a full day of bars in the store.
    The final chunk is truncated at ``end_date_iso`` rather than overshooting it.
    """
    if chunk_days < 1:
        raise BackfillConfigurationError(f"chunk_days must be >= 1, got {chunk_days}.")

    try:
        start = date.fromisoformat(start_date_iso)
        end = date.fromisoformat(end_date_iso)
    except ValueError as exc:
        raise BackfillConfigurationError(
            f"start_date_iso/end_date_iso must be ISO-8601 dates (YYYY-MM-DD): {exc}"
        ) from exc

    if end < start:
        raise BackfillConfigurationError(
            f"end_date_iso ({end_date_iso}) precedes start_date_iso ({start_date_iso})."
        )

    chunks: List[BackfillChunk] = []
    cursor = start
    index = 0
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append(
            BackfillChunk(
                chunk_id=f"{chunk_id_prefix}_{index:04d}",
                start_date_iso=cursor.isoformat(),
                end_date_iso=chunk_end.isoformat(),
            )
        )
        cursor = chunk_end + timedelta(days=1)
        index += 1
    return chunks


# --- Retry-After parsing ------------------------------------------------------

def parse_retry_after(value: Optional[Union[float, int, str]]) -> Optional[float]:
    """
    Parses a ``Retry-After`` field into seconds, per RFC 9110 Sec. 10.2.3.

    Accepts both permitted forms — ``delay-seconds`` and ``HTTP-date``. The HTTP-date
    form is resolved against wall-clock UTC (deliberately: the header names an absolute
    instant, not a monotonic offset), and a date already in the past yields ``0.0``.
    Unparseable or negative values return ``None`` so the caller falls back to computed
    backoff rather than treating a malformed header as "retry immediately".
    """
    if value is None:
        return None

    if isinstance(value, bool):        # bool is an int subclass; never a valid delay
        return None
    if isinstance(value, (int, float)):
        # ``inf`` passes a naive ``>= 0`` test and would be slept on; ``nan`` fails
        # every comparison. Neither is a delay.
        return float(value) if math.isfinite(value) and value >= 0 else None

    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None

    text = str(value).strip()
    if not text:
        return None

    try:
        seconds = float(text)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:          # RFC 9110 HTTP-dates are GMT
        target = target.replace(tzinfo=timezone.utc)
    delta = (target - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


# --- token bucket -------------------------------------------------------------

class TokenBucketLimiter:
    """
    Thread-safe token bucket.

    The clock is ``time.monotonic``, never ``time.time``: a wall clock can step
    backwards (NTP correction) and a backward step makes the elapsed interval
    negative, which *removes* tokens from the bucket and inflates the computed wait
    for every subsequent request. ``time.monotonic`` cannot go backwards.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        if rate_per_sec <= 0:
            raise BackfillConfigurationError(
                f"rate_per_sec must be > 0, got {rate_per_sec}; a zero refill rate can "
                "never release a request."
            )
        if capacity < 1:
            raise BackfillConfigurationError(
                f"capacity must be >= 1, got {capacity}; a bucket that cannot hold one "
                "token can never release a request."
            )
        self.rate_per_sec = float(rate_per_sec)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep_func
        self._last_refill = clock()
        self._lock = threading.Lock()

    @property
    def available_tokens(self) -> float:
        with self._lock:
            self._refill_locked()
            return self._tokens

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
        self._last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> float:
        """
        Consumes ``tokens`` if available and returns ``0.0``.

        Returns the seconds until enough tokens exist otherwise, **without** consuming
        anything and without partially debiting the bucket.
        """
        if tokens <= 0:
            raise BackfillConfigurationError(f"tokens must be > 0, got {tokens}.")
        if tokens > self.capacity:
            raise BackfillConfigurationError(
                f"cannot acquire {tokens} tokens from a bucket of capacity "
                f"{self.capacity}; the request can never be satisfied."
            )
        with self._lock:
            self._refill_locked()
            if self._tokens + _TOKEN_EPSILON >= tokens:
                self._tokens = max(0.0, self._tokens - tokens)
                return 0.0
            deficit = tokens - self._tokens
            return deficit / self.rate_per_sec

    def acquire(self, tokens: float = 1.0) -> float:
        """
        Blocks until ``tokens`` have actually been consumed. Returns seconds waited.

        Loops rather than sleeping once and proceeding: with a shared limiter another
        worker may take the token that this wait was sized for, so the post-sleep
        re-check is what makes the limit an enforcement rather than a suggestion.
        """
        waited = 0.0
        while True:
            wait_sec = self.try_acquire(tokens)
            if wait_sec <= 0.0:
                return waited
            self._sleep(wait_sec)
            waited += wait_sec


# --- checkpointing ------------------------------------------------------------

class CheckpointStore:
    """
    In-memory chunk-state store. Persistence backends subclass and override
    ``save``/``load``; the engine only requires these three methods.
    """

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}

    def load(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._state)

    def save(self, chunk: BackfillChunk) -> None:
        self._state[chunk.chunk_id] = asdict(chunk)

    def completed_chunk_ids(self) -> set:
        return {
            cid for cid, rec in self.load().items()
            if rec.get("status") == CHUNK_COMPLETED
        }


class JsonFileCheckpointStore(CheckpointStore):
    """
    JSON-file checkpoint store.

    Writes to a temporary file and then ``os.replace``s it over the target, so a crash
    mid-write leaves the previous checkpoint intact rather than a truncated file that
    would fail to parse on resume — which is the failure mode that costs the whole
    backfill.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    self._state = loaded
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "CHECKPOINT UNREADABLE [%s]: %s. Starting from an empty checkpoint; "
                    "already-ingested chunks will be re-fetched.", path, exc
                )

    def save(self, chunk: BackfillChunk) -> None:
        super().save(chunk)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2)
        os.replace(tmp_path, self.path)


FetchCallback = Callable[[BackfillChunk], Union[FetchOutcome, Tuple[bool, bool, int]]]


def _validate_records(records: Any) -> None:
    """
    Rejects a record count that cannot be real.

    A callback returning a negative count previously flowed straight into
    ``total_records_ingested``, so a buggy fetch could report a *negative* dataset size
    under a ``BACKFILL_SUCCESS`` status. A corrupt count must fail loudly: the audit
    report is the only record of what was actually ingested.
    """
    if isinstance(records, bool) or not isinstance(records, (int, float)):
        raise BackfillConfigurationError(
            f"records must be a non-negative number, got {records!r}."
        )
    if not math.isfinite(records) or records < 0:
        raise BackfillConfigurationError(
            f"records must be a finite non-negative count, got {records!r}."
        )


def _normalize_outcome(raw: Union[FetchOutcome, Sequence[Any]]) -> FetchOutcome:
    """
    Accepts a ``FetchOutcome`` or the legacy ``(success, is_429, records)`` tuple.

    The tuple form carries no ``Retry-After`` and no status detail beyond "was it a
    429", so callers that need server-directed pacing must return ``FetchOutcome``.
    """
    if isinstance(raw, FetchOutcome):
        _validate_records(raw.records)
        return raw
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        success, is_429, records = raw
        _validate_records(records)
        return FetchOutcome(
            success=bool(success),
            records=int(records or 0),
            status_code=429 if is_429 else None,
            retryable=bool(is_429) if not success else None,
        )
    raise BackfillConfigurationError(
        "fetch callback must return a FetchOutcome or a (success, is_429, records) "
        f"tuple, got {type(raw).__name__}."
    )


class HistoricalBackfillManagerEngine:
    """
    Paced, resumable backfill orchestrator: token-bucket request pacing, full-jitter
    retry that honours ``Retry-After``, error classification, and chunk checkpointing.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        max_burst_capacity: int = 10,
        max_retries: int = 3,
        base_retry_delay_sec: float = 1.0,
        max_retry_delay_sec: float = 16.0,
        max_retry_after_sec: float = 300.0,
        checkpoint_store: Optional[CheckpointStore] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        if requests_per_minute <= 0:
            raise BackfillConfigurationError(
                f"requests_per_minute must be > 0, got {requests_per_minute}."
            )
        if max_burst_capacity < 1:
            raise BackfillConfigurationError(
                f"max_burst_capacity must be >= 1, got {max_burst_capacity}."
            )
        if max_retries < 0:
            raise BackfillConfigurationError(f"max_retries must be >= 0, got {max_retries}.")
        if base_retry_delay_sec <= 0:
            raise BackfillConfigurationError(
                f"base_retry_delay_sec must be > 0, got {base_retry_delay_sec}."
            )
        if max_retry_delay_sec < base_retry_delay_sec:
            raise BackfillConfigurationError(
                f"max_retry_delay_sec ({max_retry_delay_sec}) must be >= "
                f"base_retry_delay_sec ({base_retry_delay_sec})."
            )
        if max_retry_after_sec < 0:
            raise BackfillConfigurationError(
                f"max_retry_after_sec must be >= 0, got {max_retry_after_sec}."
            )

        self.requests_per_minute = requests_per_minute
        self.max_burst_capacity = max_burst_capacity
        self.max_retries = max_retries
        self.base_retry_delay_sec = base_retry_delay_sec
        self.max_retry_delay_sec = max_retry_delay_sec
        self.max_retry_after_sec = max_retry_after_sec
        self.checkpoint_store = checkpoint_store

        self.token_fill_rate_hz = requests_per_minute / 60.0
        self._sleep = sleep_func
        self._clock = clock
        self.limiter = TokenBucketLimiter(
            rate_per_sec=self.token_fill_rate_hz,
            capacity=float(max_burst_capacity),
            clock=clock,
            sleep_func=sleep_func,
        )

    # --- pacing ---------------------------------------------------------------

    def acquire_token(self) -> float:
        """
        Non-consuming probe: ``0.0`` if a token was consumed, else seconds to wait.

        Callers that dispatch a request must use :meth:`TokenBucketLimiter.acquire`
        (or :meth:`_pace`) instead — a non-zero return here means *no token was taken*,
        so proceeding to send anyway is exactly the un-paced burst this skill exists to
        prevent.
        """
        return round(self.limiter.try_acquire(1.0), 3)

    def _pace(self) -> float:
        """Blocks until a request slot is genuinely available. Returns seconds waited."""
        return self.limiter.acquire(1.0)

    # --- retry ----------------------------------------------------------------

    def calculate_backoff_delay(
        self,
        attempt: int,
        retry_after_header: Optional[Union[float, int, str]] = None,
    ) -> float:
        """
        Full-jitter backoff, or the server-directed ``Retry-After`` when present.

        ``sleep = random(0, min(cap, base * 2 ** attempt))`` — AWS Architecture Blog,
        Marc Brooker, "Exponential Backoff And Jitter" (2015). The jitter spans the
        whole interval rather than being a small additive term, so the cap does not
        collapse a throttled fleet onto one synchronised retry instant.

        A parseable ``Retry-After`` always wins: the server has stated the reset window,
        and a shorter self-computed delay is what escalates a 429 into an IP ban.
        """
        if attempt < 0:
            raise BackfillConfigurationError(f"attempt must be >= 0, got {attempt}.")

        directed = parse_retry_after(retry_after_header)
        if directed is not None:
            return round(directed, 3)

        ceiling = min(self.max_retry_delay_sec, self.base_retry_delay_sec * (2 ** attempt))
        return round(random.uniform(0.0, ceiling), 3)

    # --- orchestration --------------------------------------------------------

    def execute_backfill(
        self,
        symbol: str,
        chunks: List[BackfillChunk],
        fetch_func: FetchCallback,
    ) -> BackfillExecutionReport:
        """
        Runs every chunk under token-bucket pacing, classified retry, and checkpointing.

        Chunks already recorded ``COMPLETED`` in the checkpoint store are skipped and
        counted as resumed. Every terminal chunk outcome is checkpointed before the
        next chunk starts, so an interrupt costs at most the chunk in flight.
        """
        if not chunks:
            raise BackfillConfigurationError("Chunks list cannot be empty.")
        if not callable(fetch_func):
            raise BackfillConfigurationError("fetch_func must be callable.")

        unique_ids = {c.chunk_id for c in chunks}
        if len(unique_ids) != len(chunks):
            raise BackfillConfigurationError(
                "chunk_id values must be unique; duplicates make checkpoint state "
                "ambiguous and can mark an unfetched chunk as complete."
            )

        start_time = self._clock()
        completed = 0
        failed = 0
        deferred = 0
        resumed = 0
        total_records = 0
        total_throttles = 0
        pacing_wait = 0.0
        backoff_wait = 0.0

        already_done = (
            self.checkpoint_store.completed_chunk_ids() if self.checkpoint_store else set()
        )

        for chunk in chunks:
            if chunk.chunk_id in already_done:
                chunk.status = CHUNK_COMPLETED
                resumed += 1
                completed += 1
                logger.info(
                    "CHUNK RESUMED [%s - %s]: already checkpointed COMPLETED, skipping fetch.",
                    symbol, chunk.chunk_id,
                )
                continue

            attempt = 0
            terminal_status = CHUNK_FAILED

            while True:
                pacing_wait += self._pace()

                try:
                    outcome = _normalize_outcome(fetch_func(chunk))
                except BackfillConfigurationError:
                    raise
                except Exception as exc:                      # noqa: BLE001 - callback owns I/O
                    outcome = FetchOutcome(
                        success=False, retryable=True, error_message=f"{type(exc).__name__}: {exc}"
                    )
                    logger.warning(
                        "FETCH CALLBACK RAISED [%s - %s]: %s. Treated as a retryable "
                        "transport failure.", symbol, chunk.chunk_id, exc,
                    )

                if outcome.success:
                    chunk.records_ingested = outcome.records
                    total_records += outcome.records
                    terminal_status = CHUNK_COMPLETED
                    break

                chunk.last_error = outcome.error_message or f"HTTP {outcome.status_code}"

                if outcome.status_code == 429 or outcome.status_code in IP_BAN_STATUS_CODES:
                    total_throttles += 1

                if outcome.status_code in IP_BAN_STATUS_CODES:
                    # An auto-ban is not a retry condition. Sleeping through a ban that
                    # scales to days inside the request loop wastes the process; the
                    # checkpoint is what lets the operator resume after it expires.
                    logger.error(
                        "IP BAN [%s - %s]: HTTP %s. Backfill deferred; resume after the "
                        "ban window and reduce requests_per_minute before retrying.",
                        symbol, chunk.chunk_id, outcome.status_code,
                    )
                    terminal_status = CHUNK_DEFERRED
                    break

                if not outcome.is_retryable():
                    logger.error(
                        "NON-RETRYABLE FETCH ERROR [%s - %s]: HTTP %s (%s). Failing the "
                        "chunk without retry; retrying a client error only burns quota.",
                        symbol, chunk.chunk_id, outcome.status_code, chunk.last_error,
                    )
                    terminal_status = CHUNK_FAILED
                    break

                if attempt >= self.max_retries:
                    logger.error(
                        "RETRY BUDGET EXHAUSTED [%s - %s]: %d retries against HTTP %s.",
                        symbol, chunk.chunk_id, self.max_retries, outcome.status_code,
                    )
                    terminal_status = CHUNK_FAILED
                    break

                attempt += 1
                chunk.retries_count = attempt
                directed = parse_retry_after(outcome.retry_after)

                if directed is not None and directed > self.max_retry_after_sec:
                    logger.error(
                        "RETRY-AFTER EXCEEDS CEILING [%s - %s]: server asked for %.1fs, "
                        "ceiling is %.1fs. Deferring rather than holding the process open.",
                        symbol, chunk.chunk_id, directed, self.max_retry_after_sec,
                    )
                    terminal_status = CHUNK_DEFERRED
                    break

                delay = self.calculate_backoff_delay(attempt, outcome.retry_after)
                logger.warning(
                    "RATE LIMITED [%s - %s]: HTTP %s, attempt %d/%d, backing off %.2fs (%s).",
                    symbol, chunk.chunk_id, outcome.status_code, attempt, self.max_retries,
                    delay, "server Retry-After" if directed is not None else "full jitter",
                )
                self._sleep(delay)
                backoff_wait += delay

            chunk.status = terminal_status
            if terminal_status == CHUNK_COMPLETED:
                completed += 1
            elif terminal_status == CHUNK_DEFERRED:
                deferred += 1
            else:
                failed += 1

            if self.checkpoint_store is not None:
                self.checkpoint_store.save(chunk)

        exec_time = round(self._clock() - start_time, 3)
        unresolved = failed + deferred

        if unresolved == 0:
            status = "BACKFILL_SUCCESS"
        elif completed > 0:
            status = "BACKFILL_PARTIAL"
        else:
            status = "BACKFILL_FAILED"

        notes = (
            f"HISTORICAL BACKFILL [{symbol}]: {len(chunks)} chunks -- completed {completed} "
            f"(resumed {resumed}), failed {failed}, deferred {deferred}. "
            f"Records = {total_records:,}. Throttles (429/418) = {total_throttles}. "
            f"Pacing wait = {pacing_wait:.2f}s, backoff wait = {backoff_wait:.2f}s. "
            f"Exec time = {exec_time}s."
        )
        logger.info(notes)

        return BackfillExecutionReport(
            symbol=symbol,
            total_chunks=len(chunks),
            completed_chunks_count=completed,
            failed_chunks_count=failed,
            total_records_ingested=total_records,
            total_rate_limit_throttles=total_throttles,
            execution_time_sec=exec_time,
            status=status,
            audit_notes=notes,
            resumed_chunks_count=resumed,
            deferred_chunks_count=deferred,
            total_pacing_wait_sec=round(pacing_wait, 3),
            total_backoff_wait_sec=round(backoff_wait, 3),
        )

"""
redis-streams-multi-consumer-tick-fanout: market-data fanout over Redis Streams.

The module contains two things:

* ``RedisTickFanoutManager`` -- a thin, well-validated wrapper over the
  ``redis-py`` stream commands (``XADD``/``XGROUP CREATE``/``XREADGROUP``/
  ``XACK``/``XCLAIM``/``XAUTOCLAIM``/``XPENDING``). It speaks the *real*
  ``redis-py`` signatures and normalises both RESP2 and RESP3 reply shapes, so
  the same code path works against a live server and against the simulator
  below.
* ``MockRedisStreamEngine`` -- an in-memory simulator that reproduces the
  *documented* Redis consumer-group semantics (last-delivered-id, PEL,
  delivery counters, idle time, KEEPREF trimming, PEL entries whose payload was
  trimmed away). It exists so the fanout, acknowledgement and recovery logic can
  be tested without a server; it is not a Redis implementation and its
  limitations are listed in the ``MockRedisStreamEngine`` docstring.

Delivery is **at-least-once**. Every consumer of this module must be idempotent:
a claimed message is re-delivered, and a crash between processing and ``XACK``
guarantees a second delivery.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "TickData",
    "TickDecodeError",
    "TickValidationError",
    "PendingEntry",
    "TickBatch",
    "RecoveryResult",
    "MockRedisStreamEngine",
    "RedisTickFanoutManager",
    "DEFAULT_MAXLEN",
    "NEW_MESSAGES",
]

#: ``XREADGROUP ... STREAMS key >`` -- "messages never delivered to any other
#: consumer", per the XREADGROUP reference.
NEW_MESSAGES = ">"

#: Engineering default only. No Redis, exchange or regulatory document mandates
#: a stream length; calibrate it from tick rate x replay window (see
#: references/standards.md).
DEFAULT_MAXLEN = 100_000

_REQUIRED_TICK_FIELDS = ("symbol", "last_price", "volume", "timestamp")


class TickValidationError(ValueError):
    """A tick failed field validation before publication."""


class TickDecodeError(ValueError):
    """A stream entry could not be decoded into a :class:`TickData`.

    Raised for missing fields, unparseable numbers, and for the empty payload
    Redis returns when a still-pending entry has been trimmed out of the stream.
    """


# --------------------------------------------------------------------------- #
# Stream IDs
# --------------------------------------------------------------------------- #
def _as_text(value: Any) -> str:
    """Decodes a redis-py reply element to ``str``.

    redis-py returns ``bytes`` unless the client was built with
    ``decode_responses=True``; silently treating those bytes keys as missing is
    how a tick turns into a zero-priced phantom.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value if isinstance(value, str) else str(value)


def _parse_stream_id(stream_id: Any) -> Tuple[int, int]:
    """Parses a ``<ms>-<seq>`` Redis stream ID into a comparable tuple.

    A bare millisecond part is interpreted as sequence ``0``, matching XADD's
    "incomplete ID" rule. String comparison is *not* a valid ordering for stream
    IDs (``"5-9" > "5-10"`` lexicographically), so every comparison in this
    module goes through this function.
    """
    text = _as_text(stream_id)
    if text in ("-", "+"):  # XPENDING range sentinels
        return (0, 0) if text == "-" else (2 ** 63 - 1, 2 ** 63 - 1)
    ms, _, seq = text.partition("-")
    try:
        return (int(ms), int(seq) if seq else 0)
    except ValueError as exc:
        raise ValueError(f"malformed stream ID {stream_id!r}") from exc


def _format_stream_id(parts: Tuple[int, int]) -> str:
    return f"{parts[0]}-{parts[1]}"


def _decode_mapping(fields: Any) -> Dict[str, str]:
    if fields is None:
        return {}
    return {_as_text(k): _as_text(v) for k, v in dict(fields).items()}


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #
@dataclass
class TickData:
    """One market data tick.

    ``timestamp`` is the *event* time in Unix seconds, not an ingest time. It
    defaults to the local wall clock only as a convenience for tests; a feed
    handler must pass the venue timestamp, because Redis stream IDs carry the
    Redis server's clock, not the venue's.
    """

    symbol: str
    last_price: float
    volume: float
    timestamp: float = field(default_factory=time.time)
    #: Opt out of the positive-price rule for instruments that legitimately
    #: quote at or below zero (calendar spreads; CL futures settled at
    #: -$37.63 on 2020-04-20).
    allow_non_positive_price: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.symbol = _as_text(self.symbol).strip()
        if not self.symbol:
            raise TickValidationError("symbol must be a non-empty string")
        self.last_price = float(self.last_price)
        self.volume = float(self.volume)
        self.timestamp = float(self.timestamp)
        for name, value in (
            ("last_price", self.last_price),
            ("volume", self.volume),
            ("timestamp", self.timestamp),
        ):
            if not math.isfinite(value):
                raise TickValidationError(f"{name} must be finite, got {value!r}")
        if self.volume < 0:
            raise TickValidationError(f"volume must be >= 0, got {self.volume}")
        if self.timestamp <= 0:
            raise TickValidationError(f"timestamp must be > 0, got {self.timestamp}")
        if self.last_price <= 0 and not self.allow_non_positive_price:
            raise TickValidationError(
                f"last_price must be > 0, got {self.last_price}; set "
                "allow_non_positive_price=True for instruments that quote <= 0"
            )

    def to_dict(self) -> Dict[str, str]:
        """Renders the tick as the flat string field map XADD accepts."""
        return {
            "symbol": self.symbol,
            "last_price": repr(self.last_price),
            "volume": repr(self.volume),
            "timestamp": repr(self.timestamp),
        }

    @classmethod
    def from_dict(cls, data: Any, *, allow_non_positive_price: bool = False) -> "TickData":
        """Decodes a stream entry payload.

        Raises :class:`TickDecodeError` on a missing or unparseable field rather
        than defaulting it. An entry that was trimmed while still pending comes
        back from Redis as a null payload (an empty dict once redis-py has
        parsed it) -- defaulting that to ``symbol="" last_price=0.0`` would hand
        a risk monitor a fabricated zero-priced print.
        """
        fields = _decode_mapping(data)
        missing = [f for f in _REQUIRED_TICK_FIELDS if f not in fields]
        if missing:
            raise TickDecodeError(
                f"stream entry missing required field(s) {missing}; "
                f"present fields: {sorted(fields)}"
            )
        try:
            numeric = {
                "last_price": float(fields["last_price"]),
                "volume": float(fields["volume"]),
                "timestamp": float(fields["timestamp"]),
            }
        except (TypeError, ValueError) as exc:
            raise TickDecodeError(f"stream entry has unparseable numeric field: {exc}") from exc
        try:
            return cls(
                symbol=fields["symbol"],
                allow_non_positive_price=allow_non_positive_price,
                **numeric,
            )
        except TickValidationError as exc:
            raise TickDecodeError(f"stream entry failed validation: {exc}") from exc


@dataclass(frozen=True)
class PendingEntry:
    """One row of the Pending Entries List, as reported by XPENDING."""

    message_id: str
    consumer: str
    idle_ms: int
    delivery_count: int


@dataclass(frozen=True)
class TickBatch:
    """Result of one consume call.

    ``malformed`` carries entries that were delivered but could not be decoded.
    They are still in the PEL: decide deliberately whether to dead-letter and
    XACK them or leave them for investigation. Dropping them silently is how a
    poison entry gets re-claimed forever.
    """

    ticks: List[Tuple[str, TickData]] = field(default_factory=list)
    malformed: List[Tuple[str, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.ticks)


@dataclass(frozen=True)
class RecoveryResult:
    """Result of an XAUTOCLAIM sweep."""

    claimed: List[Tuple[str, TickData]] = field(default_factory=list)
    malformed: List[Tuple[str, str]] = field(default_factory=list)
    #: IDs that were pending but no longer exist in the stream. Redis 7.0+
    #: deletes these from the PEL and reports them here; the tick is gone.
    deleted_ids: List[str] = field(default_factory=list)
    #: Cursor for the next XAUTOCLAIM call; ``"0-0"`` means the scan completed.
    cursor: str = "0-0"


# --------------------------------------------------------------------------- #
# In-memory simulator
# --------------------------------------------------------------------------- #
@dataclass
class _PelEntry:
    message_id: str
    consumer: str
    delivery_time_ms: int
    delivery_count: int


@dataclass
class _Group:
    last_delivered_id: str
    pel: Dict[str, _PelEntry] = field(default_factory=dict)


class MockRedisStreamEngine:
    """In-memory simulator of Redis Streams consumer-group semantics.

    Method names, argument order and reply shapes follow ``redis-py`` (RESP2),
    so :class:`RedisTickFanoutManager` drives this and a real client through one
    code path.

    Reproduced, per the Redis command reference:

    * ``>`` returns only entries never delivered to the group, advancing the
      group's last-delivered-id -- an acknowledged entry is **not** redelivered.
    * Any other ID returns that consumer's own pending entries with a greater ID.
    * XCLAIM/XAUTOCLAIM claim only entries idle for at least ``min-idle-time``,
      reset idle time (so two racing claimers cannot both win) and increment the
      delivery counter unless ``JUSTID``.
    * An entry that is in the PEL but no longer in the stream is not claimed and
      is deleted from the PEL (Redis 7.0+); XAUTOCLAIM reports it in the third
      reply element.
    * Trimming uses the ``KEEPREF`` default: entries leave the stream, their PEL
      references remain, and a history read of one returns a null payload.

    Deliberately **not** reproduced -- do not test these against the mock:

    * ``~`` approximate trimming. Real Redis "may leave slightly more entries
      than the threshold"; the mock always trims exactly, so a test asserting an
      exact length under ``approximate=True`` proves nothing about production.
    * BLOCK, cluster behaviour, persistence, replication and failover, memory
      accounting, XDEL/XTRIM/XACKDEL/XNACK, consumer TTL, RESP3 replies.
    * Concurrency. It is a plain object with no locking; it models the
      *semantics* of concurrent consumers, not their interleaving.
    """

    def __init__(self, clock_ms: Optional[Callable[[], int]] = None) -> None:
        self._clock_ms: Callable[[], int] = clock_ms or (lambda: int(time.time() * 1000))
        self.streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self.groups: Dict[str, Dict[str, _Group]] = {}

    # -- helpers ---------------------------------------------------------- #
    def _require_group(self, name: str, groupname: str) -> _Group:
        try:
            return self.groups[name][groupname]
        except KeyError:
            raise ValueError(
                f"NOGROUP No such consumer group '{groupname}' for key name '{name}'"
            ) from None

    def _entry_fields(self, name: str, message_id: str) -> Optional[Dict[str, str]]:
        for entry_id, fields in self.streams.get(name, []):
            if entry_id == message_id:
                return fields
        return None

    # -- commands --------------------------------------------------------- #
    def xadd(
        self,
        name: str,
        fields: Dict[str, Any],
        id: str = "*",
        maxlen: Optional[int] = None,
        approximate: bool = True,
        nomkstream: bool = False,
        **_ignored: Any,
    ) -> Optional[str]:
        if name not in self.streams:
            if nomkstream:
                return None
            self.streams[name] = []
        entries = self.streams[name]
        if id == "*":
            now = self._clock_ms()
            if entries:
                top = _parse_stream_id(entries[-1][0])
                # "Redis guarantees that IDs are always incremental" -- if the
                # clock went backwards, reuse the top ms and bump the sequence.
                new_id = (now, 0) if now > top[0] else (top[0], top[1] + 1)
            else:
                new_id = (now, 0)
        else:
            new_id = _parse_stream_id(id)
            if entries and new_id <= _parse_stream_id(entries[-1][0]):
                raise ValueError(
                    "ERR The ID specified in XADD is equal or smaller than the "
                    "target stream top item"
                )
        message_id = _format_stream_id(new_id)
        entries.append((message_id, {_as_text(k): _as_text(v) for k, v in fields.items()}))
        if maxlen is not None and maxlen >= 0:
            # KEEPREF (the XADD default): PEL references survive trimming.
            del entries[: max(0, len(entries) - maxlen)]
        return message_id

    def xlen(self, name: str) -> int:
        return len(self.streams.get(name, []))

    def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
        **_ignored: Any,
    ) -> bool:
        if name not in self.streams:
            if not mkstream:
                raise ValueError(
                    "ERR The XGROUP subcommand requires the key to exist. Note "
                    "that for CREATE you may want to use the MKSTREAM option to "
                    "create an empty stream automatically."
                )
            self.streams[name] = []
        groups = self.groups.setdefault(name, {})
        if groupname in groups:
            raise ValueError("BUSYGROUP Consumer Group name already exists")
        if id == "$":
            entries = self.streams[name]
            last = entries[-1][0] if entries else "0-0"
        else:
            last = _format_stream_id(_parse_stream_id(id))
        groups[groupname] = _Group(last_delivered_id=last)
        return True

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: Optional[int] = None,
        block: Optional[int] = None,
        noack: bool = False,
        **_ignored: Any,
    ) -> List[List[Any]]:
        if not isinstance(streams, dict):
            raise TypeError("streams must be a dict of {stream_name: stream_id}")
        reply: List[List[Any]] = []
        now = self._clock_ms()
        for name, requested_id in streams.items():
            group = self._require_group(name, groupname)
            entries: List[Tuple[str, Optional[Dict[str, str]]]] = []
            if _as_text(requested_id) == NEW_MESSAGES:
                last = _parse_stream_id(group.last_delivered_id)
                for entry_id, fields in self.streams.get(name, []):
                    if _parse_stream_id(entry_id) <= last:
                        continue
                    entries.append((entry_id, dict(fields)))
                    group.last_delivered_id = entry_id
                    if not noack:
                        group.pel[entry_id] = _PelEntry(entry_id, consumername, now, 1)
                    if count is not None and len(entries) >= count:
                        break
                if entries:
                    reply.append([name, entries])
            else:
                # History read: this consumer's own pending entries, greater ID.
                after = _parse_stream_id(requested_id)
                pending = sorted(
                    (p for p in group.pel.values() if p.consumer == consumername),
                    key=lambda p: _parse_stream_id(p.message_id),
                )
                for pel_entry in pending:
                    if _parse_stream_id(pel_entry.message_id) <= after:
                        continue
                    fields = self._entry_fields(name, pel_entry.message_id)
                    # A trimmed-but-pending entry reads back as a null payload.
                    entries.append(
                        (pel_entry.message_id, dict(fields) if fields is not None else None)
                    )
                    pel_entry.delivery_time_ms = now
                    pel_entry.delivery_count += 1
                    if count is not None and len(entries) >= count:
                        break
                reply.append([name, entries])
        return reply

    def xack(self, name: str, groupname: str, *ids: str) -> int:
        group = self._require_group(name, groupname)
        acked = 0
        for message_id in ids:
            if group.pel.pop(_as_text(message_id), None) is not None:
                acked += 1
        return acked

    def xclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: Sequence[str],
        justid: bool = False,
        **_ignored: Any,
    ) -> List[Any]:
        group = self._require_group(name, groupname)
        now = self._clock_ms()
        claimed: List[Any] = []
        for raw_id in message_ids:
            message_id = _as_text(raw_id)
            pel_entry = group.pel.get(message_id)
            if pel_entry is None:
                continue  # never read by any consumer, or already acknowledged
            fields = self._entry_fields(name, message_id)
            if fields is None:
                # In the PEL but gone from the stream: not claimed, and dropped
                # from the PEL (Redis 7.0+).
                del group.pel[message_id]
                continue
            if now - pel_entry.delivery_time_ms < min_idle_time:
                continue
            pel_entry.consumer = consumername
            pel_entry.delivery_time_ms = now  # resets idle: only one claimer wins
            if not justid:
                pel_entry.delivery_count += 1
            claimed.append(message_id if justid else (message_id, dict(fields)))
        return claimed

    def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        count: Optional[int] = None,
        justid: bool = False,
        **_ignored: Any,
    ) -> List[Any]:
        group = self._require_group(name, groupname)
        limit = 100 if count is None else count
        now = self._clock_ms()
        start = _parse_stream_id(start_id)
        ordered = sorted(group.pel.values(), key=lambda p: _parse_stream_id(p.message_id))
        claimed: List[Any] = []
        deleted: List[str] = []
        cursor = "0-0"
        scanned = 0
        for pel_entry in ordered:
            if _parse_stream_id(pel_entry.message_id) < start:
                continue
            if len(claimed) >= limit or scanned >= limit * 10:
                cursor = pel_entry.message_id
                break
            scanned += 1
            fields = self._entry_fields(name, pel_entry.message_id)
            if fields is None:
                deleted.append(pel_entry.message_id)
                del group.pel[pel_entry.message_id]
                continue
            if now - pel_entry.delivery_time_ms < min_idle_time:
                continue
            pel_entry.consumer = consumername
            pel_entry.delivery_time_ms = now
            if not justid:
                pel_entry.delivery_count += 1
            claimed.append(
                pel_entry.message_id if justid else (pel_entry.message_id, dict(fields))
            )
        return [cursor, claimed, deleted]

    def xpending_range(
        self,
        name: str,
        groupname: str,
        min: str,
        max: str,
        count: int,
        consumername: Optional[str] = None,
        idle: Optional[int] = None,
        **_ignored: Any,
    ) -> List[Dict[str, Any]]:
        group = self._require_group(name, groupname)
        now = self._clock_ms()
        low, high = _parse_stream_id(min), _parse_stream_id(max)
        rows: List[Dict[str, Any]] = []
        for pel_entry in sorted(group.pel.values(), key=lambda p: _parse_stream_id(p.message_id)):
            if not low <= _parse_stream_id(pel_entry.message_id) <= high:
                continue
            idle_ms = now - pel_entry.delivery_time_ms
            if idle is not None and idle_ms < idle:
                continue
            if consumername is not None and pel_entry.consumer != consumername:
                continue
            rows.append(
                {
                    "message_id": pel_entry.message_id,
                    "consumer": pel_entry.consumer,
                    "time_since_delivered": idle_ms,
                    "times_delivered": pel_entry.delivery_count,
                }
            )
            if len(rows) >= count:
                break
        return rows


# --------------------------------------------------------------------------- #
# Reply normalisation (RESP2 list vs RESP3 map)
# --------------------------------------------------------------------------- #
def _looks_like_entry(candidate: Any) -> bool:
    return (
        isinstance(candidate, (list, tuple))
        and len(candidate) == 2
        and isinstance(candidate[1], (dict, type(None)))
    )


def _iter_stream_entries(response: Any) -> Iterator[Tuple[str, Optional[Dict[str, str]]]]:
    """Yields ``(id, fields)`` from an XREADGROUP reply.

    redis-py returns ``[[name, entries], ...]`` under RESP2 and
    ``{name: [entries]}`` under RESP3 (``protocol=3``); a client configured for
    the other protocol silently reads zero ticks if one shape is assumed.
    """
    if not response:
        return
    chunks: Iterable[Any]
    if isinstance(response, dict):
        chunks = response.values()
    else:
        chunks = [
            item[1] for item in response if isinstance(item, (list, tuple)) and len(item) == 2
        ]
    for chunk in chunks:
        if not chunk:
            continue
        for element in chunk:
            if _looks_like_entry(element):
                yield _as_text(element[0]), element[1]
            elif isinstance(element, (list, tuple)):  # RESP3 wraps entries once more
                for nested in element:
                    if _looks_like_entry(nested):
                        yield _as_text(nested[0]), nested[1]


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class RedisTickFanoutManager:
    """Publishes ticks to a Redis Stream and drives consumer-group recovery.

    One instance is bound to one stream. Delivery is at-least-once; consumers
    must be idempotent.
    """

    def __init__(
        self,
        redis_client: Any = None,
        stream_name: str = "market_ticks",
        maxlen: Optional[int] = DEFAULT_MAXLEN,
        approximate_trim: bool = True,
        allow_non_positive_price: bool = False,
    ) -> None:
        if not stream_name or not str(stream_name).strip():
            raise ValueError("stream_name must be a non-empty string")
        if maxlen is not None and maxlen < 0:
            raise ValueError(f"maxlen must be None or >= 0, got {maxlen}")
        self.stream_name = stream_name
        self.redis = redis_client if redis_client is not None else MockRedisStreamEngine()
        self.maxlen = maxlen
        self.approximate_trim = approximate_trim
        self.allow_non_positive_price = allow_non_positive_price

    # -- publish ---------------------------------------------------------- #
    def publish_tick(self, tick: TickData, maxlen: Optional[int] = None) -> str:
        """Publishes one tick via XADD and returns its stream ID.

        ``approximate_trim=True`` emits ``MAXLEN ~ n``, which Redis documents as
        leaving "a few tens more" entries than the threshold; pass
        ``approximate_trim=False`` when the cap must be exact, at a trimming
        cost.
        """
        if not isinstance(tick, TickData):
            raise TypeError(f"tick must be a TickData, got {type(tick).__name__}")
        effective_maxlen = self.maxlen if maxlen is None else maxlen
        message_id = self.redis.xadd(
            self.stream_name,
            tick.to_dict(),
            maxlen=effective_maxlen,
            approximate=self.approximate_trim,
        )
        if message_id is None:
            raise RuntimeError(f"XADD to stream '{self.stream_name}' returned no ID")
        message_id = _as_text(message_id)
        logger.debug(
            "Published %s to stream '%s' as %s", tick.symbol, self.stream_name, message_id
        )
        return message_id

    # -- groups ----------------------------------------------------------- #
    def create_consumer_group(self, group_name: str, start_id: str = "$") -> bool:
        """Creates a consumer group, returning ``True`` if it was created now.

        ``start_id`` defaults to ``"$"`` (redis-py's default): the group sees
        only ticks published *after* creation. Pass ``"0"`` to replay everything
        still in the stream. Only ``BUSYGROUP`` is treated as benign -- a
        connection or permission error must not be swallowed as "already
        exists", or consumption starts against a group that does not exist.
        """
        if not group_name or not str(group_name).strip():
            raise ValueError("group_name must be a non-empty string")
        try:
            self.redis.xgroup_create(self.stream_name, group_name, id=start_id, mkstream=True)
        except Exception as exc:  # noqa: BLE001 - re-raised unless BUSYGROUP
            if "BUSYGROUP" not in str(exc).upper():
                raise
            logger.debug(
                "Consumer group '%s' already exists on '%s'", group_name, self.stream_name
            )
            return False
        logger.info(
            "Consumer group '%s' created on stream '%s' at %s",
            group_name, self.stream_name, start_id,
        )
        return True

    # -- consume ---------------------------------------------------------- #
    def consume_batch(
        self,
        group_name: str,
        consumer_name: str,
        count: int = 10,
        start_id: str = NEW_MESSAGES,
    ) -> TickBatch:
        """Reads a batch for one consumer, separating undecodable entries.

        ``start_id`` is ``">"`` for new messages. Pass ``"0"`` after a restart to
        re-read this consumer's own pending entries before rejoining the live
        feed, as the XREADGROUP reference recommends.
        """
        if count <= 0:
            raise ValueError(f"count must be > 0, got {count}")
        response = self.redis.xreadgroup(
            group_name, consumer_name, {self.stream_name: start_id}, count=count
        )
        batch = TickBatch()
        for message_id, fields in _iter_stream_entries(response):
            try:
                batch.ticks.append(
                    (
                        message_id,
                        TickData.from_dict(
                            fields, allow_non_positive_price=self.allow_non_positive_price
                        ),
                    )
                )
            except TickDecodeError as exc:
                logger.error(
                    "Undecodable entry %s on '%s' group '%s': %s",
                    message_id, self.stream_name, group_name, exc,
                )
                batch.malformed.append((message_id, str(exc)))
        return batch

    def consume_ticks(
        self, group_name: str, consumer_name: str, count: int = 10
    ) -> List[Tuple[str, TickData]]:
        """Convenience wrapper returning decodable ticks only.

        Undecodable entries are logged and dropped, and they stay in the PEL.
        Use :meth:`consume_batch` in production so they can be dead-lettered.
        """
        return self.consume_batch(group_name, consumer_name, count=count).ticks

    # -- acknowledge ------------------------------------------------------ #
    def acknowledge_tick(self, group_name: str, msg_id: str) -> int:
        """Acknowledges one tick via XACK. Returns the number removed from the PEL."""
        return self.acknowledge_ticks(group_name, msg_id)

    def acknowledge_ticks(self, group_name: str, *msg_ids: str) -> int:
        """Acknowledges several ticks in one XACK round trip.

        XACK is idempotent: re-acknowledging an entry returns 0, it is not an
        error. A return of 0 for a first-time ACK means the entry was already
        claimed by another consumer or was never pending.
        """
        if not msg_ids:
            return 0
        return int(self.redis.xack(self.stream_name, group_name, *msg_ids))

    # -- recovery --------------------------------------------------------- #
    def pending_summary(
        self,
        group_name: str,
        count: int = 100,
        min_idle_ms: Optional[int] = None,
        consumer_name: Optional[str] = None,
    ) -> List[PendingEntry]:
        """Inspects the PEL via XPENDING. This is how stale IDs are discovered."""
        rows = self.redis.xpending_range(
            self.stream_name,
            group_name,
            min="-",
            max="+",
            count=count,
            consumername=consumer_name,
            idle=min_idle_ms,
        )
        return [
            PendingEntry(
                message_id=_as_text(row["message_id"]),
                consumer=_as_text(row["consumer"]),
                idle_ms=int(row["time_since_delivered"]),
                delivery_count=int(row["times_delivered"]),
            )
            for row in rows or []
        ]

    def find_poison_entries(
        self, group_name: str, max_delivery_count: int, count: int = 100
    ) -> List[PendingEntry]:
        """Returns pending entries delivered more than ``max_delivery_count`` times.

        XCLAIM increments the delivery counter, so an entry that crashes every
        consumer that touches it is otherwise reclaimed forever. Dead-letter
        these instead of re-claiming them.
        """
        if max_delivery_count < 1:
            raise ValueError(f"max_delivery_count must be >= 1, got {max_delivery_count}")
        return [
            entry
            for entry in self.pending_summary(group_name, count=count)
            if entry.delivery_count > max_delivery_count
        ]

    def claim_stale_ticks(
        self, group_name: str, new_consumer_name: str, min_idle_ms: float, msg_ids: List[str]
    ) -> List[Tuple[str, TickData]]:
        """Claims specific stale entries via XCLAIM.

        Fewer entries than requested is normal, not an error: XCLAIM skips
        entries that are not in the PEL, that are not idle long enough, and
        those whose payload was trimmed away (which it also deletes from the
        PEL). Claiming does **not** stop the previous owner -- a consumer that
        was merely paused, not dead, can still be mid-processing, so processing
        must be idempotent.
        """
        if min_idle_ms < 0:
            raise ValueError(f"min_idle_ms must be >= 0, got {min_idle_ms}")
        if not msg_ids:
            return []
        raw = self.redis.xclaim(
            self.stream_name, group_name, new_consumer_name, int(min_idle_ms), list(msg_ids)
        )
        claimed: List[Tuple[str, TickData]] = []
        for entry in raw or []:
            if not _looks_like_entry(entry):
                continue
            message_id = _as_text(entry[0])
            try:
                tick = TickData.from_dict(
                    entry[1], allow_non_positive_price=self.allow_non_positive_price
                )
            except TickDecodeError as exc:
                logger.error("Claimed entry %s is undecodable: %s", message_id, exc)
                continue
            claimed.append((message_id, tick))
            logger.warning(
                "Re-claimed stale tick %s for consumer '%s' on group '%s'",
                message_id, new_consumer_name, group_name,
            )
        if len(claimed) < len(msg_ids):
            logger.info(
                "XCLAIM returned %d of %d requested IDs on group '%s' "
                "(not pending, not idle enough, or trimmed from the stream)",
                len(claimed), len(msg_ids), group_name,
            )
        return claimed

    def recover_stale_ticks(
        self,
        group_name: str,
        new_consumer_name: str,
        min_idle_ms: float,
        count: int = 100,
        start_id: str = "0-0",
    ) -> RecoveryResult:
        """Sweeps the PEL with XAUTOCLAIM (Redis 6.2+).

        Call repeatedly with ``result.cursor`` until it is ``"0-0"``, then keep
        calling periodically: entries that were not idle enough on this pass
        become claimable later. ``deleted_ids`` are entries whose payload no
        longer exists in the stream -- those ticks are unrecoverable, and a
        non-zero count is the signal that trimming is outrunning consumption.
        """
        if min_idle_ms < 0:
            raise ValueError(f"min_idle_ms must be >= 0, got {min_idle_ms}")
        reply = list(
            self.redis.xautoclaim(
                self.stream_name,
                group_name,
                new_consumer_name,
                int(min_idle_ms),
                start_id=start_id,
                count=count,
            )
            or []
        )
        # Redis 6.2 replies with two elements; the deleted-IDs array was added
        # in 7.0. Padding a short reply positionally would turn the cursor
        # string "0-0" into three deleted IDs.
        cursor = reply[0] if len(reply) > 0 else "0-0"
        entries = reply[1] if len(reply) > 1 else []
        deleted = reply[2] if len(reply) > 2 else []
        if not isinstance(deleted, (list, tuple)):
            deleted = []
        result = RecoveryResult(
            cursor=_as_text(cursor), deleted_ids=[_as_text(d) for d in deleted]
        )
        for entry in entries or []:
            if not _looks_like_entry(entry):
                continue
            message_id = _as_text(entry[0])
            try:
                result.claimed.append(
                    (
                        message_id,
                        TickData.from_dict(
                            entry[1], allow_non_positive_price=self.allow_non_positive_price
                        ),
                    )
                )
            except TickDecodeError as exc:
                logger.error("Auto-claimed entry %s is undecodable: %s", message_id, exc)
                result.malformed.append((message_id, str(exc)))
        if result.deleted_ids:
            logger.warning(
                "%d pending entr(ies) on group '%s' were trimmed out of stream '%s' "
                "before acknowledgement and are unrecoverable",
                len(result.deleted_ids), group_name, self.stream_name,
            )
        return result

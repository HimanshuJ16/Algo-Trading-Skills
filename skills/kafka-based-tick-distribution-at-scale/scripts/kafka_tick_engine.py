"""
kafka-based-tick-distribution-at-scale: symbol-keyed partition routing, producer
configuration, and consumer-lag / staleness auditing for a market-data tick topic.

The headline claim of a symbol-keyed tick topic is *per-symbol ordering*: every tick
for one instrument lands on one partition, and a partition is an ordered log. That
claim holds only if three separate things are true at once, and this module exists to
make all three checkable rather than assumed:

    1. Every record carries a key.        -- an empty key is NOT "partition 0"
    2. Every producer hashes that key the SAME way.
    3. The producer cannot reorder on retry.

Point 2 is the one that silently breaks real fleets. There is no single "Kafka
partitioner": the Java client, kafka-python and aiokafka hash keys with **murmur2**,
while librdkafka -- and therefore confluent-kafka-python -- defaults to
``consistent_random``, which is a **CRC32** hash. Two producers in the same fleet
writing ``AAPL`` to the same topic will therefore write it to two different
partitions, and per-symbol ordering is gone with no error raised anywhere. This module
implements both hashes exactly and refuses to let the choice stay implicit.

Verified client behaviour (checked 2026-08):

- **Apache Kafka producer defaults (4.0)**: ``acks=all``, ``enable.idempotence=true``,
  ``max.in.flight.requests.per.connection=5``, ``batch.size=16384``, ``linger.ms=5``,
  ``compression.type=none``. On ``partitioner.class``: "If no partition is specified
  but a key is present, choose a partition based on a hash of the key."
  https://kafka.apache.org/40/generated/producer_config.html
- **Ordering is not a property of keying alone.** Kafka documents, verbatim: "if this
  configuration is set to be greater than 1 and ``enable.idempotence`` is set to
  false, there is a risk of message reordering after a failed send due to retries
  (i.e., if retries are enabled); if retries are disabled or if ``enable.idempotence``
  is set to true, ordering will be preserved." Idempotence in turn "requires
  ``max.in.flight.requests.per.connection`` to be less than or equal to 5 ..
  ``retries`` to be greater than 0, and ``acks`` must be 'all'." (same source)
- **librdkafka / confluent-kafka defaults** differ on every one of those axes:
  ``partitioner=consistent_random``, ``enable.idempotence=false``, ``acks=-1``,
  ``max.in.flight.requests.per.connection=1000000``, ``batch.size=1000000``,
  ``linger.ms=5``. Its documented partitioner list: "``consistent`` - CRC32 hash of
  key (Empty and NULL keys are mapped to single partition), ``consistent_random`` -
  CRC32 hash of key (Empty and NULL keys are randomly partitioned), ``murmur2`` -
  Java Producer compatible Murmur2 hash of key .. ``murmur2_random`` .. functionally
  equivalent to the default partitioner in the Java Producer."
  https://github.com/confluentinc/librdkafka/blob/master/CONFIGURATION.md
  Under the shipped default, an **empty key is randomly partitioned** -- which is the
  precise mechanism by which a blank symbol destroys ordering without raising.
- **Partition expansion rehashes keys.** Apache Kafka's FAQ notes that if the number
  of partitions changes the same-key-same-partition guarantee "may no longer hold",
  and KIP-253 states plainly: "this in-order delivery is not guaranteed if we expand
  partition of the topic."
  https://cwiki.apache.org/confluence/display/KAFKA/FAQ
- **Client-side lag metrics are not committed-offset lag.** Kafka's ``records-lag-max``
  carries the note "This is based on current offset and not committed offset", so Kafka
  itself treats the two bases as distinct quantities differing by up to one commit
  interval of records. Kafka's operations guide does not spell out the exact semantics
  of the ``CURRENT-OFFSET`` column printed by ``kafka-consumer-groups.sh``, so confirm
  which basis your own tooling reports rather than assuming -- that is what
  ``offset_basis`` records.
  https://kafka.apache.org/40/generated/consumer_metrics.html

Limitations (read before acting on an output):

- **This module does not talk to a broker.** It routes and audits offsets you supply.
  Its partition indices are predictions of where a correctly configured client would
  publish; they are only correct if the producer sends the *same* key bytes this
  module normalizes to, with the *same* partitioner.
- **Lag in messages is not staleness in time.** 10,000 ticks is sub-second on a
  mega-cap and half a session on an illiquid name. ``max_lag_threshold_ticks`` is a
  capacity guardrail; ``max_tick_age_ms`` is the staleness guardrail. Only the second
  one answers "am I about to trade on an old quote?".
- **Every numeric default here is a tunable, not a standard.** No exchange, regulator
  or Kafka document mandates 128 KB batches, 5 ms linger, a 10,000-tick lag limit, or
  a 2.0 skew ratio. Calibrate them per venue and per instrument universe.
- **Skew auditing is only meaningful with many symbols.** With fewer distinct symbols
  than partitions, an uneven partition load is arithmetically forced, not a defect,
  so the skew status is suppressed rather than reported as a false alarm.
"""
import logging
import time
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------
# Partitioner identities
# --------------------------------------------------------------------------------

#: CRC32 hash of the key bytes. Reproduces librdkafka's ``consistent`` and (for a
#: non-empty key) ``consistent_random`` partitioners -- i.e. the shipped default of
#: confluent-kafka-python. NOT what the Java client does.
PARTITIONER_CRC32 = "crc32"

#: Java-compatible murmur2 hash of the key bytes. Reproduces the Apache Kafka Java
#: producer, kafka-python and aiokafka. NOT the confluent-kafka default.
PARTITIONER_MURMUR2 = "murmur2"

SUPPORTED_PARTITIONERS: Tuple[str, ...] = (PARTITIONER_CRC32, PARTITIONER_MURMUR2)

#: Which hash each client library uses for a keyed record *out of the box*. Written
#: down because assuming a single "Kafka default" is the most common way a fleet ends
#: up splitting one symbol across two partitions.
CLIENT_DEFAULT_PARTITIONER: Dict[str, str] = {
    "java": PARTITIONER_MURMUR2,
    "kafka-python": PARTITIONER_MURMUR2,
    "aiokafka": PARTITIONER_MURMUR2,
    "confluent-kafka": PARTITIONER_CRC32,
    "librdkafka": PARTITIONER_CRC32,
}

# --------------------------------------------------------------------------------
# Offset basis for the lag audit
# --------------------------------------------------------------------------------

#: Lag measured against the consumer group's *committed* offset, i.e. the group-offset
#: view. Inflated by up to one commit interval of records for a consumer that has
#: processed but not yet committed.
OFFSET_BASIS_COMMITTED = "COMMITTED"

#: Lag measured against the consumer's *current position* -- what the client-side
#: ``records-lag`` metric family reports. This is the one that tracks real processing
#: progress, and the one to prefer for a staleness alarm.
OFFSET_BASIS_CURRENT = "CURRENT_POSITION"

SUPPORTED_OFFSET_BASES: Tuple[str, ...] = (OFFSET_BASIS_COMMITTED, OFFSET_BASIS_CURRENT)

# --------------------------------------------------------------------------------
# Report statuses, most severe first
# --------------------------------------------------------------------------------

#: The supplied offsets are internally impossible (a consumer ahead of the log end).
#: The lag number computed from them means nothing, so this outranks a lag warning:
#: a broken measurement must never be reported as either healthy or alarming.
STATUS_OFFSET_INCONSISTENCY = "OFFSET_INCONSISTENCY_WARNING"
STATUS_CONSUMER_LAG = "CONSUMER_LAG_WARNING"

#: A tick timestamped ahead of this host's clock. The staleness check subtracts one
#: from the other, so skew does not merely distort the age -- it drives it negative,
#: which can never exceed a positive budget. The staleness guard therefore switches
#: itself off precisely when the clocks it depends on are wrong, so skew is reported
#: rather than allowed to read as "fresh".
STATUS_CLOCK_SKEW = "CLOCK_SKEW_WARNING"

STATUS_STALE_TICKS = "STALE_TICK_WARNING"
STATUS_PARTITION_UNBALANCED = "PARTITION_UNBALANCED_WARNING"
STATUS_HEALTHY = "KAFKA_STREAM_HEALTHY"

_NANOS_PER_MILLI = 1_000_000

_MURMUR2_SEED = 0x9747B28C
_MURMUR2_M = 0x5BD1E995
_MURMUR2_R = 24
_UINT32 = 0xFFFFFFFF


# --------------------------------------------------------------------------------
# Hashes
# --------------------------------------------------------------------------------

def murmur2(data: bytes) -> int:
    """
    Apache Kafka's ``Utils.murmur2``, returning a **signed** 32-bit value exactly as
    the Java implementation does.

    Ported line-for-line from ``clients/src/main/java/org/apache/kafka/common/utils/
    Utils.java`` (seed ``0x9747b28c``, ``m = 0x5bd1e995``, ``r = 24``, little-endian
    4-byte reads, switch fall-through over the tail bytes). The signed return matters:
    Kafka's own published test vectors are negative, and ``h & 0x7fffffff``
    -- never ``abs()`` -- is the documented way to make the value non-negative.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"murmur2 expects bytes, got {type(data).__name__}.")

    length = len(data)
    h = (_MURMUR2_SEED ^ length) & _UINT32
    whole_words = length >> 2

    for i in range(whole_words):
        i4 = i << 2
        # Little-endian 4-byte read, matching Kafka's LITTLE_ENDIAN INT_HANDLE.
        k = (
            data[i4]
            | (data[i4 + 1] << 8)
            | (data[i4 + 2] << 16)
            | (data[i4 + 3] << 24)
        )
        k = (k * _MURMUR2_M) & _UINT32
        k ^= k >> _MURMUR2_R          # Java's >>> on an int is a logical shift here
        k = (k * _MURMUR2_M) & _UINT32
        h = (h * _MURMUR2_M) & _UINT32
        h ^= k

    index = whole_words << 2
    remaining = length - index
    # Mirrors the Java switch fall-through: case 3 -> case 2 -> case 1 -> h *= m.
    if remaining == 3:
        h ^= data[index + 2] << 16
    if remaining >= 2:
        h ^= data[index + 1] << 8
    if remaining >= 1:
        h ^= data[index]
        h = (h * _MURMUR2_M) & _UINT32

    h ^= h >> 13
    h = (h * _MURMUR2_M) & _UINT32
    h ^= h >> 15
    h &= _UINT32

    return h - 0x1_0000_0000 if h & 0x8000_0000 else h


def partition_for_key(key: bytes, num_partitions: int, partitioner: str) -> int:
    """
    Map key bytes to a partition index using the named client hash.

    ``crc32``   -> ``zlib.crc32(key) % num_partitions`` (librdkafka ``consistent``).
    ``murmur2`` -> ``(murmur2(key) & 0x7fffffff) % num_partitions`` (Java client,
    kafka-python, aiokafka, and librdkafka's ``murmur2``).
    """
    if num_partitions < 1:
        raise ValueError(f"num_partitions must be >= 1, got {num_partitions}.")
    if partitioner == PARTITIONER_CRC32:
        return zlib.crc32(key) % num_partitions
    if partitioner == PARTITIONER_MURMUR2:
        return (murmur2(key) & 0x7FFFFFFF) % num_partitions
    raise ValueError(
        f"Unknown partitioner {partitioner!r}; expected one of {SUPPORTED_PARTITIONERS}."
    )


def normalize_symbol_key(symbol: str) -> str:
    """
    Canonicalize a ticker into the exact string that MUST be used as the record key.

    The broker hashes the bytes the producer actually sends, not the bytes this module
    normalized. If routing is computed from ``"aapl"`` but the record is published with
    key ``b"aapl"`` while another service publishes ``b"AAPL"``, the two land on
    different partitions and per-symbol ordering is silently lost. Normalize once, at
    the edge, and key off the result.
    """
    if not isinstance(symbol, str):
        raise TypeError(f"symbol must be str, got {type(symbol).__name__}.")
    cleaned = symbol.strip().upper()
    if not cleaned:
        # librdkafka's default consistent_random partitioner maps an empty key to a
        # *random* partition, so an unroutable symbol does not fail loudly downstream
        # -- it scatters that instrument across the topic. Fail here instead.
        raise ValueError(
            "symbol must be a non-empty ticker: an empty record key is randomly "
            "partitioned under librdkafka's default partitioner, which destroys "
            "per-symbol ordering without raising."
        )
    return cleaned


# --------------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------------

@dataclass
class MarketTickPayload:
    symbol: str                         # e.g. 'AAPL'
    timestamp_ns: int                   # Unix timestamp nanoseconds
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    last_price: float
    last_size: int


@dataclass
class KafkaPartitionState:
    partition_id: int
    log_end_offset: int = 0
    committed_offset: int = 0
    total_ticks_routed: int = 0


@dataclass
class KafkaTickDistributionReport:
    total_ticks_processed: int
    num_partitions: int
    assigned_partition_id: int          # partition of the LAST tick in the batch only
    symbols_partition_map: Dict[str, int]
    max_consumer_lag_ticks: int
    throughput_ticks_per_sec: float     # measured routing rate of THIS call, not broker throughput
    status: str
    audit_notes: str
    partitioner: str = PARTITIONER_CRC32
    offset_basis: str = OFFSET_BASIS_COMMITTED
    lagging_partition_id: int = -1
    partition_tick_counts: Dict[int, int] = field(default_factory=dict)
    partition_skew_ratio: float = 0.0
    skew_audit_applicable: bool = False
    max_tick_age_ms: Optional[float] = None
    out_of_order_symbols: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------

class KafkaTickDistributionEngine:
    """
    Symbol-keyed tick routing plus a consumer-lag / staleness / skew audit for a
    market-data Kafka topic.

    This is an offline router and auditor: it computes where a correctly configured
    producer *would* publish each tick and evaluates offsets you hand it. It opens no
    socket. See the module docstring for the client-compatibility rules that make its
    partition predictions valid.
    """

    def __init__(
        self,
        num_partitions: int = 16,
        max_lag_threshold_ticks: int = 10_000,
        batch_size_bytes: int = 131_072,  # 128 KB -- a tunable, not a standard (Kafka default is 16 KB)
        linger_ms: int = 5,
        partitioner: str = PARTITIONER_CRC32,
        offset_basis: str = OFFSET_BASIS_COMMITTED,
        max_tick_age_ms: Optional[float] = None,
        partition_skew_threshold: float = 2.0,
        compression_type: str = "lz4",
        clock_skew_tolerance_ms: float = 0.0,
    ) -> None:
        if not isinstance(num_partitions, int) or isinstance(num_partitions, bool):
            raise TypeError("num_partitions must be an int.")
        if num_partitions < 1:
            raise ValueError(f"num_partitions must be >= 1, got {num_partitions}.")
        if not isinstance(max_lag_threshold_ticks, int) or max_lag_threshold_ticks < 0:
            raise ValueError("max_lag_threshold_ticks must be a non-negative int.")
        if not isinstance(batch_size_bytes, int) or batch_size_bytes < 1:
            raise ValueError("batch_size_bytes must be a positive int.")
        if not isinstance(linger_ms, int) or linger_ms < 0:
            raise ValueError("linger_ms must be a non-negative int.")
        if partitioner not in SUPPORTED_PARTITIONERS:
            raise ValueError(
                f"partitioner must be one of {SUPPORTED_PARTITIONERS}, got {partitioner!r}."
            )
        if offset_basis not in SUPPORTED_OFFSET_BASES:
            raise ValueError(
                f"offset_basis must be one of {SUPPORTED_OFFSET_BASES}, got {offset_basis!r}."
            )
        if max_tick_age_ms is not None and (
            not isinstance(max_tick_age_ms, (int, float)) or max_tick_age_ms <= 0
        ):
            raise ValueError("max_tick_age_ms must be a positive number or None.")
        if partition_skew_threshold < 1.0:
            raise ValueError(
                "partition_skew_threshold is a max/mean ratio and must be >= 1.0."
            )
        if clock_skew_tolerance_ms < 0:
            raise ValueError("clock_skew_tolerance_ms must be non-negative.")

        self.num_partitions = num_partitions
        self.max_lag_threshold_ticks = max_lag_threshold_ticks
        self.batch_size_bytes = batch_size_bytes
        self.linger_ms = linger_ms
        self.partitioner = partitioner
        self.offset_basis = offset_basis
        self.max_tick_age_ms = max_tick_age_ms
        self.partition_skew_threshold = partition_skew_threshold
        self.compression_type = compression_type
        self.clock_skew_tolerance_ms = clock_skew_tolerance_ms

        self.partition_states: Dict[int, KafkaPartitionState] = {
            i: KafkaPartitionState(partition_id=i) for i in range(num_partitions)
        }
        # Highest event timestamp seen per symbol, for the per-symbol ordering check.
        self._last_timestamp_ns: Dict[str, int] = {}

    # -- routing ------------------------------------------------------------------

    def get_symbol_partition_id(self, symbol: str) -> int:
        """
        Deterministic partition index for a ticker under the configured partitioner.

        Valid only if the producer publishes ``normalize_symbol_key(symbol)`` as the
        record key using the matching client partitioner -- see
        :meth:`build_producer_config` and :meth:`diagnose_partitioner_divergence`.
        """
        key = normalize_symbol_key(symbol).encode("utf-8")
        return partition_for_key(key, self.num_partitions, self.partitioner)

    def diagnose_partitioner_divergence(
        self, symbols: List[str]
    ) -> Dict[str, Tuple[int, int]]:
        """
        Symbols that a CRC32 client and a murmur2 client would route differently.

        Run this before mixing client libraries on one topic. Every entry is a symbol
        whose ordering guarantee is void across the fleet, mapping to
        ``(crc32_partition, murmur2_partition)``. An empty result means the two
        families agree *at this partition count only* -- it is not a licence to leave
        the partitioner implicit.
        """
        divergent: Dict[str, Tuple[int, int]] = {}
        for symbol in symbols:
            key_str = normalize_symbol_key(symbol)
            key = key_str.encode("utf-8")
            crc = partition_for_key(key, self.num_partitions, PARTITIONER_CRC32)
            mur = partition_for_key(key, self.num_partitions, PARTITIONER_MURMUR2)
            if crc != mur:
                divergent[key_str] = (crc, mur)
        return divergent

    def symbols_remapped_by_partition_growth(
        self, symbols: List[str], new_num_partitions: int
    ) -> Dict[str, Tuple[int, int]]:
        """
        Blast radius of a partition-count change, as ``{symbol: (old, new)}``.

        Kafka does not reorganize existing data when partitions are added, so every
        symbol listed here has in-flight history stranded on its old partition while
        new ticks arrive on a different one. Per KIP-253, in-order delivery is not
        guaranteed across that transition. Drain or replay those symbols deliberately;
        do not expand a keyed tick topic mid-session and assume ordering holds.
        """
        if not isinstance(new_num_partitions, int) or isinstance(new_num_partitions, bool):
            raise TypeError("new_num_partitions must be an int.")
        if new_num_partitions < 1:
            raise ValueError("new_num_partitions must be >= 1.")
        remapped: Dict[str, Tuple[int, int]] = {}
        for symbol in symbols:
            key_str = normalize_symbol_key(symbol)
            key = key_str.encode("utf-8")
            old = partition_for_key(key, self.num_partitions, self.partitioner)
            new = partition_for_key(key, new_num_partitions, self.partitioner)
            if old != new:
                remapped[key_str] = (old, new)
        return remapped

    # -- producer configuration ---------------------------------------------------

    def build_producer_config(self, client: str = "confluent-kafka") -> Dict[str, object]:
        """
        Producer settings that actually deliver the ordering this skill claims.

        Keying alone does not preserve order. Kafka documents that with
        ``max.in.flight.requests.per.connection > 1``, ``enable.idempotence=false`` and
        retries enabled there is "a risk of message reordering after a failed send due
        to retries". librdkafka ships exactly that combination by default
        (``enable.idempotence=false``, ``max.in.flight=1000000``), so a confluent-kafka
        producer left on defaults can reorder ticks *within* a partition. This method
        emits the corrected set in the naming dialect of the requested client, pinning
        the partitioner explicitly so it never falls back to a library default.

        Raises ``ValueError`` if the configured partitioner cannot be expressed as a
        config value for that client: kafka-python and aiokafka hash with murmur2 and
        offer no CRC32 setting, so CRC32 there needs a custom partitioner callable
        rather than a config key, and silently emitting a murmur2 config would create
        the exact split this module exists to prevent.
        """
        if client not in CLIENT_DEFAULT_PARTITIONER:
            raise ValueError(
                f"Unknown client {client!r}; expected one of "
                f"{tuple(CLIENT_DEFAULT_PARTITIONER)}."
            )

        if client in ("confluent-kafka", "librdkafka"):
            # 'consistent' rather than 'consistent_random': both are CRC32 for a
            # non-empty key, but 'consistent_random' scatters an empty key at random.
            librdkafka_partitioner = (
                "consistent" if self.partitioner == PARTITIONER_CRC32 else "murmur2"
            )
            return {
                "partitioner": librdkafka_partitioner,
                "batch.size": self.batch_size_bytes,
                "linger.ms": self.linger_ms,
                "compression.type": self.compression_type,
                # Ordering preconditions -- all three are required together.
                "enable.idempotence": True,
                "acks": "all",
                "max.in.flight.requests.per.connection": 5,
            }

        if self.partitioner != PARTITIONER_MURMUR2:
            raise ValueError(
                f"Client {client!r} hashes keys with murmur2 and exposes no CRC32 "
                f"partitioner setting, but this engine is configured for "
                f"{self.partitioner!r}. Supply a custom partitioner callable to the "
                f"client, or standardise the fleet on murmur2 -- do not accept the "
                f"library default, which would split each symbol across two partitions."
            )
        return {
            "batch_size": self.batch_size_bytes,
            "linger_ms": self.linger_ms,
            "compression_type": self.compression_type,
            "enable_idempotence": True,
            "acks": "all",
            "max_in_flight_requests_per_connection": 5,
        }

    # -- publish + audit ----------------------------------------------------------

    def publish_and_audit_ticks(
        self,
        ticks: List[MarketTickPayload],
        simulated_consumed_offsets: Optional[Dict[int, int]] = None,
        now_ns: Optional[int] = None,
    ) -> KafkaTickDistributionReport:
        """
        Route a tick batch by symbol key, then audit offsets, staleness and skew.

        ``simulated_consumed_offsets`` is interpreted under this engine's
        ``offset_basis``: committed group offsets, or the consumer's current position
        (the basis Kafka's ``records-lag`` family uses). The two differ by up to one
        commit interval, so confirm which your tooling reports. Pass ``now_ns`` to make
        the staleness check deterministic in tests.

        Input is validated before any partition state is mutated, so a rejected batch
        leaves the engine exactly as it was.
        """
        if not isinstance(ticks, list):
            raise TypeError(f"ticks must be a list, got {type(ticks).__name__}.")
        if not ticks:
            raise ValueError("Tick payload list cannot be empty.")

        # ---- 0. Validate the whole batch first (no partial mutation on failure) ----
        routed: List[Tuple[str, int]] = []
        for position, tick in enumerate(ticks):
            if not isinstance(tick, MarketTickPayload):
                raise TypeError(
                    f"ticks[{position}] must be a MarketTickPayload, got "
                    f"{type(tick).__name__}."
                )
            try:
                symbol = normalize_symbol_key(tick.symbol)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"ticks[{position}] has an unroutable symbol: {exc}"
                ) from exc
            if not isinstance(tick.timestamp_ns, int) or isinstance(tick.timestamp_ns, bool):
                raise TypeError(
                    f"ticks[{position}].timestamp_ns must be an int of Unix nanoseconds."
                )
            if tick.timestamp_ns < 0:
                raise ValueError(f"ticks[{position}].timestamp_ns must be non-negative.")
            routed.append(
                (
                    symbol,
                    partition_for_key(
                        symbol.encode("utf-8"), self.num_partitions, self.partitioner
                    ),
                )
            )

        offsets = self._validated_offsets(simulated_consumed_offsets)

        started = time.perf_counter()
        warnings: List[str] = []

        # ---- 1. Route ticks into partitions ----
        symbols_map: Dict[str, int] = {}
        batch_counts: Dict[int, int] = {p: 0 for p in range(self.num_partitions)}
        out_of_order: List[str] = []
        last_assigned_partition = -1

        for (symbol, p_id), tick in zip(routed, ticks):
            symbols_map[symbol] = p_id
            last_assigned_partition = p_id
            batch_counts[p_id] += 1
            self.partition_states[p_id].log_end_offset += 1
            self.partition_states[p_id].total_ticks_routed += 1

            # Kafka can preserve the order it receives; it cannot repair an order the
            # upstream feed already broke. Surface that instead of inheriting the claim.
            previous = self._last_timestamp_ns.get(symbol)
            if previous is not None and tick.timestamp_ns < previous:
                if symbol not in out_of_order:
                    out_of_order.append(symbol)
            else:
                self._last_timestamp_ns[symbol] = tick.timestamp_ns

        # ---- 2. Apply consumer offsets ----
        for p_id, consumed in offsets.items():
            self.partition_states[p_id].committed_offset = consumed

        # ---- 3. Audit lag ----
        max_lag = 0
        lagging_partition = -1
        inconsistent: List[int] = []
        for p_id, state in self.partition_states.items():
            raw_lag = state.log_end_offset - state.committed_offset
            if raw_lag < 0:
                # A consumer cannot be ahead of the log end. Clamping this to zero is
                # how a monitor wired to the wrong topic, or one reading a reset group,
                # reports "healthy" forever -- so it is reported, not absorbed.
                inconsistent.append(p_id)
            lag = max(0, raw_lag)
            if lag > max_lag:
                max_lag = lag
                lagging_partition = p_id

        # ---- 4. Audit staleness (time, not message count) ----
        oldest_age_ms: Optional[float] = None
        if self.max_tick_age_ms is not None:
            reference_ns = time.time_ns() if now_ns is None else now_ns
            oldest_ns = min(tick.timestamp_ns for tick in ticks)
            oldest_age_ms = (reference_ns - oldest_ns) / _NANOS_PER_MILLI

        # ---- 5. Audit partition skew ----
        distinct_symbols = len(symbols_map)
        skew_applicable = distinct_symbols >= self.num_partitions
        mean_load = len(ticks) / self.num_partitions
        skew_ratio = (max(batch_counts.values()) / mean_load) if mean_load > 0 else 0.0

        # ---- 6. Classify ----
        lag_breached = max_lag > self.max_lag_threshold_ticks
        clock_skewed = (
            oldest_age_ms is not None
            and oldest_age_ms < -self.clock_skew_tolerance_ms
        )
        stale_breached = (
            oldest_age_ms is not None
            and self.max_tick_age_ms is not None
            and oldest_age_ms > self.max_tick_age_ms
        )
        skew_breached = skew_applicable and skew_ratio > self.partition_skew_threshold

        if inconsistent:
            warnings.append(
                f"OFFSET_INCONSISTENCY: partitions {sorted(inconsistent)} report a "
                f"consumed offset ahead of the log end offset under offset_basis="
                f"{self.offset_basis}. The lag figure is not trustworthy."
            )
        if lag_breached:
            warnings.append(
                f"CONSUMER_LAG: partition {lagging_partition} lag ({max_lag:,} ticks) "
                f"exceeds threshold ({self.max_lag_threshold_ticks:,} ticks)."
            )
        if clock_skewed:
            warnings.append(
                f"CLOCK_SKEW: oldest tick is timestamped {abs(oldest_age_ms):,.1f} ms "
                f"AHEAD of this host's clock, beyond the "
                f"{self.clock_skew_tolerance_ms:,.1f} ms tolerance. A negative age can "
                f"never exceed the staleness budget, so the staleness guard is "
                f"effectively disabled until the clocks agree."
            )
        if stale_breached:
            warnings.append(
                f"STALE_TICKS: oldest tick in batch is {oldest_age_ms:,.1f} ms old, "
                f"over the {self.max_tick_age_ms:,.1f} ms budget."
            )
        if skew_breached:
            warnings.append(
                f"PARTITION_SKEW: busiest partition carries {skew_ratio:.2f}x the mean "
                f"load across {distinct_symbols} symbols, over the "
                f"{self.partition_skew_threshold:.2f}x threshold."
            )
        if out_of_order:
            warnings.append(
                f"UPSTREAM_OUT_OF_ORDER: {len(out_of_order)} symbol(s) arrived with a "
                f"decreasing event timestamp ({', '.join(out_of_order[:5])}). Partition "
                f"ordering preserves arrival order; it cannot repair this."
            )

        # Precedence: within a dimension, "this metric is not trustworthy" is reported
        # ahead of a reading taken from it; across dimensions, a confirmed breach beats
        # an unmeasurable condition from a lower-priority one. Every triggered
        # condition is in `warnings` regardless, so precedence never hides one.
        if inconsistent:
            status = STATUS_OFFSET_INCONSISTENCY
        elif lag_breached:
            status = STATUS_CONSUMER_LAG
        elif clock_skewed:
            status = STATUS_CLOCK_SKEW
        elif stale_breached:
            status = STATUS_STALE_TICKS
        elif skew_breached:
            status = STATUS_PARTITION_UNBALANCED
        else:
            status = STATUS_HEALTHY

        if status == STATUS_HEALTHY:
            notes = (
                f"KAFKA STREAM HEALTHY: routed {len(ticks):,} ticks for "
                f"{distinct_symbols:,} symbol(s) across {self.num_partitions} "
                f"partitions via {self.partitioner}. Max consumer lag = {max_lag:,} "
                f"ticks (basis={self.offset_basis})."
            )
        else:
            notes = f"{status}: " + " | ".join(warnings)

        if not skew_applicable:
            notes += (
                f" Skew audit suppressed: {distinct_symbols} distinct symbol(s) across "
                f"{self.num_partitions} partitions cannot fill every partition, so an "
                f"uneven load here is arithmetic, not a defect."
            )

        if status == STATUS_HEALTHY:
            logger.info(notes)
        else:
            logger.warning(notes)

        elapsed = max(time.perf_counter() - started, 1e-9)

        return KafkaTickDistributionReport(
            total_ticks_processed=len(ticks),
            num_partitions=self.num_partitions,
            assigned_partition_id=last_assigned_partition,
            symbols_partition_map=symbols_map,
            max_consumer_lag_ticks=max_lag,
            throughput_ticks_per_sec=len(ticks) / elapsed,
            status=status,
            audit_notes=notes,
            partitioner=self.partitioner,
            offset_basis=self.offset_basis,
            lagging_partition_id=lagging_partition,
            partition_tick_counts=batch_counts,
            partition_skew_ratio=skew_ratio,
            skew_audit_applicable=skew_applicable,
            max_tick_age_ms=oldest_age_ms,
            out_of_order_symbols=out_of_order,
            warnings=warnings,
        )

    # -- internals ----------------------------------------------------------------

    def _validated_offsets(self, offsets: Optional[Dict[int, int]]) -> Dict[int, int]:
        """
        Validate supplied consumer offsets before any state is touched.

        An unknown partition id is rejected rather than skipped: silently dropping it
        means the operator believes a partition is monitored when it is not.
        """
        if offsets is None:
            return {}
        if not isinstance(offsets, dict):
            raise TypeError(
                f"simulated_consumed_offsets must be a dict, got {type(offsets).__name__}."
            )
        validated: Dict[int, int] = {}
        for p_id, consumed in offsets.items():
            if not isinstance(p_id, int) or isinstance(p_id, bool):
                raise TypeError(f"Offset key {p_id!r} must be an int partition id.")
            if p_id not in self.partition_states:
                raise ValueError(
                    f"Offset supplied for partition {p_id}, which is outside this "
                    f"topic's 0..{self.num_partitions - 1} range. Refusing to ignore "
                    f"it: a monitor reading the wrong partition set reports healthy."
                )
            if not isinstance(consumed, int) or isinstance(consumed, bool):
                raise TypeError(f"Offset for partition {p_id} must be an int.")
            if consumed < 0:
                raise ValueError(
                    f"Offset for partition {p_id} must be non-negative, got {consumed}."
                )
            validated[p_id] = consumed
        return validated

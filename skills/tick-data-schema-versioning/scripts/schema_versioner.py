"""
tick-data-schema-versioning:
Explicit schema-version envelopes for internal tick payloads, plus a registry of
migration adapters that move a payload between versions along a *chained* path
and report, per field, exactly what the migration had to invent or throw away.

Design rule: a migration that loses information must say so
-----------------------------------------------------------
``normalize_to_target_version`` returns a ``MigrationResult``, not a bare dict.
That is the point of the module. A migrator that returns only the migrated
payload cannot tell a consumer the difference between a quote the publisher
actually sent and a quote the adapter manufactured, and every one of the
failures below is invisible in a bare dict:

  * **A synthesized quote.** V1 carries one ``price``; V2 carries ``bid`` and
    ``ask``. Any V1 -> V2 upgrade must produce two numbers from one, and the
    honest derivation ``bid = ask = price`` implies a **zero-width spread**. A
    spread model, a TCA run or an adverse-selection metric fed that tick reads a
    perfect market. The value is still emitted -- refusing would make the
    upgrade impossible -- but it is tagged ``NoteKind.SYNTHESIZED_VALUE`` and
    the caller decides.
  * **A silently truncated timestamp.** V1 seconds are an IEEE 754 binary64
    float. At epoch 1.78e9 s that float's ULP is 2.384e-7 s, i.e. ~238 ns, so a
    V1 timestamp *cannot* carry nanosecond resolution however the conversion is
    written. Upgrading is therefore always lossy and is tagged
    ``NoteKind.PRECISION_REDUCED``; the conversion itself goes through
    ``Decimal`` because the obvious ``int(ts_sec * 1e9)`` both scales in float
    and truncates toward zero (1784948000.999999 -> ...998976, off by 24 ns).
  * **A dropped field.** Downgrading V2 -> V1 has nowhere to put
    ``exchange_id``. Dropping it outright makes a V3 -> V1 -> V3 round trip
    destroy data that never needed to be lost, which is the "dropping fields
    during intermediate hop migrations" failure this skill exists to prevent.
    Known fields the target version lacks are parked in the reserved
    ``_carried_fields`` envelope key and restored if a later hop reintroduces
    them; fields belonging to no registered schema are copied through verbatim.

Design rule: never infer a version
----------------------------------
A payload without a ``schema_version`` header raises
``MissingVersionHeaderError``. Defaulting an unversioned payload to V1 and
"migrating" it yields a structurally valid tick whose price, bid, ask and
timestamp are all zero -- a fabricated tick no downstream validator can
distinguish from a real one. Explicit, in-band version headers are what
production wire formats do: the Confluent Schema Registry wire format prefixes a
magic byte and a 4-byte big-endian schema ID before the payload, and the FIX SBE
message header carries ``blockLength``, ``templateId``, ``schemaId`` and
``version`` so a decoder can act at the message's version rather than guess.

Rollout order is the caller's decision, and it is not symmetric
---------------------------------------------------------------
Under the Confluent compatibility taxonomy, BACKWARD compatibility (new readers
read old writers' data) requires **consumers to be upgraded first**, and FORWARD
compatibility (old readers read new writers' data) requires **producers to be
upgraded first**. This module supports both directions, which is what makes a
staged rollout possible, but it does not choose for you: each consumer pins
``target_version`` to the version *its own code* was written against. Pointing a
legacy consumer at "whatever is newest" defeats the mechanism.

Scope
-----
Version envelopes and version-to-version field mapping only. This module does
not validate value ranges or detect schema drift on an inbound vendor feed
(``data-pipeline-schema-contract-testing``), does not reconcile venue field
names or symbol namespaces (``multi-exchange-feed-normalization``), and does not
sequence, deduplicate or gap-check anything
(``sequence-number-gap-detection-for-feeds``).

Thread safety
-------------
Migration is pure: no instance state is read or written on the migration path
except the ``stats()`` counters and the once-per-condition warning set, neither
of which is synchronized. Concurrent use is safe for the payloads; counters may
under-count and a warning may be emitted more than once.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
import collections
import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "SchemaVersionError",
    "MissingVersionHeaderError",
    "UnknownSchemaVersionError",
    "NoMigrationPathError",
    "SchemaConformanceError",
    "NoteKind",
    "MigrationNote",
    "MigrationResult",
    "FieldSpec",
    "TickSchema",
    "TickSchemaVersioner",
    "VersionedTickV1",
    "VersionedTickV2",
    "VersionedTickV3",
    "VERSION_KEY",
    "CARRIED_KEY",
    "LOSSY_NOTE_KINDS",
]

#: Envelope key carrying the payload's schema version. Always an ``int``.
VERSION_KEY = "schema_version"

#: Reserved envelope key holding known fields a downgrade had nowhere to put,
#: so a later upgrade can restore them instead of re-defaulting them.
CARRIED_KEY = "_carried_fields"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class SchemaVersionError(ValueError):
    """Base class for every failure raised by this module."""


class MissingVersionHeaderError(SchemaVersionError):
    """Payload carried no usable ``schema_version`` header.

    Never downgraded to a default. An unversioned payload is not a V1 payload;
    it is a payload of unknown provenance, and guessing produces a fabricated
    tick rather than an error.
    """


class UnknownSchemaVersionError(SchemaVersionError):
    """A version header (or requested target) names no registered schema."""


class NoMigrationPathError(SchemaVersionError):
    """Both versions are registered but no chain of adapters connects them."""


class SchemaConformanceError(SchemaVersionError):
    """Payload does not conform to the schema its own header declares."""


# --------------------------------------------------------------------------
# Migration provenance
# --------------------------------------------------------------------------
class NoteKind(Enum):
    """Why a migrated field cannot be trusted the way a published field can."""

    #: A value derived from insufficient information (V1 price -> V2 bid/ask).
    SYNTHESIZED_VALUE = "synthesized_value"
    #: A value whose representable resolution shrank (ns -> float seconds).
    PRECISION_REDUCED = "precision_reduced"
    #: A field the target schema declares but the source did not carry; the
    #: target schema's declared default was applied, per Avro's rule that a
    #: reader field absent from the writer's schema resolves to its default.
    DEFAULT_APPLIED = "default_applied"
    #: A known field the target schema lacks, parked in ``_carried_fields``.
    FIELD_CARRIED = "field_carried"
    #: A value that is well-formed but economically implausible (crossed quote).
    SUSPECT_VALUE = "suspect_value"


@dataclass(frozen=True)
class MigrationNote:
    """One field-level statement about what a migration hop did."""

    kind: NoteKind
    field: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - diagnostic convenience
        return f"{self.kind.value}:{self.field}: {self.detail}"


#: Note kinds meaning the payload is no longer a faithful copy of what the
#: publisher sent. ``DEFAULT_APPLIED`` is excluded: filling a newly added
#: optional field with its declared default is the defined resolution of a
#: reader field the writer never had, not a loss.
LOSSY_NOTE_KINDS: Tuple[NoteKind, ...] = (
    NoteKind.SYNTHESIZED_VALUE,
    NoteKind.PRECISION_REDUCED,
    NoteKind.FIELD_CARRIED,
)


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of normalizing one payload to one target version."""

    payload: Dict[str, Any]
    source_version: int
    target_version: int
    #: Every version visited, source first, target last. Length 1 = no-op.
    path: Tuple[int, ...]
    notes: Tuple[MigrationNote, ...] = ()
    #: Keys belonging to no registered schema, copied through untouched.
    unknown_fields: Tuple[str, ...] = ()

    @property
    def is_lossless(self) -> bool:
        """True when nothing was invented, truncated or parked."""
        return not any(n.kind in LOSSY_NOTE_KINDS for n in self.notes)

    @property
    def has_synthesized_values(self) -> bool:
        """True when at least one field was derived rather than published.

        Gate anything that reads a spread, a size or a venue on this.
        """
        return any(n.kind is NoteKind.SYNTHESIZED_VALUE for n in self.notes)

    @property
    def has_suspect_values(self) -> bool:
        """True when a value is well-formed but economically implausible."""
        return any(n.kind is NoteKind.SUSPECT_VALUE for n in self.notes)

    def notes_of(self, kind: NoteKind) -> Tuple[MigrationNote, ...]:
        """All notes of one kind, in the order they were raised."""
        return tuple(n for n in self.notes if n.kind is kind)

    def fields_noted(self, kind: NoteKind) -> Tuple[str, ...]:
        """Field names carrying a note of one kind."""
        return tuple(n.field for n in self.notes_of(kind))


# --------------------------------------------------------------------------
# Schema declaration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    """One field of one schema version.

    ``default`` is consulted only when an *upgrade* introduces the field and the
    source payload could not supply it. ``None`` is the honest default for a
    field that did not exist upstream -- the same choice SBE makes, where a
    decoder acting at a lower version returns the null representation for a
    field added in a later version. A numeric stand-in such as ``0.0`` for an
    absent size is indistinguishable from a real zero size.
    """

    name: str
    python_type: type
    required: bool = True
    default: Any = None
    non_negative: bool = False


@dataclass(frozen=True)
class TickSchema:
    """The field contract for one schema version."""

    version: int
    fields: Tuple[FieldSpec, ...]

    def spec(self, name: str) -> FieldSpec:
        """The named field's spec, or ``KeyError`` if this version lacks it."""
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"V{self.version} declares no field {name!r}")

    @property
    def field_names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.fields)


# --------------------------------------------------------------------------
# Reference dataclasses for the three built-in versions
# --------------------------------------------------------------------------
@dataclass
class VersionedTickV1:
    """V1: one price, second-resolution float timestamp."""

    schema_version: int = 1
    symbol: str = ""
    timestamp_sec: float = 0.0
    price: float = 0.0
    volume: float = 0.0


@dataclass
class VersionedTickV2:
    """V2: two-sided quote, integer nanosecond timestamp, venue code.

    ``exchange_id`` defaults to ``None``, not ``"UNKNOWN"`` or ``"US"``: a
    sentinel string joins against a venue reference table as though it were a
    venue, and a wrong default venue is worse than a missing one.
    """

    schema_version: int = 2
    symbol: str = ""
    timestamp_ns: int = 0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    exchange_id: Optional[str] = None


@dataclass
class VersionedTickV3:
    """V3: V2 plus top-of-book sizes, both optional."""

    schema_version: int = 3
    symbol: str = ""
    timestamp_ns: int = 0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    exchange_id: Optional[str] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None


SCHEMA_V1 = TickSchema(
    version=1,
    fields=(
        FieldSpec("symbol", str),
        FieldSpec("timestamp_sec", float),
        FieldSpec("price", float),
        FieldSpec("volume", float, non_negative=True),
    ),
)

SCHEMA_V2 = TickSchema(
    version=2,
    fields=(
        FieldSpec("symbol", str),
        FieldSpec("timestamp_ns", int),
        FieldSpec("bid", float),
        FieldSpec("ask", float),
        FieldSpec("volume", float, non_negative=True),
        FieldSpec("exchange_id", str, required=False, default=None),
    ),
)

SCHEMA_V3 = TickSchema(
    version=3,
    fields=(
        FieldSpec("symbol", str),
        FieldSpec("timestamp_ns", int),
        FieldSpec("bid", float),
        FieldSpec("ask", float),
        FieldSpec("volume", float, non_negative=True),
        FieldSpec("exchange_id", str, required=False, default=None),
        FieldSpec("bid_size", float, required=False, default=None, non_negative=True),
        FieldSpec("ask_size", float, required=False, default=None, non_negative=True),
    ),
)


# --------------------------------------------------------------------------
# Numeric helpers
# --------------------------------------------------------------------------
def _seconds_to_nanos(ts_sec: float) -> int:
    """Convert float epoch seconds to integer nanoseconds without float scaling.

    ``int(ts_sec * 1e9)`` is wrong twice over: the multiply is performed in
    binary64, and ``int()`` truncates toward zero rather than rounding. Scaling
    the shortest round-trip decimal representation of the float and rounding
    half-to-even gives the nearest nanosecond to the value the producer held.

    This cannot *recover* precision the float never had; see
    ``_seconds_quantization_ns``.
    """
    if not math.isfinite(ts_sec):
        raise SchemaConformanceError(
            f"timestamp seconds must be finite, got {ts_sec!r}")
    scaled = Decimal(repr(float(ts_sec))).scaleb(9)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _nanos_to_seconds(ts_ns: int) -> float:
    """Convert integer nanoseconds to float epoch seconds via exact decimal.

    ``ts_ns / 1e9`` rounds the quotient once; the decimal route rounds only in
    the final float conversion and is not affected by the representation of the
    divisor. Either way the result is bounded by binary64 resolution, which the
    caller is told about through a ``PRECISION_REDUCED`` note.
    """
    return float(Decimal(int(ts_ns)).scaleb(-9))


def _seconds_quantization_ns(ts_sec: float) -> float:
    """Spacing, in nanoseconds, between adjacent binary64 values at ``ts_sec``.

    At a present-day epoch (~1.78e9 s) this is ~238 ns, which is why a
    float-seconds field can never be upgraded into a genuine nanosecond field.
    """
    return math.ulp(float(ts_sec)) * 1e9


def _mid_price(bid: float, ask: float) -> float:
    """Midpoint of a two-sided quote, valid for negative prices.

    ``bid + (ask - bid) / 2`` rather than ``(bid + ask) / 2``: the result is
    guaranteed to lie in ``[bid, ask]`` and the intermediate sum cannot
    overflow.

    A positivity guard such as ``if bid > 0 and ask > 0`` is not a validity
    test. Negative outright prices are real: the NYMEX WTI front-month contract
    settled at -$37.63/b on 2020-04-20 after trading as low as -$40.32/b, and
    CME switched options valuation to the Bachelier model days later precisely
    because negative underlyings and strikes had to be supported. Under a
    positivity guard that tick's mid resolves to the *bid*, -40.32, instead of
    -38.975.
    """
    return bid + (ask - bid) / 2.0


# --------------------------------------------------------------------------
# Versioner
# --------------------------------------------------------------------------
#: An adapter maps one payload body to the next version's body, appending any
#: provenance notes it raises. The engine stamps the version header itself.
Adapter = Callable[[Dict[str, Any], List[MigrationNote]], Dict[str, Any]]


class TickSchemaVersioner:
    """Stamps, validates and migrates versioned tick payloads.

    Versions are connected by registered single-hop adapters; a multi-version
    migration runs the shortest chain of hops, so V1 -> V3 executes
    V1 -> V2 -> V3 and accumulates the notes of both hops.

    ``target_version`` is the version *this consumer's code* was written
    against. Pin it explicitly. It is deliberately not "the newest schema
    known": a legacy consumer that follows the newest version defeats the
    staged rollout the envelope exists to enable.
    """

    #: The newest schema this module defines. A default, not a recommendation.
    CURRENT_TARGET_VERSION = 3

    def __init__(self, target_version: int = CURRENT_TARGET_VERSION) -> None:
        self._schemas: Dict[int, TickSchema] = {}
        self._adapters: Dict[Tuple[int, int], Adapter] = {}
        self._counters: "collections.Counter[str]" = collections.Counter()
        self._warned: Set[Tuple[int, int, NoteKind, str]] = set()

        for schema in (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3):
            self.register_schema(schema)
        self.register_adapter(1, 2, self._upgrade_v1_to_v2)
        self.register_adapter(2, 1, self._downgrade_v2_to_v1)
        self.register_adapter(2, 3, self._upgrade_v2_to_v3)
        self.register_adapter(3, 2, self._downgrade_v3_to_v2)

        if target_version not in self._schemas:
            raise UnknownSchemaVersionError(
                f"target_version {target_version!r} is not a registered schema; "
                f"known versions: {sorted(self._schemas)}")
        self.target_version = target_version

    # -- registration ------------------------------------------------------
    def register_schema(self, schema: TickSchema) -> None:
        """Register (or replace) the field contract for one version."""
        self._schemas[schema.version] = schema

    def register_adapter(self, from_version: int, to_version: int,
                         adapter: Adapter) -> None:
        """Register a single-hop adapter. Chains are composed automatically."""
        if from_version == to_version:
            raise ValueError("an adapter must change the version")
        for v in (from_version, to_version):
            if v not in self._schemas:
                raise UnknownSchemaVersionError(
                    f"cannot register an adapter for unregistered version {v}")
        self._adapters[(from_version, to_version)] = adapter

    @property
    def known_versions(self) -> Tuple[int, ...]:
        """Every registered schema version, ascending."""
        return tuple(sorted(self._schemas))

    # -- envelope ----------------------------------------------------------
    def read_version(self, payload: Mapping[str, Any]) -> int:
        """Read the explicit version header, or raise.

        Never infers. ``bool`` is rejected despite being an ``int`` subclass,
        because ``schema_version: True`` would otherwise read as version 1.
        """
        if VERSION_KEY not in payload:
            raise MissingVersionHeaderError(
                f"payload has no {VERSION_KEY!r} header; refusing to infer a "
                f"version from its shape (keys: {sorted(payload)})")
        raw = payload[VERSION_KEY]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise MissingVersionHeaderError(
                f"{VERSION_KEY} must be an int, got "
                f"{type(raw).__name__} ({raw!r})")
        return raw

    def wrap_payload(self, data: Mapping[str, Any], version: int) -> Dict[str, Any]:
        """Validate ``data`` against ``version``'s schema, then stamp the header.

        ``version`` is required, and the body is checked before the header goes
        on. Stamping without checking produces a payload that lies about
        itself: a V1 body labelled ``version=2`` reaches a V2 consumer that
        reads ``bid`` and raises ``KeyError`` at the far end of the pipeline,
        where the cause is no longer visible. Re-stamping a payload that
        already declares a *different* version is refused for the same reason
        -- relabelling is not migration.
        """
        schema = self._schema_for(version)
        existing = data.get(VERSION_KEY)
        if existing is not None and existing != version:
            raise SchemaConformanceError(
                f"payload already declares {VERSION_KEY}={existing!r}; refusing "
                f"to relabel it as {version}. Use normalize_to_target_version "
                f"to change versions.")
        payload = dict(data)
        payload[VERSION_KEY] = version
        self._validate(payload, schema)
        return payload

    # -- migration ---------------------------------------------------------
    def normalize_to_target_version(
        self,
        payload: Mapping[str, Any],
        target_version: Optional[int] = None,
    ) -> MigrationResult:
        """Migrate ``payload`` to ``target_version`` and report what it cost.

        Raises rather than handing back a payload the caller cannot interpret:
        ``MissingVersionHeaderError`` when the envelope has no version,
        ``UnknownSchemaVersionError`` for an unregistered version,
        ``NoMigrationPathError`` when no adapter chain connects the two, and
        ``SchemaConformanceError`` when the body contradicts its own header.

        The returned payload is always a fresh dict, including on the no-op
        path, so a caller mutating the result cannot reach back into the source
        payload.
        """
        target_v = self.target_version if target_version is None else target_version
        target_schema = self._schema_for(target_v)
        source_v = self.read_version(payload)
        source_schema = self._schema_for(source_v)

        body = dict(payload)
        self._validate(body, source_schema)

        known_everywhere = {
            name for s in self._schemas.values() for name in s.field_names}
        unknown = tuple(sorted(
            k for k in body
            if k not in known_everywhere and k not in (VERSION_KEY, CARRIED_KEY)))

        if source_v == target_v:
            self._counters["noop"] += 1
            return MigrationResult(
                payload=body,
                source_version=source_v,
                target_version=target_v,
                path=(source_v,),
                unknown_fields=unknown,
            )

        path = self._migration_path(source_v, target_v)
        notes: List[MigrationNote] = []
        for hop_from, hop_to in zip(path, path[1:]):
            body = self._adapters[(hop_from, hop_to)](body, notes)
            body[VERSION_KEY] = hop_to
            self._counters[f"hop:{hop_from}->{hop_to}"] += 1

        self._validate(body, target_schema)
        self._counters[f"migrate:{source_v}->{target_v}"] += 1
        for note in notes:
            self._counters[f"note:{note.kind.value}"] += 1
            self._warn_once(source_v, target_v, note)

        return MigrationResult(
            payload=body,
            source_version=source_v,
            target_version=target_v,
            path=path,
            notes=tuple(notes),
            unknown_fields=unknown,
        )

    def stats(self) -> Dict[str, int]:
        """Snapshot of migration counters. Unsynchronized; see module docstring."""
        return dict(self._counters)

    # -- internals ---------------------------------------------------------
    def _schema_for(self, version: int) -> TickSchema:
        if isinstance(version, bool) or not isinstance(version, int):
            raise UnknownSchemaVersionError(
                f"schema version must be an int, got "
                f"{type(version).__name__} ({version!r})")
        try:
            return self._schemas[version]
        except KeyError:
            raise UnknownSchemaVersionError(
                f"no schema registered for version {version}; known versions: "
                f"{sorted(self._schemas)}") from None

    def _migration_path(self, source_v: int, target_v: int) -> Tuple[int, ...]:
        """Shortest chain of registered hops from source to target (BFS)."""
        frontier = collections.deque([(source_v,)])
        seen = {source_v}
        while frontier:
            path = frontier.popleft()
            for (hop_from, hop_to) in self._adapters:
                if hop_from != path[-1] or hop_to in seen:
                    continue
                extended = path + (hop_to,)
                if hop_to == target_v:
                    return extended
                seen.add(hop_to)
                frontier.append(extended)
        raise NoMigrationPathError(
            f"no adapter chain connects V{source_v} to V{target_v}; registered "
            f"hops: {sorted(self._adapters)}")

    def _validate(self, body: Mapping[str, Any], schema: TickSchema) -> None:
        """Check a body against the schema its header declares.

        Type checks are exact apart from accepting ``int`` where ``float`` is
        declared (Python's numeric tower). ``bool`` is rejected everywhere:
        ``True`` would otherwise pass as a price of 1.0. A declared-but-absent
        *required* field is an error rather than a defaulted zero -- see the
        module docstring on fabricated ticks.
        """
        for spec in schema.fields:
            if spec.name not in body:
                if spec.required:
                    raise SchemaConformanceError(
                        f"V{schema.version} payload missing required field "
                        f"{spec.name!r}")
                continue
            value = body[spec.name]
            if value is None:
                if spec.required:
                    raise SchemaConformanceError(
                        f"V{schema.version} field {spec.name!r} is required and "
                        f"may not be None")
                continue
            if isinstance(value, bool):
                raise SchemaConformanceError(
                    f"V{schema.version} field {spec.name!r} must be "
                    f"{spec.python_type.__name__}, got bool ({value!r})")
            if spec.python_type is float:
                if not isinstance(value, (int, float)):
                    raise SchemaConformanceError(
                        f"V{schema.version} field {spec.name!r} must be a "
                        f"number, got {type(value).__name__} ({value!r})")
                if not math.isfinite(value):
                    raise SchemaConformanceError(
                        f"V{schema.version} field {spec.name!r} must be finite, "
                        f"got {value!r}")
            elif not isinstance(value, spec.python_type):
                raise SchemaConformanceError(
                    f"V{schema.version} field {spec.name!r} must be "
                    f"{spec.python_type.__name__}, got "
                    f"{type(value).__name__} ({value!r})")
            if spec.non_negative and value < 0:
                raise SchemaConformanceError(
                    f"V{schema.version} field {spec.name!r} must be "
                    f"non-negative, got {value!r}")

    def _warn_once(self, source_v: int, target_v: int,
                   note: MigrationNote) -> None:
        """Log each distinct lossy condition once; counters carry the volume.

        Logging per migrated tick at INFO is not viable on a tick pipeline: at
        even modest tick rates the formatting and I/O dominate the migration
        itself and the log is unreadable. Routine detail goes to DEBUG with
        lazy ``%`` formatting, which costs nothing when the level is off.
        """
        logger.debug("schema migration V%d->V%d note: %s",
                     source_v, target_v, note)
        if note.kind not in LOSSY_NOTE_KINDS:
            return
        key = (source_v, target_v, note.kind, note.field)
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(
            "schema migration V%d->V%d is lossy for field %r (%s): %s "
            "(logged once; see stats() for volume)",
            source_v, target_v, note.field, note.kind.value, note.detail)

    @staticmethod
    def _split_carried(body: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Separate the reserved carry envelope from the payload body.

        The envelope arrives over the wire like everything else, so a malformed
        one is rejected as a conformance failure rather than escaping as a bare
        ``ValueError`` from ``dict()``.
        """
        raw = body.get(CARRIED_KEY)
        if raw is None:
            carried: Dict[str, Any] = {}
        elif isinstance(raw, Mapping):
            carried = dict(raw)
        else:
            raise SchemaConformanceError(
                f"{CARRIED_KEY!r} must be a mapping of field name to value, "
                f"got {type(raw).__name__} ({raw!r})")
        rest = {k: v for k, v in body.items() if k != CARRIED_KEY}
        return rest, carried

    @staticmethod
    def _attach_carried(out: Dict[str, Any], carried: Mapping[str, Any]) -> None:
        if carried:
            out[CARRIED_KEY] = dict(carried)
        else:
            out.pop(CARRIED_KEY, None)

    @staticmethod
    def _passthrough(src: Mapping[str, Any], out: Dict[str, Any],
                     consumed: Iterable[str]) -> None:
        """Copy every key the adapter did not itself handle.

        This is what stops an intermediate hop silently eating a field it does
        not recognize: on a V1 -> V2 -> V3 chain a vendor extension such as
        ``venue_seq`` survives both hops untouched.
        """
        skip = set(consumed) | {VERSION_KEY, CARRIED_KEY}
        for key, value in src.items():
            if key not in skip and key not in out:
                out[key] = value

    def _restore_or_default(self, out: Dict[str, Any], carried: Dict[str, Any],
                            spec: FieldSpec,
                            notes: List[MigrationNote]) -> None:
        """Reinstate a field an earlier downgrade parked, else apply its default."""
        if spec.name in carried:
            out[spec.name] = carried.pop(spec.name)
            return
        out[spec.name] = spec.default
        notes.append(MigrationNote(
            NoteKind.DEFAULT_APPLIED, spec.name,
            f"field introduced by this version; the source could not supply it, "
            f"so the declared default {spec.default!r} was applied"))

    # -- built-in adapters -------------------------------------------------
    def _upgrade_v1_to_v2(self, body: Dict[str, Any],
                          notes: List[MigrationNote]) -> Dict[str, Any]:
        src, carried = self._split_carried(body)
        ts_sec = float(src["timestamp_sec"])
        price = float(src["price"])

        out: Dict[str, Any] = {
            "symbol": src["symbol"],
            "timestamp_ns": _seconds_to_nanos(ts_sec),
            "bid": price,
            "ask": price,
            "volume": float(src["volume"]),
        }

        quantum_ns = _seconds_quantization_ns(ts_sec)
        if quantum_ns > 1.0:
            notes.append(MigrationNote(
                NoteKind.PRECISION_REDUCED, "timestamp_ns",
                f"source was binary64 seconds; adjacent representable values "
                f"are {quantum_ns:.0f} ns apart at this epoch, so the low-order "
                f"nanoseconds were never in the data"))
        for side in ("bid", "ask"):
            notes.append(MigrationNote(
                NoteKind.SYNTHESIZED_VALUE, side,
                f"derived from the single V1 price {price!r}; bid == ask, so "
                f"the implied spread is zero and is not a real quote"))

        self._restore_or_default(
            out, carried, self._schema_for(2).spec("exchange_id"), notes)
        self._passthrough(src, out, consumed=(
            "symbol", "timestamp_sec", "price", "volume"))
        self._attach_carried(out, carried)
        return out

    def _downgrade_v2_to_v1(self, body: Dict[str, Any],
                            notes: List[MigrationNote]) -> Dict[str, Any]:
        src, carried = self._split_carried(body)
        bid = float(src["bid"])
        ask = float(src["ask"])
        ts_ns = int(src["timestamp_ns"])

        if ask < bid:
            notes.append(MigrationNote(
                NoteKind.SUSPECT_VALUE, "price",
                f"crossed quote (bid {bid!r} > ask {ask!r}); the midpoint is "
                f"still computed but the source quote is not a valid market"))

        ts_sec = _nanos_to_seconds(ts_ns)
        out: Dict[str, Any] = {
            "symbol": src["symbol"],
            "timestamp_sec": ts_sec,
            "price": _mid_price(bid, ask),
            "volume": float(src["volume"]),
        }

        roundtrip_ns = _seconds_to_nanos(ts_sec)
        if roundtrip_ns != ts_ns:
            notes.append(MigrationNote(
                NoteKind.PRECISION_REDUCED, "timestamp_sec",
                f"binary64 seconds cannot represent {ts_ns} ns exactly; it "
                f"round-trips to {roundtrip_ns} ns"))
        notes.append(MigrationNote(
            NoteKind.PRECISION_REDUCED, "price",
            f"two-sided quote collapsed to a midpoint; bid {bid!r} and ask "
            f"{ask!r} are not recoverable from it"))

        if src.get("exchange_id") is not None:
            carried["exchange_id"] = src["exchange_id"]
            notes.append(MigrationNote(
                NoteKind.FIELD_CARRIED, "exchange_id",
                f"V1 has no such field; parked in {CARRIED_KEY!r} so a later "
                f"upgrade restores it instead of re-defaulting"))

        self._passthrough(src, out, consumed=(
            "symbol", "timestamp_ns", "bid", "ask", "volume", "exchange_id"))
        self._attach_carried(out, carried)
        return out

    def _upgrade_v2_to_v3(self, body: Dict[str, Any],
                          notes: List[MigrationNote]) -> Dict[str, Any]:
        src, carried = self._split_carried(body)
        out: Dict[str, Any] = {
            "symbol": src["symbol"],
            "timestamp_ns": int(src["timestamp_ns"]),
            "bid": float(src["bid"]),
            "ask": float(src["ask"]),
            "volume": float(src["volume"]),
            "exchange_id": src.get("exchange_id"),
        }
        for name in ("bid_size", "ask_size"):
            self._restore_or_default(
                out, carried, self._schema_for(3).spec(name), notes)

        self._passthrough(src, out, consumed=(
            "symbol", "timestamp_ns", "bid", "ask", "volume", "exchange_id"))
        self._attach_carried(out, carried)
        return out

    def _downgrade_v3_to_v2(self, body: Dict[str, Any],
                            notes: List[MigrationNote]) -> Dict[str, Any]:
        src, carried = self._split_carried(body)
        out: Dict[str, Any] = {
            "symbol": src["symbol"],
            "timestamp_ns": int(src["timestamp_ns"]),
            "bid": float(src["bid"]),
            "ask": float(src["ask"]),
            "volume": float(src["volume"]),
            "exchange_id": src.get("exchange_id"),
        }
        for name in ("bid_size", "ask_size"):
            if src.get(name) is not None:
                carried[name] = src[name]
                notes.append(MigrationNote(
                    NoteKind.FIELD_CARRIED, name,
                    f"V2 has no such field; parked in {CARRIED_KEY!r} so a "
                    f"later upgrade restores it instead of re-defaulting"))

        self._passthrough(src, out, consumed=(
            "symbol", "timestamp_ns", "bid", "ask", "volume", "exchange_id",
            "bid_size", "ask_size"))
        self._attach_carried(out, carried)
        return out

---
name: tick-data-schema-versioning
description: >-
  Use when an internal tick schema changes while consumers run at mixed versions. Stamps
  a schema_version envelope, chains upgrade and downgrade adapters, and marks the fields
  a migration synthesised rather than received.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, schema-versioning, migration-adapters, backward-compatibility, forward-compatibility, event-schema, serialization
  brokers_frameworks: "Confluent Schema Registry; Apache Avro; Protocol Buffers; FIX Simple Binary Encoding (SBE); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an internal tick structure changes — nanosecond timestamps
replacing float seconds, a two-sided quote replacing a single price, top-of-book
sizes being added — and the strategy workers, risk monitors and DB writers that
consume it will **not** all be redeployed at the same instant. During that
window the pipeline carries two or three schema generations at once, and every
message must say which one it is.

`TickSchemaVersioner` stamps and reads an explicit `schema_version` envelope,
migrates a payload to the version the *reading* consumer was written against
along a chained adapter path (V1 → V3 runs V1 → V2 → V3), and returns a
`MigrationResult` that names every field it had to invent, round off, or park.

The provenance is the load-bearing part. Two of the three built-in hops cannot
be lossless, and neither loss is visible in the resulting payload:

- Upgrading V1 → V2 turns one `price` into a `bid` and an `ask`. The honest
  derivation sets both to the price, which means **a spread of exactly zero**.
- Downgrading V2 → V1 collapses a quote to its midpoint and converts integer
  nanoseconds into a binary64 float, whose adjacent representable values are
  ~238 ns apart at a present-day epoch.

A migrator that returns a bare dict hands both of those to a TCA run or a
spread model as though the publisher had sent them.

## When NOT to Use

- **To validate an inbound vendor feed.** Field-type contracts, value bounds and
  drift detection against an external producer are
  `data-pipeline-schema-contract-testing`. This engine checks a payload against
  the schema *its own header declares* — that is a version check, not a data
  quality check.
- **To reconcile venue field names or symbol namespaces.** Mapping Binance's
  `m` flag or Coinbase's maker-side `side` onto one canonical tick is
  `multi-exchange-feed-normalization`. Adapters here deliberately **do not**
  touch `symbol`: rewriting an identifier inside a version hop breaks every
  downstream join, and it is somebody else's job.
- **To migrate an external broker or exchange API version.** Shadow reads,
  canary routing and latched rollback across a vendor's v2 → v3 cutover are
  `broker-api-versioning-migration-playbook`. This skill owns the schema you
  publish internally, which you control on both sides.
- **To migrate a database.** Expand/contract DDL, backfills and reader cutover
  are `zero-downtime-database-schema-migrations`.
- **On a latency-critical hot path in CPython.** Per-payload dict copies,
  `Decimal` timestamp conversion and result objects are a correctness
  reference, not a colocated feed handler. Generate fixed-layout codecs
  (SBE, Protobuf, Avro) for that tier and keep this as the semantics spec.
- **As a substitute for a real serialization format.** If you already publish
  through Avro, Protobuf or SBE, that format's resolution rules govern; use
  this skill's workflow and pitfalls, not its dict plumbing.

## Prerequisites

- **A version-carrying envelope on the wire.** The version must travel with the
  payload, not be inferred from it. Every production format does this: the
  Confluent Schema Registry wire format prefixes a magic byte and a 4-byte
  big-endian schema ID before the encoded payload, and the FIX SBE message
  header carries `blockLength`, `templateId`, `schemaId` and `version`.
- **A declared field contract per version** (`TickSchema` / `FieldSpec` here),
  including which fields are required and what a newly added optional field
  defaults to.
- **A decided rollout order** — see Workflow step 1. This is a deployment
  decision, not a code decision, and the wrong one breaks the pipeline no
  matter how correct the adapters are.
- **A pinned `target_version` per consumer**, equal to the version that
  consumer's own code was written against.
- Python 3.8+, standard library only. Validated on CPython 3.11.

## Workflow

1. **Decide the rollout order before writing the adapter.** The two
   compatibility directions are not interchangeable and each dictates a
   different deployment sequence (Confluent Schema Registry taxonomy):
   - **BACKWARD** — the new schema can read data written by the old one.
     Permits deleting fields and adding fields *with defaults*.
     **Upgrade consumers first.**
   - **FORWARD** — the old schema can read data written by the new one.
     Permits adding fields and deleting fields that have defaults.
     **Upgrade producers first.**
   - **FULL** — both hold, every field has a default, and order does not
     matter. This is what you want and rarely what you have.
   - **Decision point — the guarantee is pairwise unless it is transitive.**
     A non-transitive mode checks only the immediately preceding version. A
     consumer three versions behind is covered by nothing.

2. **Stamp the version at the producer, and validate the body while you do it.**
   `wrap_payload(data, version)` checks the body against that version's schema
   *before* attaching the header.
   - **Decision point — stamping is a claim, not a formality.** A V1 body
     labelled `version=2` does not fail at the producer; it fails inside a V2
     consumer reading `bid`, in a stack trace that no longer contains the
     mislabelling. Relabelling a payload that already declares a different
     version is refused outright — relabelling is not migration.

3. **Read the declared version. Never infer one.** `read_version` raises on a
   missing or non-integer header.
   - **Decision point — an unversioned payload is not a V1 payload.** Treating
     it as the oldest known version and "migrating" it produces a structurally
     valid tick whose symbol, price, bid, ask and timestamp are all defaults —
     a fabricated tick that no downstream validator can distinguish from a
     real one. Refuse it and route it to the dead-letter path.

4. **Migrate toward the consumer's pinned target, not toward the newest
   version.** `normalize_to_target_version(payload, target_version)` resolves
   the shortest chain of registered hops.
   - **Decision point — "latest" is the wrong default for a legacy consumer.**
     A consumer that follows whatever version is newest has opted out of the
     staged rollout the envelope exists to enable.
   - **Decision point — an unreachable or unregistered version is an error, not
     a pass-through.** Returning the payload unmigrated hands the consumer
     fields it cannot read while the header still claims the old version.
     `UnknownSchemaVersionError` and `NoMigrationPathError` are raised instead.

5. **Read the notes before you read the values.** The `MigrationResult`
   carries `is_lossless`, `has_synthesized_values`, `has_suspect_values` and a
   per-field note list.
   - **Decision point — gate on `has_synthesized_values` anywhere a spread, a
     size or a venue matters.** A V1-sourced tick has `bid == ask`. Feed it to
     an effective-spread calculation and the answer is zero, not an error.
   - **Decision point — `SUSPECT_VALUE` is a crossed quote, not a bad
     migration.** The midpoint is still computed; the source quote is what is
     wrong.

6. **Let intermediate hops carry what they cannot represent.** Known fields the
   target version lacks are parked in the reserved `_carried_fields` envelope
   key and restored if a later hop reintroduces them; fields belonging to no
   registered schema are copied through verbatim.
   - **Decision point — a V3 → V1 → V3 round trip must not lose the venue.**
     `bid` and `ask` genuinely cannot survive a hop through a single-price
     schema. `exchange_id`, `bid_size`, `ask_size` and a vendor's `venue_seq`
     never needed to be lost, and an adapter that rebuilds a clean dict loses
     all of them.

7. **Export `stats()` to monitoring, not the log.** Counters are keyed by hop,
   by end-to-end migration and by note kind; each distinct lossy condition
   warns once.
   - **Decision point — a rising `note:synthesized_value` count is the
     rollout's progress bar.** It should fall to zero as producers finish
     upgrading. If it does not, a producer has been forgotten.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inferring the version from the payload's shape.** Key counts and "does it
  have a `bid`" heuristics fail silently the first time an optional field is
  absent or a vendor adds an extension, and they turn an unversioned payload
  into a fabricated one. The header is the only source of truth.
- **Defaulting a missing header to the oldest version.** This is the same bug
  wearing a helpful face. The result is a tick with a zero price that looks
  exactly like a real tick with a zero price.
- **Filling a newly added field with a numeric sentinel.** `0.0` for an absent
  size is indistinguishable from a real zero size, and `"UNKNOWN"` for an
  absent venue joins against a venue reference table as though it were a venue.
  Use the null representation — this is what SBE does, where a decoder acting
  at a lower version returns null for a field added later, and what Avro does,
  where a reader field absent from the writer's schema resolves to its declared
  default.
- **Adding a required field without a default and calling it backward
  compatible.** Under Avro's resolution rules, a reader field with no default
  and no matching writer field is an *error*, not a null. The change is
  BACKWARD-compatible only if the new field has a default.
- **Changing a field's type in place instead of adding a version.** `int` to
  `string` is not a wire-compatible change in Protobuf, and reusing a field
  number after deleting a field "makes decoding wire-format messages
  ambiguous". Add a field, deprecate the old one, and reserve the number.
- **Rebuilding a clean dict inside an adapter.** Every unrecognized key — the
  vendor extension, the internal trace ID, the field the *next* version will
  need — is deleted, and the deletion is invisible because the output looks
  tidy. This is worst on a multi-hop chain, where the middle hop eats fields
  neither endpoint ever knew about.
- **Guarding a price with `if price > 0`.** Negative outright prices are real:
  the NYMEX WTI front-month contract settled at **-$37.63/b on 2020-04-20**
  after trading as low as -$40.32/b, and CME switched options valuation to the
  Bachelier model days later precisely because negative underlyings and strikes
  had to be supported. A midpoint written as
  `(bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask)` returns **-40.32**
  for that quote instead of -38.975, and returns the *ask* whenever the bid is
  legitimately 0.
- **Scaling a timestamp through float.** `int(ts_sec * 1e9)` performs the
  multiply in binary64 and then truncates toward zero:
  `1784948000.999999` becomes `1784948000999998976`, 24 ns early. Scale the
  decimal and round half-to-even.
- **Believing a seconds-to-nanoseconds upgrade recovers resolution.** It cannot.
  At epoch ~1.78e9 s a binary64 float's neighbours are ~238 ns apart, so the
  low-order nanoseconds were never in the data. An upgraded V1 timestamp is
  precise-looking and wrong at the nanosecond scale — do not measure
  tick-to-trade latency with one.
- **Returning the raw payload when no migration path exists.** The consumer
  then reads fields that are not there, and the header still claims the old
  version. Raise.
- **Logging every migrated tick at INFO.** At tick rates the formatting and I/O
  cost more than the migration, and the log is unreadable. Warn once per
  distinct condition and count the rest.
- **Normalizing the symbol inside a version adapter.** Upper-casing an
  identifier is a silent identity rewrite that breaks joins against reference
  data, and it hides in a diff that appears to be about timestamps.

## Verification

- **Missing header**: a payload with no `schema_version` ⟹
  `MissingVersionHeaderError`, not a defaulted V1 migration. `schema_version:
  True` is rejected too, despite `bool` subclassing `int`.
- **Mislabelling**: `wrap_payload(v1_body, 2)` ⟹ `SchemaConformanceError`;
  re-stamping an already-versioned payload with a different version ⟹
  `SchemaConformanceError`.
- **Conformance**: missing required field, NaN/±Inf price, `"150.50"` as a
  price, `True` as a price, negative volume ⟹ `SchemaConformanceError`;
  `int` where `float` is declared is accepted.
- **Timestamp exactness**: `timestamp_sec = 1784948000.999999` ⟹
  `timestamp_ns == 1784948000999999000`, **not** the truncated
  `1784948000999998976`. `1.5e-9 s` and `2.5e-9 s` both ⟹ `2 ns`, pinning
  round-half-even rather than truncation.
- **Synthesized quote**: V1 → V2 ⟹ `bid == ask == price`,
  `has_synthesized_values` true, `fields_noted(SYNTHESIZED_VALUE) == {bid,
  ask}`, `is_lossless` false, and `exchange_id is None` — not `"UNKNOWN"`.
- **Negative and zero prices**: bid `-40.32` / ask `-37.63` ⟹ `-38.975`;
  bid `0.0` / ask `10.0` ⟹ `5.0`. The midpoint always lies within the quote,
  including at `1e300`.
- **Crossed vs locked**: bid `101` / ask `99` ⟹ `has_suspect_values` true and
  the midpoint still returned; bid == ask ⟹ not suspect.
- **Field preservation**: `venue_seq` and `trade_id` survive V1 → V2;
  V3 → V1 → V3 restores `exchange_id`, `bid_size` and `ask_size` from
  `_carried_fields` and raises no `DEFAULT_APPLIED` note on the way back.
- **Chaining**: V1 → V3 ⟹ `path == (1, 2, 3)` with both hops' notes;
  V3 → V1 ⟹ `path == (3, 2, 1)`; V2 → V3 ⟹ `is_lossless` true; a same-version
  call returns a *copy*, `path == (2,)`, no notes.
- **Routing errors**: unregistered source or target ⟹
  `UnknownSchemaVersionError`; a registered but unreachable version ⟹
  `NoMigrationPathError`. Neither returns the payload.
- **Observability**: five identical lossy migrations ⟹ one WARNING per distinct
  (hop, kind, field) and `stats()["note:synthesized_value"] == 10`; a lossless
  V2 → V3 emits no warning.
- Run `python -m unittest discover -s skills/tick-data-schema-versioning/scripts`
  and confirm 56/56 pass.

## Related Skills

- `data-pipeline-schema-contract-testing`
- `multi-exchange-feed-normalization`
- `broker-api-versioning-migration-playbook`
- `kafka-based-tick-distribution-at-scale`
- `grpc-streaming-for-internal-service-communication`
- `zero-downtime-database-schema-migrations`
- `historical-tick-data-storage-and-compaction`

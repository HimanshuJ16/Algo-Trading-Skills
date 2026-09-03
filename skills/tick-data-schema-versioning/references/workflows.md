# Deep Workflow Reference — tick-data-schema-versioning

This file holds the full technical procedure referenced by `SKILL.md`.
Terminology and compatibility rules are cited in `references/standards.md`.

## Phase 0 — Classify the change before writing any code

Decide which compatibility direction the change has, because it fixes the
deployment order (see the table in `references/standards.md` §1).

| Change | Direction | Deploy order |
|---|---|---|
| Adding an optional field with a default | `FULL` | Either |
| Removing a field that has a default | `FORWARD` | Producers first |
| Adding a field consumers must have | `BACKWARD` | Consumers first |
| Replacing one field with two (`price` → `bid`/`ask`) | Neither | Adapter required on both sides for the whole window |
| Changing a field's declared type in place | Neither | Do not. Add a new field, deprecate the old one |

The last row is the one that causes production incidents. `int32` → `string` is
not a wire-compatible change in Protobuf, and reusing a retired field number
makes decoding ambiguous. Add, deprecate, reserve.

## Phase 1 — Declare the new version

1. Add a `TickSchema` with an incremented `version` and the full field list.
2. Every field the new version introduces gets `required=False` and
   `default=None`. Not `0.0`, not `""`, not `"UNKNOWN"` — a numeric or string
   sentinel is indistinguishable from a real value, and a sentinel venue code
   joins against a venue reference table as though it were a venue. This
   mirrors SBE, where a decoder acting at a lower version returns the null
   representation for a later field.
3. Register the schema *before* registering any adapter that references it;
   `register_adapter` refuses an unregistered version.

## Phase 2 — Write the adapter pair

Register **both** directions. An upgrade-only registry cannot serve a legacy
consumer during a producers-first rollout, and a downgrade-only registry cannot
serve an early-upgraded consumer.

An adapter receives the payload body and a mutable note list, and returns the
next version's body. It must:

1. **Read only fields the source version declares.** The engine has already
   validated the body against the source schema, so a required field is
   present; `body["price"]` rather than `body.get("price", 0.0)`. A `.get` with
   a numeric default is how a fabricated tick gets created.
2. **Append a note for anything it invents.** `SYNTHESIZED_VALUE` for a value
   derived from insufficient information, `PRECISION_REDUCED` for a narrowing
   conversion, `DEFAULT_APPLIED` for a declared default filling a newly added
   field, `SUSPECT_VALUE` for a well-formed but implausible input.
3. **Park, rather than drop, known fields the target lacks.** Move them into
   the `_carried_fields` envelope with a `FIELD_CARRIED` note so a later
   upgrade can restore them.
4. **Pass unknown keys straight through.** `_passthrough` copies every key the
   adapter did not itself consume. On a V1 → V2 → V3 chain a vendor extension
   such as `venue_seq` must survive both hops untouched.
5. **Not touch `symbol`.** Symbol namespace normalization is
   `multi-exchange-feed-normalization`. An identity rewrite inside a version
   hop breaks reference-data joins and hides in a diff that looks like it is
   about timestamps.

The engine stamps `schema_version` after each hop; the adapter must not.

## Phase 3 — Conversions that need care

### Seconds ↔ nanoseconds

```
int(ts_sec * 1e9)        # WRONG twice: binary64 multiply, then truncation
```

`1784948000.999999 * 1e9` truncates to `1784948000999998976`, 24 ns early.
Scale the shortest round-trip decimal representation and round half-to-even:

```
int(Decimal(repr(ts_sec)).scaleb(9).quantize(Decimal(1), ROUND_HALF_EVEN))
```

This gives the nearest nanosecond to the value the producer actually held. It
does **not** recover precision the float never had: at epoch ~1.78e9 s the
adjacent binary64 values are ~238 ns apart, so a V1-sourced `timestamp_ns` is
precise-looking and wrong at the nanosecond scale. Never measure tick-to-trade
latency with one. The upgrade raises `PRECISION_REDUCED` whenever the source
float's quantum exceeds 1 ns, which for any modern epoch is always.

Going the other way, `Decimal(ts_ns).scaleb(-9)` converted to `float` avoids
compounding a division rounding on top of the representation rounding, and the
adapter checks whether the value round-trips before deciding to raise a note.

### Two-sided quote → single price

```
mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid or ask)   # WRONG
```

Two failures in one line:

- **Negative prices are real.** NYMEX WTI front-month settled at -$37.63/b on
  2020-04-20 with an intraday low of -$40.32/b. For that quote the guard fails
  and `bid or ask` yields the *bid*, -40.32, instead of the midpoint -38.975.
- **`bid or ask` treats a legitimate `0.0` bid as absent** and returns the ask
  outright — 10.0 instead of 5.0 for a 0.0/10.0 quote.

Presence is a schema question (is the field declared and non-`None`?), not a
sign question. Compute `bid + (ask - bid) / 2`, which is guaranteed to land in
`[bid, ask]` and cannot overflow on large inputs, and raise `PRECISION_REDUCED`
because the two sides are not recoverable from the midpoint.

A crossed quote (`ask < bid`) is migrated, not rejected — it is a real if
pathological market state — but it raises `SUSPECT_VALUE`. A locked quote
(`bid == ask`) is normal and raises nothing.

## Phase 4 — Run the rollout

1. **Producers stamp and validate.** `wrap_payload(body, version)` checks the
   body against the declared schema before attaching the header, so a
   mislabelled payload fails at the producer rather than inside a consumer.
2. **Each consumer pins its own `target_version`.** The version its code was
   written against, not the newest one registered.
3. **Consumers call `normalize_to_target_version` and check the result before
   the payload.** Gate anything that reads a spread, a size or a venue on
   `has_synthesized_values`; gate quote-quality logic on `has_suspect_values`.
4. **Route every raised error to a dead-letter path, never to a retry.**
   `MissingVersionHeaderError`, `UnknownSchemaVersionError`,
   `NoMigrationPathError` and `SchemaConformanceError` are all deterministic
   properties of the payload. Re-delivering the same bytes produces the same
   error forever and blocks the partition behind it.
5. **Watch `stats()`.** `note:synthesized_value` is the rollout's progress bar:
   it should fall to zero as the last producer upgrades. A hop counter that
   never reaches zero names a service nobody redeployed.

## Phase 5 — Retire the old version

Only after `hop:<old>-><new>` has read zero for a full trading cycle *and* the
producer inventory confirms nobody still emits it. Then remove the schema and
its adapters. Keeping a dead adapter registered is harmless; keeping a live one
undocumented is not.

## Production Implementation Reference

- Reference code: `scripts/schema_versioner.py` — `TickSchemaVersioner`,
  `TickSchema`, `FieldSpec`, `MigrationResult`, `MigrationNote`, `NoteKind`,
  and the `VersionedTickV1` / `VersionedTickV2` / `VersionedTickV3` reference
  dataclasses.
- Automated unit tests: `scripts/test_schema_versioner.py` (56 tests).

### Known limitations

- **Not thread-safe for observability.** Migration itself is pure, but the
  `stats()` counters and the warn-once set are unsynchronized: counters may
  under-count and a warning may be emitted more than once under concurrency.
- **Not a hot-path codec.** Per-payload dict copies, `Decimal` conversion and
  result objects are a correctness reference. Generate fixed-layout codecs for
  a colocated feed handler and keep this as the semantics specification.
- **Adapter quality is the caller's.** The engine validates against the source
  and target schemas and composes hops; it cannot tell whether a user-supplied
  adapter's field mapping is economically correct.

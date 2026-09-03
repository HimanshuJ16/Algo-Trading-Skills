# Standards Reference — tick-data-schema-versioning

Every rule below is attributed to a named source. Nothing here is a regulatory
requirement: schema versioning is an engineering discipline, and the standards
that govern it are format specifications and registry contracts, not rulebooks.
Re-verify against the document version your platform is actually running.

## 1. Compatibility types and rollout order

Source: Confluent Schema Registry, *Schema Evolution and Compatibility Types*
(<https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html>)
and Confluent Developer, *Schema Registry 101 — Testing Schema Compatibility*
(<https://developer.confluent.io/courses/schema-registry/schema-compatibility/>).

| Type | Permitted changes | Upgrade first | Meaning |
|---|---|---|---|
| `BACKWARD` (default) | delete fields; add fields **with defaults** | **Consumers** | A consumer on the new schema reads data written with the previous schema |
| `BACKWARD_TRANSITIVE` | as above | Consumers | …written with **all** previously registered schemas |
| `FORWARD` | add fields; delete fields **that have defaults** | **Producers** | A consumer on the previous schema reads data written with the new schema |
| `FORWARD_TRANSITIVE` | as above | Producers | …for all previously registered schemas |
| `FULL` | add and delete fields, **all with defaults** | either order | Both directions hold |
| `FULL_TRANSITIVE` | as above, against all versions | either order | Both directions hold for every version |
| `NONE` | anything | — | No compatibility checking at registration |

Two consequences that decide deployments:

- Under `BACKWARD`, there is no assurance that consumers on older schemas can
  read data produced with the new schema, so **all consumers must be upgraded
  before producers start emitting the new version**. Under `FORWARD` the
  sequence is reversed.
- Every non-transitive mode validates against the **immediate predecessor
  only**. A consumer several versions behind is covered by nothing, which is
  the case this skill's chained adapters exist to handle.

## 2. Field resolution when reader and writer disagree

Source: Apache Avro Specification 1.11.1, *Schema Resolution*
(<https://avro.apache.org/docs/1.11.1/specification/>).

- Record fields "are matched by **name**", not by position.
- "If the writer's record contains a field with a name not present in the
  reader's record, the writer's value for that field is **ignored**."
- "If the reader's record schema has a field with **no default value**, and
  writer's schema does not have a field with the same name, **an error is
  signalled**." A field added without a default is therefore not a backward-
  compatible addition.
- "A schema must always be used in order to read Avro data correctly" — the
  reader needs the writer's schema, which is why the version must travel with
  the payload.

**Applied here:** `FieldSpec.default` is the reader-side default, `None` for
every added field, and applying it raises a `DEFAULT_APPLIED` note rather than
passing silently.

## 3. Field identity and type changes

Source: Protocol Buffers, *Language Guide (proto3) — Updating A Message Type*
(<https://protobuf.dev/programming-guides/proto3/>).

- "Changing field numbers for any existing field is not safe" — it is
  equivalent to deleting and recreating the field.
- "Reusing a field number makes decoding wire-format messages ambiguous",
  risking data corruption. The `reserved` keyword exists to stop a later author
  reusing a retired number or name.
- Wire-compatible type changes are narrow: `int32`, `uint32`, `int64`, `uint64`
  and `bool` are mutually compatible, and `string`↔`bytes` works for valid
  UTF-8. **`int32` → `string` is not among them.**

**Applied here:** changing a field's declared type is a new schema version with
a new field, never an in-place edit — see the "breaking field type mutations"
pitfall in `SKILL.md`. `_validate` rejects a value whose type contradicts the
version the payload declares.

## 4. Carrying the version in the message header

| Format | Where the version lives |
|---|---|
| Confluent Schema Registry wire format | A magic byte, then a 4-byte **big-endian** schema ID, then the encoded payload. Confluent Platform 8.1.1+ can carry a 16-byte schema GUID instead. (<https://docs.confluent.io/platform/current/schema-registry/fundamentals/serdes-develop/>) |
| FIX Simple Binary Encoding | The message header composite carries `blockLength`, `templateId`, `schemaId` and `version`. (<https://github.com/FIXTradingCommunity/fix-simple-binary-encoding>) |

SBE's extension rules are the closest published analogue to this skill's
adapters (source: the SBE reference implementation's *Message Versioning* guide,
<https://github.com/aeron-io/simple-binary-encoding/wiki/Message-Versioning>):

- "A new field can be added by creating a new `messageSchema` and increasing the
  `version` number of the schema, versions start at 0."
- "The new field can then be added at the **end** of the root block" — never in
  the middle, and never replacing an existing field.
- "The new field should have its `sinceVersion` attribute set to be the version
  number that has been used for the new schema and its `presence=optional`
  attribute set."
- A decoder reading an older message acts at that message's `actingVersion` "to
  ensure it does not read beyond the end of an existing block", returning the
  **null representation** for fields added in later versions.

**Applied here:** `None`, not `0.0` or `"UNKNOWN"`, is the default for every
field a source version could not supply.

## 5. Numeric facts the adapters depend on

| Claim | Basis |
|---|---|
| A binary64 float cannot hold nanosecond resolution at a present-day epoch. At 1.784948e9 s, `math.ulp` is 2.384185791015625e-7 s ≈ **238 ns**. | IEEE 754 binary64 (52-bit significand); reproduced with `math.ulp` on CPython 3.11 |
| `int(ts_sec * 1e9)` both scales in binary64 and truncates toward zero: `1784948000.999999` → `1784948000999998976`, 24 ns early. | Reproduced on CPython 3.11 |
| A positivity guard on price is invalid. NYMEX WTI front-month futures settled at **-$37.63/b on 2020-04-20**, intraday low **-$40.32/b** — the first negative front-month print since trading began in 1983. | U.S. EIA, *Low liquidity and limited available storage pushed WTI crude oil futures prices below zero* (<https://www.eia.gov/todayinenergy/detail.php?id=43495>) |
| Negative underlyings and strikes are a supported, non-exceptional condition at CME. | CME Clearing Advisory 20-171, 2020-04-21, switching options valuation to the Bachelier model effective 2020-04-22 (<https://www.cmegroup.com/notices/clearing/2020/04/Chadv20-171.html>) |

## Built-in example schemas

These are the three versions shipped in `scripts/schema_versioner.py` as a
worked example, not a standard. Replace them with your own.

| Field | V1 | V2 | V3 |
|---|---|---|---|
| `symbol` | `str` req | `str` req | `str` req |
| `timestamp_sec` | `float` req | — | — |
| `timestamp_ns` | — | `int` req | `int` req |
| `price` | `float` req | — | — |
| `bid` / `ask` | — | `float` req | `float` req |
| `volume` | `float` req, ≥ 0 | `float` req, ≥ 0 | `float` req, ≥ 0 |
| `exchange_id` | — | `str` opt, default `None` | `str` opt, default `None` |
| `bid_size` / `ask_size` | — | — | `float` opt, default `None`, ≥ 0 |

V1 → V2 is not FULL-compatible in the Confluent sense: `price` is removed and
`bid`/`ask` are added without defaults, so it is a breaking change that only a
migration adapter can bridge. V2 → V3 adds two optional fields with defaults and
is `FULL`-compatible, which is why the engine reports it as lossless.

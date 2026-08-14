# Broker Integration Standards — broker-api-changelog-diffing-tool

Quantitative trading systems are highly sensitive to schema mutations. The table below is
this skill's classification policy, not an external standard: severities are a house
choice about what should stop a build, and you should tune them to your release process.
What is *not* a choice is the direction of the bias — this is a gate, so a false positive
costs minutes and a false negative ships a broken integration.

## Severity matrix

| Change Category | Change Type | Severity | Action |
|---|---|---|---|
| Removed path / method | `REMOVED_ENDPOINT` | `CRITICAL_BREAKING` | Fail CI / require adapter refactor |
| Removed request body | `REMOVED_REQUEST_BODY` | `CRITICAL_BREAKING` | Fail CI / callers send a payload no longer accepted |
| Removed parameter / request field | `REMOVED_FIELD` | `HIGH_BREAKING` | Fail CI / update payload builders |
| Removed response field | `REMOVED_RESPONSE_FIELD` | `HIGH_BREAKING` | Fail CI / fix state machine parsers |
| Removed response status code | `REMOVED_RESPONSE_CODE` | `HIGH_BREAKING` | Fail CI / consumers branching on it will never see it |
| Removed request/response content type | `REMOVED_CONTENT_TYPE` | `HIGH_BREAKING` | Fail CI / renegotiate media type |
| Mutated request/param type | `TYPE_MUTATION` | `HIGH_BREAKING` | Fail CI / update type casting |
| Mutated response type | `RESPONSE_TYPE_MUTATION` | `HIGH_BREAKING` | Fail CI / update parser typings |
| Breaking enum change (see below) | `ENUM_MUTATION` | `HIGH_BREAKING` | Fail CI / update enum definitions |
| Response field no longer guaranteed | `REQUIREMENT_MUTATION` | `HIGH_BREAKING` | Fail CI / make the parser tolerate absence |
| New required parameter/field, or optional → required | `NEW_REQUIRED_PARAMETER` | `MEDIUM_BREAKING` | Fail CI / add mandatory argument |
| Added optional parameter/field | `ADDED_OPTIONAL_FIELD` | `NON_BREAKING_INFO` | Pass CI / log |
| Added endpoint | `ADDED_ENDPOINT` | `NON_BREAKING_INFO` | Pass CI / log |
| Reference not resolvable locally | `UNRESOLVED_REF` | `NON_BREAKING_INFO` | **Investigate** — that region was not compared |

`is_compatible` is False when any change is `MEDIUM_BREAKING` or higher.
`UNRESOLVED_REF` carries informational severity because it is not itself a breaking change,
but it means part of the schema went uncompared. Do not treat a report containing one as
complete.

## Enum classification depends on direction

The same edit is breaking in one direction and harmless in the other, because a request
enum and a response enum constrain different parties.

| | Value removed | Value added | Constraint newly imposed | Constraint dropped |
|---|---|---|---|---|
| **Request** enum (what the client may send) | **Breaking** — callers sending it are now rejected | Info — wider acceptance | **Breaking** — previously accepted values now rejected | Info — wider acceptance |
| **Response** enum (what the client must handle) | Info — a state that no longer occurs | **Breaking** — exhaustive consumers do not recognise it | Info | **Breaking** — the field is no longer a closed set |

The response-addition case is the one most often missed. A broker adding a new order status
does not remove anything, yet it breaks every consumer that switches exhaustively over the
old set — which is precisely the "quantitative state machine" this skill exists to protect.

## Requirement transitions

| | Moved into `required` | Moved out of `required` |
|---|---|---|
| **Request** | **Breaking** — existing callers omit it | Info |
| **Response** | Info | **Breaking** — parsers assuming presence fail when it is absent |

## Type normalization

OpenAPI 3.0 expresses nullability as a scalar `type` plus `nullable: true`; OpenAPI 3.1
drops `nullable` and uses JSON Schema type arrays (`type: ["string", "null"]`). The differ
normalizes both spellings to a set before comparing, so:

- `type: "string"` and `type: ["string"]` are not reported as a mutation;
- `type: "string"` with `nullable: true` matches `type: ["string", "null"]`;
- a schema declared `type: ["object", "null"]` is still recursed into for property diffing,
  which a literal equality test against `"object"` would silently skip.

Type comparison is otherwise deliberately conservative: any difference in the normalized
set is reported, including widenings a permissive client would tolerate.

Reference: [Upgrading from OpenAPI 3.0 to 3.1](https://learn.openapis.org/upgrading/v3.0-to-v3.1.html).

## Reference resolution

Local references are followed against the document each side came from:

- OpenAPI 3.x — `#/components/schemas/...`
- Swagger 2.0 — `#/definitions/...`

JSON Pointer escapes (`~0`, `~1`) are decoded. Cycles are detected, so self-referential
models (`Order.parent → Order`) terminate. External and remote references are **not**
fetched and are reported as `UNRESOLVED_REF`.

Reference: [OpenAPI Specification](https://spec.openapis.org/oas/v3.0.3.html);
Swagger 2.0 → OpenAPI 3.0 structural differences (`definitions` → `components.schemas`,
body parameter → `requestBody`, `consumes`/`produces` → `content`) are summarised at
[learn.openapis.org](https://learn.openapis.org/).

## Scope limits

- **Structural diffing only.** Rate limits, auth scopes, error semantics inside a 200 body,
  rounding and matching-engine behaviour are not expressible here and will not appear.
- **Swagger 2.0 is only partially handled.** `#/definitions/` references resolve, but body
  parameters and top-level `consumes`/`produces` are not modeled. Convert to 3.x first.
- **Composition keywords** (`oneOf`, `anyOf`, `allOf`, `discriminator`) are not evaluated.
- **No file loading.** The differ takes parsed dictionaries; JSON/YAML parsing is the
  caller's responsibility.

## Category

`broker-integration` — see top-level `mappings/` directory.

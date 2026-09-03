---
name: broker-api-changelog-diffing-tool
description: >-
  Use before upgrading a broker SDK or OpenAPI spec, to diff two schema snapshots for
  removed endpoints, newly required parameters, enum mutations and type changes so CI
  fails before the change reaches an order path.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, api-changelog, schema-diffing, openapi, breaking-changes, ci-cd
  brokers_frameworks: "OpenAPI 3.x; Swagger 2.0 (reference resolution only)"
  version: "3.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this before upgrading a broker SDK version or pulling a new API specification
(Binance, Coinbase, IBKR Client Portal and similar publish OpenAPI documents). Broker
releases introduce silent breaking changes — a removed nested response field, a parameter
that quietly became mandatory, a new order status the state machine has never seen. This
skill diffs two schema snapshots and classifies what changed, so a CI job can fail the
build before the change reaches an order path.

The tool is a **gate**, and its failure modes are asymmetric: a false positive costs a
developer a few minutes, a false negative ships a broken integration. Everything about its
classification is biased accordingly.

## When NOT to Use

- **As proof a release is safe.** It compares structure only. Rate limits, auth scope
  changes, altered matching-engine behaviour, changed rounding, new error codes returned
  in a 200 body — none are expressible in a schema, and none will appear in the report. A
  clean diff means "nothing structural broke", not "safe to deploy".
- **On specifications it cannot fully resolve.** Only local (`#/...`) references are
  followed. External and remote `$ref`s are reported as `UNRESOLVED_REF` — that region was
  *not compared*, and treating the report as complete when one is present is a mistake.
- **As a Swagger 2.0 differ.** `#/definitions/...` references resolve, but Swagger 2.0's
  body parameters and top-level `consumes`/`produces` are not modeled; the request-body
  logic assumes OpenAPI 3.x `requestBody.content`. Convert 2.0 documents to 3.x first.
- **As a file loader.** It takes parsed Python dictionaries. Reading and parsing JSON or
  YAML is the caller's job.
- **For composition keywords.** `oneOf`, `anyOf`, `allOf` and `discriminator` are not
  evaluated; schemas using them will diff only at the level the tool can see.

## Prerequisites

- Baseline (older) and target (newer) API schemas, parsed into dictionaries.
- Both documents complete, including the `components`/`definitions` sections the
  `$ref`s point at — a spec split across files must be bundled first, or references will
  come back unresolved.

## Workflow

1. **Load both documents and let the differ reject unusable input.** `diff_schemas`
   raises `SchemaDiffError` when a document is not a mapping or has no `paths`. This is
   deliberate: a failed download or a wrong path yields an empty document, and a differ
   that shrugs and reports zero changes turns the gate green at exactly the moment it
   matters.

2. **Diff endpoints.** Removed paths and removed methods are `CRITICAL_BREAKING`. Only
   real HTTP methods are treated as operations — a Path Item Object also legally carries
   `parameters`, `servers`, `summary`, `description` and `$ref`, and path-level
   `parameters` are diffed as shared across every operation.

3. **Resolve `$ref` before comparing anything.** Real broker specs describe payloads
   almost entirely through references, and a `$ref` schema carries no `type`, `properties`
   or `enum` of its own. Resolution follows `#/components/schemas/...` and
   `#/definitions/...` against the document each side came from, with cycle protection for
   self-referential models.

4. **Treat absence as a change.** A removed response status code, a removed request or
   response content type, and a removed `requestBody` are all breaking and all invisible
   to a differ that walks only the keys present on both sides.

5. **Check requirement transitions in both directions.** A request field or parameter
   moving *into* `required` breaks callers that omit it. A response field moving *out of*
   `required` breaks parsers that assume it is present. Both matter; they are not the same
   check.

6. **Classify enums by direction.** A request enum constrains what the client may send, so
   *removing* a value is breaking. A response enum constrains what the client must handle,
   so *adding* a value is breaking — a new order status silently breaks an exhaustive state
   machine. Newly imposing a request constraint, and dropping a response constraint, are
   breaking too.

7. **Gate the build.** `report.exit_code` is 0 when compatible and 1 otherwise;
   `report.format_report()` renders the findings severity-first. `is_compatible` is False
   if any change is `MEDIUM_BREAKING` or higher.

> Full procedure: see `references/workflows.md`.
> Severity matrix and classification rationale: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing `$ref` schemas without resolving them.** Both sides look like empty objects,
  every check is skipped, and a release that deleted an entire response model reports
  clean. This is the single most likely way to get a false green.
- **Ignoring an `UNRESOLVED_REF` finding.** It is informational in severity but it means a
  region of the schema was never compared. Bundle the spec and re-run.
- **Only diffing keys present on both sides.** Removals are the breaking changes; a
  loop written as `if key in new: compare(...)` cannot see any of them.
- **Treating enum changes as direction-agnostic.** Flagging every set difference raises
  false alarms on request widenings while missing the response additions that actually
  break consumers.
- **Assuming a scalar `type`.** OpenAPI 3.1 allows `type: ["object", "null"]` where 3.0
  used `nullable: true`. An equality test against the literal `"object"` silently skips
  property diffing, and comparing the two spellings reports a mutation that never happened.
- **Treating every key under a path as an HTTP method.** `parameters` is a list and
  `summary` is a string; calling `.get()` on them raises on a perfectly valid document.
- **Letting an empty or malformed document produce a clean report.**
- **Unbounded recursion on self-referential models.** `Order.parent → Order` is ordinary,
  and resolving references without a cycle guard hangs the build.
- **Reading a clean report as deployment approval.** Structure is not behaviour.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-api-changelog-diffing-tool/scripts`
- Build a fixture whose response model sits behind a `$ref`, delete a field from the
  referenced component, and confirm `REMOVED_RESPONSE_FIELD` is reported. A differ that
  passes every inline-schema test can still fail this one, which is the case that matters.
- Confirm two empty documents raise `SchemaDiffError` rather than reporting compatible.
- Confirm a self-referential model terminates.
- Confirm direction-aware enum behaviour: adding a response enum value is breaking; adding
  a request enum value is not.
- Confirm a Path Item Object carrying `parameters` and `summary` does not raise.
- Mutate fixtures by deep-copying a baseline and changing exactly one thing, so a test for
  one change cannot accidentally introduce another.

## Related Skills

- `broker-api-versioning-migration-playbook`
- `broker-api-deprecation-notice-monitoring`
- `sandbox-vs-production-endpoint-drift`
- `broker-agnostic-adapter-interface`

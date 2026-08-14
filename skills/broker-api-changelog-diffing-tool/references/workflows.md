# Deep Workflow Reference — broker-api-changelog-diffing-tool

This file holds the full technical procedure referenced by `SKILL.md` under institutional quant standards.

## Full Procedure

1. **Ingest API Schemas**:
   - Ingest baseline schema $V_{\text{old}}$ and target schema $V_{\text{new}}$ as parsed
     dictionaries. JSON/YAML parsing is the caller's responsibility.
   - **Reject unusable documents rather than diffing them.** `diff_schemas` raises
     `SchemaDiffError` when a document is not a mapping or has no `paths`. A failed
     download or a wrong file path produces an empty document, and a differ that reports
     zero changes for it turns the gate green precisely when it should not.
   - Bundle multi-file specifications first. Only local (`#/...`) references resolve.

2. **Diff Endpoints & Paths**:
   - Compare `paths` for removed endpoints (`REMOVED_ENDPOINT`), removed methods, or added
     routes (`ADDED_ENDPOINT`).
   - **Only real HTTP methods are operations.** A Path Item Object legally carries
     `parameters`, `servers`, `summary`, `description` and `$ref` alongside them; treating
     those as operations raises on a valid document.
   - Diff path-level `parameters`, which apply to every operation on the path.

3. **Resolve References Before Comparing**:
   - Follow `#/components/schemas/...` (OpenAPI 3.x) and `#/definitions/...` (Swagger 2.0)
     against the document each side came from, decoding `~0`/`~1` JSON Pointer escapes.
   - Guard cycles: self-referential models (`Order.parent → Order`) are ordinary and will
     otherwise recurse forever.
   - Report anything unresolvable as `UNRESOLVED_REF` rather than skipping it silently —
     an unresolved reference means that region was never compared.

4. **Diff Query/Path Parameters & Request Bodies**:
   - Recursively inspect parameters and `requestBody.content` schemas.
   - Detect removed fields (`REMOVED_FIELD`), type mutations (`TYPE_MUTATION`), enum
     changes (`ENUM_MUTATION`), new mandatory parameters and **optional → required
     transitions** (`NEW_REQUIRED_PARAMETER`).
   - Detect removed content types (`REMOVED_CONTENT_TYPE`) and a wholly removed body
     (`REMOVED_REQUEST_BODY`).

5. **Diff Response Models**:
   - Recursively inspect `responses` by status code and content type.
   - Detect removed response fields (`REMOVED_RESPONSE_FIELD`), response type mutations
     (`RESPONSE_TYPE_MUTATION`), removed status codes (`REMOVED_RESPONSE_CODE`), removed
     content types, and fields dropped from `required` (`REQUIREMENT_MUTATION`) which are
     no longer guaranteed present.

6. **Classify Enums By Direction**:
   - Request enums constrain what the client sends: **removing** a value is breaking.
   - Response enums constrain what the client handles: **adding** a value is breaking,
     because an exhaustive state machine will not recognise it.
   - See the matrix in `references/standards.md` for all four transitions.

7. **Classify Severity & Report**:
   - Classify into `CRITICAL_BREAKING`, `HIGH_BREAKING`, `MEDIUM_BREAKING`, or
     `NON_BREAKING_INFO`.
   - `is_compatible = False` if any breaking change of MEDIUM or higher exists.
   - Gate the build on `report.exit_code`; render findings with `report.format_report()`.

## Failure Modes Observed in Production

- **`$ref` Blindness:** Comparing reference schemas without resolving them. Both sides
  present as empty objects, every check is skipped, and a release that deleted an entire
  response model reports clean. The most likely source of a false green.
- **Removal Blindness:** Iterating only keys present in both documents, so removed status
  codes, content types and request bodies — the breaking changes — are structurally
  invisible.
- **Direction-Blind Enums:** Flagging every set difference. Raises false alarms on request
  widenings while missing the response additions that break consumers.
- **Scalar Type Assumption:** Testing `type == "object"` against an OpenAPI 3.1
  `["object", "null"]`, silently skipping the whole subtree.
- **Empty-Document Green:** A failed fetch yields `{}`, the differ finds no paths, and CI
  passes.
- **Unbounded Recursion:** Resolving references without a cycle guard on a self-referential
  model hangs the build.
- **Imprecise Test Fixtures:** Hand-writing each `new_schema` instead of deep-copying and
  mutating one thing, so a fixture accidentally omits a section and the test passes for the
  wrong reason.

## Production Implementation Reference

- Reference code: `scripts/changelog_differ.py` (`BrokerAPIChangelogDiffer`, `SchemaChange`, `APIChangelogReport`).
- Automated unit tests: `scripts/test_changelog_differ.py`.

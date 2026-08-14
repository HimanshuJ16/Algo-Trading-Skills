# Pre-Flight / Sign-off Checklist — broker-api-changelog-diffing-tool

Use this before considering the skill's implementation complete for production
quantitative environments. Tick a box only after running the check against your own
specifications — a pre-ticked checklist asserts verification that did not happen.

## Ingestion
- [ ] **Input Validation:** Confirm two empty or malformed documents raise `SchemaDiffError`
      rather than reporting compatible. A failed download must not turn the gate green.
- [ ] **Bundling:** Confirm multi-file specifications are bundled before diffing; only
      local (`#/...`) references resolve.
- [ ] **Unresolved References:** Confirm the report is checked for `UNRESOLVED_REF`. It is
      informational in severity but means that region was **not compared**.
- [ ] **Specification Version:** Confirm inputs are OpenAPI 3.x. Swagger 2.0 body
      parameters and `consumes`/`produces` are not modeled — convert first.

## Structural Detection
- [ ] **Reference Resolution:** Confirm a field deleted from a component behind a `$ref` is
      reported. A differ passing every inline-schema test can still fail this one.
- [ ] **Cycle Safety:** Confirm a self-referential model (`Order.parent → Order`) terminates.
- [ ] **Endpoint Removal:** Confirm deleted paths and methods trigger `CRITICAL_BREAKING`.
- [ ] **Path Item Fields:** Confirm a path carrying `parameters`, `summary` or `servers`
      alongside its methods does not raise, and that path-level parameters are diffed.
- [ ] **Removals Are Detected:** Confirm removed response status codes, removed request and
      response content types, and a removed `requestBody` are each reported.
- [ ] **Recursive Body Diffing:** Confirm nested objects and array `items` are diffed.

## Classification Correctness
- [ ] **Requirement Transitions:** Confirm a request field moving *into* `required` and a
      response field moving *out of* `required` are both reported.
- [ ] **Enum Direction:** Confirm adding a **response** enum value is breaking and adding a
      **request** enum value is not; confirm the reverse for removals.
- [ ] **Type Normalization:** Confirm `type: "string"` vs `type: ["string"]` is not a
      mutation, that 3.0 `nullable: true` matches 3.1 `["string","null"]`, and that
      `["object","null"]` is still recursed into.
- [ ] **Backward Compatibility:** Confirm optional additions are `NON_BREAKING_INFO`.

## Gate Integration
- [ ] **Exit Code:** Confirm CI fails on `report.exit_code == 1`.
- [ ] **Report Surface:** Confirm `format_report()` output is captured in the build log.
- [ ] **Severity Policy:** Confirm the MEDIUM-and-above threshold matches your release
      process; these severities are house policy, not an external standard.

## Testing
- [ ] **Fixture Precision:** Confirm test fixtures deep-copy a baseline and mutate exactly
      one thing, so a test cannot pass for the wrong reason.
- [ ] **Automated Testing:** Run
      `python -m unittest discover -s skills/broker-api-changelog-diffing-tool/scripts`
      — 100% pass rate.

## Scope Acknowledgement
- [ ] **Structure Is Not Behaviour:** Confirm reviewers understand a clean report does not
      cover rate limits, auth scopes, error semantics inside a 200 body, rounding, or
      matching-engine changes.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Specifications diffed (old → new versions): ___________________________

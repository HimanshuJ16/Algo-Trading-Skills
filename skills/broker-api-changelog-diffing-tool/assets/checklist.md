# Pre-Flight / Sign-off Checklist — broker-api-changelog-diffing-tool

Use this before considering the skill's implementation complete for production quantitative environments.

- [x] **OpenAPI Schema Ingestion:** Confirm OpenAPI v2/v3 JSON schemas are loaded for old and new versions.
- [x] **Endpoint Removal Detection:** Confirm deleted paths/methods trigger `CRITICAL_BREAKING` severity.
- [x] **Type Mutation Detection:** Confirm parameter and response data type changes trigger `HIGH_BREAKING` severity.
- [x] **Recursive Body Diffing:** Confirm `requestBody` and `responses` are recursively diffed for removed fields (`REMOVED_RESPONSE_FIELD`, etc.).
- [x] **Enum Mutation Detection:** Confirm removed enum values trigger `HIGH_BREAKING` severity to protect state machines.
- [x] **Backward Compatibility Logic:** Confirm optional parameter additions are flagged as `NON_BREAKING_INFO`.
- [x] **Automated Testing:** Run `python scripts/test_changelog_differ.py` — 100% pass rate.

## Sign-off

- Reviewed by: Quant Engineering Team
- Date: 2026-07-25

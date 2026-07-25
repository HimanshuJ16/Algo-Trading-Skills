# Pre-Flight / Sign-off Checklist — broker-api-changelog-diffing-tool

Use this before considering the skill's implementation complete.

- [ ] **OpenAPI Schema Ingestion:** Confirm OpenAPI v2/v3 JSON schemas are loaded for old and new versions.
- [ ] **Endpoint Removal Detection:** Confirm deleted paths/methods trigger `CRITICAL_BREAKING` severity.
- [ ] **Type Mutation Detection:** Confirm parameter data type changes trigger `HIGH_BREAKING` severity.
- [ ] **Backward Compatibility Logic:** Confirm optional parameter additions are flagged as `NON_BREAKING_INFO`.
- [ ] **Automated Testing:** Run `python scripts/test_changelog_differ.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

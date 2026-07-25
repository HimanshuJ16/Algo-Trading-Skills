# Deep Workflow Reference — broker-api-changelog-diffing-tool

This file holds the full technical procedure referenced by `SKILL.md` under institutional quant standards.

## Full Procedure

1. **Ingest API Schemas**:
   - Ingest baseline OpenAPI/Swagger schema $V_{\text{old}}$ and target schema $V_{\text{new}}$.

2. **Diff Endpoints & Paths**:
   - Compare `paths` objects for removed endpoints (`REMOVED_ENDPOINT`), removed methods, or added routes (`ADDED_ENDPOINT`).

3. **Diff Query/Path Parameters & Request Bodies**:
   - Recursively inspect query/path parameters and `requestBody.content` schemas.
   - Detect removed fields (`REMOVED_FIELD`), data type mutations (`TYPE_MUTATION`), deleted enums (`ENUM_MUTATION`), or new mandatory parameters (`NEW_REQUIRED_PARAMETER`).

4. **Diff Response Models**:
   - Recursively inspect `responses` by HTTP status code and `content-type`.
   - Detect removed response fields (`REMOVED_RESPONSE_FIELD`) or response type mutations (`RESPONSE_TYPE_MUTATION`) that could crash execution state machines.

5. **Classify Severity & Report**:
   - Classify breaking changes into `CRITICAL_BREAKING`, `HIGH_BREAKING`, `MEDIUM_BREAKING`, or `NON_BREAKING_INFO`.
   - Return `is_compatible = False` if any breaking changes of MEDIUM or higher exist.
   - Output structured CI/CD report logs.

## Production Implementation Reference

- Reference code: `scripts/changelog_differ.py` (`BrokerAPIChangelogDiffer`, `SchemaChange`, `APIChangelogReport`).
- Automated unit tests: `scripts/test_changelog_differ.py`.

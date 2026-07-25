# Deep Workflow Reference — broker-api-changelog-diffing-tool

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest API Schemas**:
   - Ingest baseline OpenAPI/Swagger schema $V_{\text{old}}$ and target schema $V_{\text{new}}$.

2. **Diff Endpoints & Paths**:
   - Compare `paths` objects for removed endpoints (`REMOVED_ENDPOINT`) or added routes (`ADDED_ENDPOINT`).

3. **Diff Parameters & Types**:
   - Inspect request parameters and response schemas for removed fields (`REMOVED_FIELD`), data type mutations (`TYPE_MUTATION`), or new mandatory parameters (`NEW_REQUIRED_PARAMETER`).

4. **Classify Severity & Report**:
   - Classify breaking changes into `CRITICAL`, `HIGH`, or `MEDIUM`. Return `is_compatible = False` if any breaking changes exist.

## Production Implementation Reference

- Reference code: `scripts/changelog_differ.py` (`BrokerAPIChangelogDiffer`, `SchemaChange`, `APIChangelogReport`).
- Automated unit tests: `scripts/test_changelog_differ.py`.

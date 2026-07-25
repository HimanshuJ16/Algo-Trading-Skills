# Deep Workflow Reference — tick-data-schema-versioning

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Tag Payload Version**:
   - Prepend `schema_version` header to payload data dictionary.

2. **Inspect Payload & Target Version**:
   - Compare `payload['schema_version']` against consumer's expected version $V_{\text{target}}$.

3. **Execute Upgrade / Downgrade Migration**:
   - Apply $V_1 \to V_2$ upgrade adapter (convert sec to ns, mid price to bid/ask).
   - Apply $V_2 \to V_1$ downgrade adapter (convert ns to sec, compute mid price for legacy readers).

4. **Deliver Normalized Payload**:
   - Forward normalized tick payload to strategy logic.

## Production Implementation Reference

- Reference code: `scripts/schema_versioner.py` (`TickSchemaVersioner`, `VersionedTickV1`, `VersionedTickV2`).
- Automated unit tests: `scripts/test_schema_versioner.py`.

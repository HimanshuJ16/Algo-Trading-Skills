# Deep Workflow Reference — broker-api-versioning-migration-playbook

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Dual-Version Protocol Adapters**:
   - Implement `V1Adapter` and `V2Adapter` implementing a unified `IBrokerAdapter` contract.

2. **Shadow Mode Schema Audit**:
   - Issue read requests to both V1 and V2 in parallel.
   - Run `audit_shadow_response()` to detect missing fields or schema drift prior to cutover.

3. **Canary Traffic Split**:
   - Set migration phase to `CANARY_CUTOVER`.
   - Scale V2 canary percentage incrementally ($0\% \to 25\% \to 50\% \to 100\%$).

4. **Emergency Rollback Guard**:
   - If V2 encounters elevated error rates or unexpected field breaks, instantly revert phase to `ROLLBACK_V1`.

## Production Implementation Reference

- Reference code: `scripts/api_migrator.py` (`BrokerAPIVersionMigrator`, `MigrationPhase`).
- Automated unit tests: `scripts/test_api_migrator.py`.

# Pre-Flight / Sign-off Checklist — broker-api-versioning-migration-playbook

Use this before considering the skill's implementation complete.

- [ ] **Dual-Version Adapters:** Confirm V1 and V2 adapters implement a common interface contract.
- [ ] **Payload Translation:** Confirm V1 order parameters map cleanly to V2 API JSON structures.
- [ ] **Shadow Traffic Audit:** Confirm V1 vs V2 schema diff auditor flags missing/drifted fields.
- [ ] **Canary Traffic Split:** Confirm canary traffic percentage routes live orders accurately.
- [ ] **Emergency Rollback:** Confirm `ROLLBACK_V1` instantly reverts 100% traffic to V1.
- [ ] **Automated Testing:** Run `python scripts/test_api_migrator.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

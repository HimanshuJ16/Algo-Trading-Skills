# Pre-Flight / Sign-off Checklist — tick-data-schema-versioning

Use this before considering the skill's implementation complete.

- [ ] **Version Header Injection:** Confirm `schema_version` is attached to all outbound tick payloads.
- [ ] **Upgrade Adapter Verification:** Confirm V1 legacy payloads upgrade cleanly to V2 format.
- [ ] **Downgrade Adapter Verification:** Confirm V2 payloads downgrade gracefully for V1 legacy readers.
- [ ] **Unknown Version Handling:** Confirm unsupported version headers log warnings rather than crashing processes.
- [ ] **Automated Testing:** Run `python scripts/test_schema_versioner.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

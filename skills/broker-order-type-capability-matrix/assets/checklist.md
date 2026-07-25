# Pre-Flight / Sign-off Checklist — broker-order-type-capability-matrix

Use this before considering the skill's implementation complete.

- [ ] **Capability Registry:** Confirm target broker native order types are registered accurately.
- [ ] **Native Capability Pre-Validation:** Confirm native support is queried prior to order dispatch.
- [ ] **Software Emulation Synthesizer:** Confirm complex orders (Bracket, Iceberg) are decomposed into local trigger legs when native support is missing.
- [ ] **Automated Testing:** Run `python scripts/test_capability_matrix.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

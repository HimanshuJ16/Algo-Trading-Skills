# Pre-Flight / Sign-off Checklist — broker-order-type-capability-matrix

Use this before considering the skill's implementation complete.

- [ ] **Capability Registry Expansion:** Target broker native order types (including advanced algo types like TWAP) are registered accurately.
- [ ] **Native Capability Pre-Validation:** Ensure `plan_order_execution` routes native flags efficiently without fallback overhead.
- [ ] **Software Emulation Synthesizer:** Confirm complex orders (Bracket, Iceberg, TWAP) are decomposed into well-typed `EmulatedLeg` objects (with action inversion correctly mapped for stops/takes).
- [ ] **Edge Cases Handled:** `plan_order_execution` catches mutually exclusive arguments, invalid broker names, and unsupported/un-emulatable edge cases.
- [ ] **Automated Testing:** Run `python -m unittest test_capability_matrix.py` — 100% pass rate confirmed.

## Sign-off

- Quant Engineer: ___________________________
- Code Reviewer: ___________________________
- Date: ___________________________

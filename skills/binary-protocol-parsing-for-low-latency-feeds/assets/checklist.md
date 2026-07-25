# Pre-Flight / Sign-off Checklist — binary-protocol-parsing-for-low-latency-feeds

Use this before considering the skill's implementation complete.

- [ ] **Struct Layout Alignment:** Confirm struct format string matches byte offsets per exchange spec.
- [ ] **Endianness Verification:** Confirm big-endian (`>`) or little-endian (`<`) is enforced correctly.
- [ ] **Price Scale Division:** Confirm integer prices are scaled by $10^4$ to match floating-point prices.
- [ ] **Invalid Size Rejection:** Confirm truncated binary frames raise validation errors.
- [ ] **Automated Testing:** Run `python scripts/test_binary_parser.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

# Pre-Flight / Sign-off Checklist — data-vendor-cross-validation-for-backtests

- [ ] **Timestamp Alignment:** Confirm both vendors use identical timezone (UTC).
- [ ] **Price Discrepancy Flagging:** Confirm bars exceeding threshold are flagged.
- [ ] **Missing Bar Audit:** Confirm missing bar ratio is computed and tolerance enforced.
- [ ] **Adjusted vs Raw Awareness:** Confirm both vendors provide same adjustment type.
- [ ] **Automated Testing:** Run `python scripts/test_vendor_cross_validator.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

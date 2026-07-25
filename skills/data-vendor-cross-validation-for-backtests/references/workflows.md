# Deep Workflow Reference — data-vendor-cross-validation-for-backtests

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Align Bars by Timestamp**: Join Vendor A and Vendor B datasets on timestamp keys.
2. **Compute Per-Bar Price Delta**: $\Delta = |C_A - C_B| / C_A \times 10^4$ (bps).
3. **Audit Missing Bar Coverage**: Count bars unique to each vendor and compute missing ratio.
4. **Generate Pass/Fail Verdict**: Fail if any bar exceeds discrepancy threshold or missing ratio exceeds tolerance.

## Production Implementation Reference

- Reference code: `scripts/vendor_cross_validator.py` (`DataVendorCrossValidator`, `CrossValidationReport`).
- Automated unit tests: `scripts/test_vendor_cross_validator.py`.

# Deep Workflow Reference — data-vendor-cross-validation-for-backtests

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Guard the Inputs**: Reject the run outright if both vendor datasets are empty — a
   validator with nothing to compare reports zero discrepancies and zero missing bars,
   which reads as a green gate. `DataVendorCrossValidator.validate()` raises `ValueError`.
2. **Index Bars per Vendor and Record Duplicates**: Build a timestamp → bar index. Record
   any repeated timestamp as an integrity issue instead of letting last-write-wins erase it.
3. **Align Bars by Timestamp**: Join Vendor A and Vendor B datasets on exact timestamp
   strings. Zero overlap between two non-empty datasets indicates a timestamp
   format/timezone mismatch, not disjoint coverage; the report says so explicitly rather
   than only reporting a 100% missing ratio.
4. **Screen Bar Integrity**: For each matched pair, reject non-finite closes or volumes,
   negative volume, and a zero Vendor A close. These bars are excluded from the delta
   statistics and fail the verdict.
5. **Compute Per-Bar Price Delta**: $\Delta = |C_A - C_B| / |C_A| \times 10^4$ (bps),
   Vendor A as reference. Flag bars strictly above the threshold.
6. **Audit Volume Ratios**: $\max(V_A, V_B) / \min(V_A, V_B)$ per matched bar; flag above
   the spike threshold. Audit-only — does not fail the verdict.
7. **Audit Missing Bar Coverage**: Count bars unique to each vendor and compute the
   missing ratio over the union of timestamps.
8. **Generate Pass/Fail Verdict**: Fail if the missing ratio exceeds tolerance, any bar
   exceeds the price threshold, any integrity issue was recorded, or the vendors have zero
   overlapping timestamps.

## Interpreting the Report

- `matched_bars` — timestamps present in both vendors.
- `comparable_bars` — matched bars that survived integrity screening and contributed to
  `avg_close_delta_bps` / `max_close_delta_bps`. A large gap between the two means the
  delta statistics describe only a fraction of the overlap and should not be quoted alone.
- `integrity_issues` — unusable bars and duplicate timestamps, each with vendor and reason.
- `volume_flagged_bars` — bars to route to a duplicate-reporting audit.

## Production Implementation Reference

- Reference code: `scripts/vendor_cross_validator.py` (`DataVendorCrossValidator`,
  `CrossValidationReport`, `BarDiscrepancy`, `VolumeDiscrepancy`, `BarIntegrityIssue`).
- Automated unit tests: `scripts/test_vendor_cross_validator.py`.

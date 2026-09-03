# Pre-Flight / Sign-off Checklist — data-vendor-cross-validation-for-backtests

- [ ] **Vendor Independence:** Confirm the two feeds are not resellers of the same primary source.
- [ ] **Non-Empty Inputs:** Confirm both datasets loaded non-zero bars — a PASS on empty input is not a PASS.
- [ ] **Timestamp Alignment:** Confirm both vendors were normalised to one canonical UTC timestamp string, and that `matched_bars > 0`.
- [ ] **Duplicate Timestamps:** Confirm no repeated timestamps within either vendor feed.
- [ ] **Bar Integrity:** Confirm no NaN/Inf closes, negative volumes, or zero-close sentinel bars are present.
- [ ] **Price Discrepancy Flagging:** Confirm bars exceeding threshold are flagged, and that thresholds were calibrated for this asset class and bar interval.
- [ ] **Volume Audit:** Review `volume_flagged_bars` for duplicate reporting (audit-only; does not fail the verdict).
- [ ] **Missing Bar Audit:** Confirm missing bar ratio is computed and tolerance enforced.
- [ ] **Adjusted vs Raw Awareness:** Confirm both vendors provide same adjustment type.
- [ ] **Coverage of Statistics:** Confirm `comparable_bars` is close to `matched_bars` before quoting average/max delta.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/data-vendor-cross-validation-for-backtests/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

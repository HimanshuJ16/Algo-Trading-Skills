# Pre-Flight / Sign-off Checklist — clock-skew-correction-for-tick-timestamps

Use this before considering the skill's implementation complete.

- [ ] **Outlier Jitter Filtering:** Confirm network transport spikes $> 3\times\text{MAD}$ are rejected.
- [ ] **EWMA Smoothing:** Confirm smoothing parameter $\alpha$ updates skew estimate smoothly.
- [ ] **Timestamp Calibration:** Confirm `calibrate_timestamp()` applies estimated skew to local timestamps.
- [ ] **Threshold Alerting:** Confirm warnings trigger if $|\hat{\Delta}| > 100\text{ms}$.
- [ ] **Automated Testing:** Run `python scripts/test_clock_skew_corrector.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

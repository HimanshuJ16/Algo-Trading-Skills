# Pre-Flight / Sign-off Checklist — broker-side-order-throttle-detection

Use this before considering the skill's implementation complete.

- [ ] **ACK Timestamp Precision:** Confirm submission and ACK timestamps use high-precision monotonic timers.
- [ ] **EWMA/EWMVar Calculation:** Confirm exponential weighting correctly tracks network changes without zero-variance errors (clamped).
- [ ] **Statistical & Hard Thresholds:** Confirm RTT spikes $>3\sigma$ (with EWMVar) or $>500\text{ms}$ trigger `SILENT_THROTTLE`.
- [ ] **AIMD Backoff Execution:** Confirm order pacing applies Additive Increase (linear delay decay) and Multiplicative Decrease (exponential delay jump) properly.
- [ ] **Automated Testing:** Run `python scripts/test_throttle_detector.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

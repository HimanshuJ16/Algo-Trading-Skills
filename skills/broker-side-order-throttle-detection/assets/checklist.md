# Pre-Flight / Sign-off Checklist — broker-side-order-throttle-detection

Use this before considering the skill's implementation complete.

- [ ] **ACK Timestamp Precision:** Confirm submission and ACK timestamps use high-precision timers.
- [ ] **Sliding Baseline Calculation:** Confirm rolling mean $\mu$ and standard deviation $\sigma$ update across recent orders.
- [ ] **Statistical & Hard Thresholds:** Confirm RTT spikes $>3\sigma$ or $>500\text{ms}$ trigger `SILENT_THROTTLE`.
- [ ] **Adaptive Backoff Execution:** Confirm order pacing delay is applied when throttled.
- [ ] **Automated Testing:** Run `python scripts/test_throttle_detector.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

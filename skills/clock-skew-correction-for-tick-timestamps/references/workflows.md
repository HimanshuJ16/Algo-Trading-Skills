# Deep Workflow Reference — clock-skew-correction-for-tick-timestamps

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Ingest Exchange & Local Receipt Timestamps:**
   - Record `T_local` at exact socket read timestamp.
   - Extract `T_exchange` from tick JSON payload.

2. **Network Jitter Outlier Filtering:**
   - Calculate Median Absolute Deviation (MAD) over rolling sample window ($N=50$).
   - Reject raw samples where $|\Delta_{\text{raw}} - \mu| > 3 \times \text{MAD}$ to isolate clock drift from network transport latency spikes.

3. **Update EWMA Clock Skew Estimate:**
   - Compute smoothed clock skew estimate: $\hat{\Delta}_t = \alpha \cdot \Delta_{\text{raw}} + (1 - \alpha) \cdot \hat{\Delta}_{t-1}$.

4. **Calibrate Local Timestamps:**
   - Apply estimated skew: $T_{\text{calibrated}} = T_{\text{local}} + \hat{\Delta}_t$.

5. **Clock Drift Alarm Gate:**
   - Issue warning alert if $|\hat{\Delta}_t| > 100\text{ms}$ to notify infrastructure operations.

## Failure Modes Observed in Production

- **Confounding Transit Time with Drift:** Treating one-way network packet latency as clock skew, skewing tick timestamps.
- **Unfiltered Outlier Pollution:** Allowing network retransmission spikes to distort clock skew calibration.

## Production Implementation Reference

- Reference code: `scripts/clock_skew_corrector.py` (`ClockSkewCorrector`, `ClockSkewResult`).
- Automated unit tests: `scripts/test_clock_skew_corrector.py`.

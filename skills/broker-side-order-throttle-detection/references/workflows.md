# Deep Workflow Reference — broker-side-order-throttle-detection

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Order Submission & ACK Timestamping**:
   - Record exact $t_{\text{sub}}$ when order payload is dispatched.
   - Record exact $t_{\text{ack}}$ when broker ACK payload is received.
   - Compute $\text{RTT} = (t_{\text{ack}} - t_{\text{sub}}) \times 1000$ ms.

2. **EWMA Baseline Calculation**:
   - Use Welford's online algorithm for computing Exponentially Weighted Moving Average (EWMA) and Variance (EWMVar) using a smoothing factor $\alpha$ (e.g., 0.1).
   - Compute standard deviation as $\sigma = \sqrt{\max(\text{EWMVar}, \text{MinVarClamp})}$.

3. **Silent Throttle Detection**:
   - Flag `SILENT_THROTTLE` if $\text{RTT} \ge \text{EWMA} + 3\sigma$ or $\text{RTT} \ge 500\text{ms}$.

4. **AIMD Adaptive Backoff Dispatch**:
   - Apply Additive Increase, Multiplicative Decrease (AIMD) logic for congestion control.
   - On throttle: Multiplicatively increase backoff delay (e.g., factor of 2.0).
   - On normal: Additively decrease backoff delay (e.g., step of -20ms).
   - Clamp backoff between `min_backoff_ms` and `max_backoff_ms`.

## Production Implementation Reference

- Reference code: `scripts/throttle_detector.py` (`OrderThrottleDetector`, `ThrottleState`, `ThrottleStatusReport`).
- Automated unit tests: `scripts/test_throttle_detector.py`.

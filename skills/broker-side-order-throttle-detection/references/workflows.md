# Deep Workflow Reference — broker-side-order-throttle-detection

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Order Submission & ACK Timestamping**:
   - Record exact $t_{\text{sub}}$ when order payload is dispatched.
   - Record exact $t_{\text{ack}}$ when broker ACK payload is received.
   - Compute $\text{RTT} = (t_{\text{ack}} - t_{\text{sub}}) \times 1000$ ms.

2. **Sliding Window Baseline Calculation**:
   - Maintain sliding window of $N=50$ recent orders to compute mean $\mu$ and standard deviation $\sigma$.

3. **Silent Throttle Detection**:
   - Flag `SILENT_THROTTLE` if $\text{RTT} \ge \mu + 3\sigma$ or $\text{RTT} \ge 500\text{ms}$.

4. **Adaptive Backoff Dispatch**:
   - Apply recommended backoff delay (100ms to 2000ms) between outbound orders when throttled.

## Production Implementation Reference

- Reference code: `scripts/throttle_detector.py` (`OrderThrottleDetector`, `ThrottleState`, `ThrottleStatusReport`).
- Automated unit tests: `scripts/test_throttle_detector.py`.

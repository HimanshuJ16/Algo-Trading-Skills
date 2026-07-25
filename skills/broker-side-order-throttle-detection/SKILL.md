---
name: broker-side-order-throttle-detection
description: >-
  Use when operating high-frequency or high-volume trading bots to measure order acknowledgment round-trip latency (ACK RTT), detect silent broker-side order throttling during market volatility using Exponentially Weighted Moving Average (EWMA) and Variance (EWMVar), and dynamically back off order dispatch using an AIMD congestion control engine.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "order-throttle", "latency-monitoring", "ack-rtt", "silent-throttling", "aimd-backoff", "ewma-anomaly-detection"]
brokers_frameworks: ["Broker Throttle Detector", "Python High-Frequency Engine"]
version: "2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing order flow on broker APIs during high market volatility or news events. Brokers often silently throttle order processing (delaying order acknowledgments from <20ms to >800ms) without returning HTTP 429 rate limit error codes. Unmonitored silent throttling causes order queue buildup, stale fills, and slippage. This skill measures ACK round-trip time (RTT), detects statistical latency anomalies dynamically via EWMA and EWMVar ($Z \ge 3.0$), and applies Additive Increase, Multiplicative Decrease (AIMD) backoff.

## Prerequisites

- High-precision order submission timestamping and ACK event callback listeners.
- Understanding of smoothing factors (alpha) for exponential weighting.

## Workflow

1. **Record Order ACK Latency**:
   - Capture submission timestamp $t_{\text{sub}}$ and acknowledgment timestamp $t_{\text{ack}}$. Compute $\text{RTT} = (t_{\text{ack}} - t_{\text{sub}}) \times 1000$ ms.

2. **Calculate Baseline Latency Statistics (EWMA, EWMVar)**:
   - Continuously update EWMA and EWMVar for latency to adjust to structural network shifts quickly without $O(N)$ memory requirements.

3. **Detect Silent Throttle Anomalies**:
   - Flag `SILENT_THROTTLE` if current RTT exceeds threshold:
     $$\text{RTT} > \text{EWMA} + 3 \times \text{EWMStd} \quad \text{or} \quad \text{RTT} > 500\text{ms}$$

4. **Apply AIMD Order Dispatch Backoff**:
   - When silent throttling is flagged, multiply the backoff delay (Multiplicative Decrease of dispatch rate). When normal, linearly decrease the backoff (Additive Increase of dispatch rate).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Network Jitter with Broker Throttling**: Treating isolated single-packet network delays as systemic broker throttling. EWMVar handles jitter better than simple sliding windows.
- **Ignoring Minimum Variance Clamping**: Micro-bursting in a highly deterministic network can drop variance to near-zero, causing false positives on tiny 1ms delays. A variance clamp fixes this.
- **Static Latency Thresholds**: Using hardcoded latency limits that don't adjust to changing network conditions across trading sessions.

## Verification

- Simulate baseline RTT (15ms) followed by a 600ms latency spike and verify `SILENT_THROTTLE` classification.
- Confirm adaptive backoff delay applies AIMD logic correctly (multiplicative jump, additive decay).
- Run `python scripts/test_throttle_detector.py` and confirm 100% pass rate.

## Related Skills

- `multi-broker-rate-limit-handling`
- `tick-buffering-burst-handling`
- `structured-logging-for-post-incident-forensics`

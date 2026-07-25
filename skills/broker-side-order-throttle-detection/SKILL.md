---
name: broker-side-order-throttle-detection
description: >-
  Use when operating high-frequency or high-volume trading bots to measure order acknowledgment round-trip latency (ACK RTT), detect silent broker-side order throttling during market volatility, and dynamically back off order dispatch before order queues overflow.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "order-throttle", "latency-monitoring", "ack-rtt", "silent-throttling", "backoff"]
brokers_frameworks: ["Broker Throttle Detector", "Python High-Frequency Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing order flow on broker APIs during high market volatility or news events. Brokers often silently throttle order processing (delaying order acknowledgments from <20ms to >800ms) without returning HTTP 429 rate limit error codes. Unmonitored silent throttling causes order queue buildup, stale fills, and slippage. This skill measures ACK round-trip time (RTT), detects statistical latency anomalies ($\mu + 3\sigma$), and applies dynamic backoff.

## Prerequisites

- High-precision order submission timestamping and ACK event callback listeners.
- Rolling window size configuration (e.g., 50 recent orders).

## Workflow

1. **Record Order ACK Latency**:
   - Capture submission timestamp $t_{\text{sub}}$ and acknowledgment timestamp $t_{\text{ack}}$. Compute $\text{RTT} = (t_{\text{ack}} - t_{\text{sub}}) \times 1000$ ms.

2. **Calculate Baseline Latency Statistics ($\mu, \sigma$)**:
   - Maintain sliding window of $N$ recent order RTT measurements to compute baseline mean $\mu$ and standard deviation $\sigma$.

3. **Detect Silent Throttle Anomalies**:
   - Flag `SILENT_THROTTLE` if current RTT exceeds threshold:
     $$\text{RTT} > \mu + 3\sigma \quad \text{or} \quad \text{RTT} > 500\text{ms}$$

4. **Apply Dynamic Order Dispatch Backoff**:
   - When silent throttling is flagged, inject adaptive backoff delay (e.g., 100ms to 1000ms) between subsequent orders to prevent buffer overflows.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Network Jitter with Broker Throttling**: Treating isolated single-packet network delays as systemic broker throttling.
- **Ignoring Order Type Processing Differences**: Comparing ACK latency of simple Limit orders against complex multi-leg Options or Bracket orders.
- **Static Latency Thresholds**: Using hardcoded latency limits that don't adjust to changing network conditions across trading sessions.

## Verification

- Simulate baseline RTT (15ms) followed by a 600ms latency spike and verify `SILENT_THROTTLE` classification.
- Confirm adaptive backoff delay is calculated and applied to subsequent order submissions.
- Run `python scripts/test_throttle_detector.py` and confirm 100% pass rate.

## Related Skills

- `multi-broker-rate-limit-handling`
- `tick-buffering-burst-handling`
- `structured-logging-for-post-incident-forensics`
---

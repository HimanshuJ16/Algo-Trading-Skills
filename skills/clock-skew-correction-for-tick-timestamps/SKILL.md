---
name: clock-skew-correction-for-tick-timestamps
description: Quantitative market data pipeline utility for estimating and correcting
  clock drift between venue feeds and local recorders using minimum-delay filtering
  without violating time monotonicity.
domain: Data Management
subdomain: Market Data Infrastructure
tags:
- clock-skew
- paxson-algorithm
- timestamps
- market-data
- hft
- monotonicity
brokers_frameworks:
- Generic Infrastructure
- NumPy
- Pandas
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing multi-venue high-frequency tick data or order book message logs captured across different hosts. Variations in local clock synchronization (NTP drift, host-level clock skew) cause measured delays to drift over time. This utility estimates the linear drift via minimum-delay lower-bound filtering (Paxson's algorithm principle) and adjusts the timestamps while strictly maintaining monotonicity.

## Prerequisites

- Paired timestamp data: `exchange_timestamp` (sender) and `local_timestamp` (receiver) for market events.
- Monotonically increasing event sequence numbers.

## Workflow

1. **One-Way Delay Calculation**: Compute raw one-way delay: $Delay_i = T_{local, i} - T_{exchange, i}$.
2. **Minimum Delay Filtering**: Group time into rolling bins or expanding windows and extract the minimum delay points. Because true network latency has a hard lower bound, minimum delays reflect pure clock offset without network queueing noise.
3. **Linear Regression Fit**: Fit a linear model ($Offset(t) = a + b \cdot t$) to the lower-bound delay points to find the clock drift rate ($b$).
4. **Correction Application**: Subtract the estimated $Offset(t)$ from $T_{local, i}$.
5. **Monotonicity Enforcement**: Ensure $T_{corrected, i} \ge T_{corrected, i-1}$. If a correction would force a timestamp backward, clamp it to $T_{corrected, i-1} + \epsilon$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mean/Median Regression**: Using ordinary least squares on *all* delay points. Network queueing spikes (jitter) pollute the mean, causing massive over-estimation of clock offset. You MUST filter for minimum delays.
- **Breaking Monotonicity**: Applying raw linear corrections without checking $T_i \ge T_{i-1}$, causing time to jump backward and throwing off backtesting engines or order-sequence matching.
- **In-Sample Overfitting**: Estimating clock skew on the entire day's dataset and applying it backwards (lookahead leakage). Calibration should be done on a rolling expanding window.

## Verification

- Simulate 1,000 tick messages with a constant clock drift rate (e.g. 50 microseconds per second) and random positive network delays. Run the `ClockSkewCorrector`. Verify that the estimated drift rate matches the true drift within 5% tolerance and that all output timestamps are strictly monotonic.
- Run `python scripts/test_clock_skew_corrector.py`.

## Related Skills

- `clock-drift-monitoring-alerting-thresholds`
- `historical-order-book-reconstruction-from-message-logs`

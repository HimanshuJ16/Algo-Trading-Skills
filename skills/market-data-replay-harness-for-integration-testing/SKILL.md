---
name: market-data-replay-harness-for-integration-testing
description: >-
  Use when integration-testing trading engines and execution algorithms to replay recorded historical tick sessions at deterministic speeds (1x real-time, 10x fast-forward) to verify exact strategy event handling.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "tick-replay", "integration-testing", "deterministic-backtest", "time-warp", "event-driven"]
brokers_frameworks: ["Tick Replay Harness", "Python Async Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when performing end-to-end integration testing of strategy algorithms, risk managers, or order management engines against historical high-frequency sessions (e.g. Flash Crash of May 2010, CPI release volatility). Mocking synthetic ticks fails to capture real market micro-bursts or tick arrival spacing. A deterministic replay harness streams recorded tick logs through the active pipeline at controlled speeds, verifying that strategy outputs match expected baselines.

## Prerequisites

- Recorded market data session log (CSV, Parquet, or PCAP tick files with relative timestamps).
- Target strategy or pipeline callback listener.

## Workflow

1. **Load Recorded Tick Session Log**:
   - Ingest sorted tick data containing symbol, timestamp, bid, ask, volume, and sequence ID.

2. **Configure Speed Multiplier**:
   - Set speed factor $S$: $S=1.0$ (1x real-time speed), $S=10.0$ (10x fast-forward speed), or $S=\infty$ (ASAP mode).

3. **Replay Event Stream with Intra-Tick Delays**:
   - Calculate sleep delay $\Delta t_{\text{sleep}} = \frac{t_{i+1} - t_i}{S}$ and dispatch tick payload to subscriber callbacks.

4. **Verify Strategy Order Audit Log**:
   - Compare strategy-generated orders and risk events against expected deterministic regression baselines.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Non-Deterministic System Clocks**: Strategy code calling `time.time()` instead of using the replayed tick timestamp, causing non-deterministic backtest behavior.
- **Unsorted Input Ticks**: Replaying tick files that contain out-of-order timestamps, corrupting time-warp sleep calculations.
- **Ignoring Speed Limits in ASAP Mode**: Pushing millions of ticks ASAP without throttling when downstream components require realistic latency simulation.

## Verification

- Replay 100 ticks at $10\times$ speed factor and verify exact relative tick arrival intervals.
- Verify strategy callback receives 100% of replayed ticks in deterministic order.
- Run `python scripts/test_replay_harness.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-optimization-window-management`
- `paper-to-live-promotion-checklist`
- `structured-logging-for-post-incident-forensics`
---

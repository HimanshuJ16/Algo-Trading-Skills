# Real-Time Architecture Standards — market-data-replay-harness-for-integration-testing

| Speed Mode | Multiplier ($S$) | Description |
|---|---|---|
| Real-Time Mode | $S = 1.0$ | Replays ticks with exact real-world timestamp spacing |
| Fast-Forward | $S = 10.0$ to $100.0$ | Accelerated replay for integration test suites |
| ASAP Mode | $S = \infty$ | Zero-delay burst replay for maximum throughput backtests |

## Category

`real-time-architecture` — see top-level `mappings/` directory.

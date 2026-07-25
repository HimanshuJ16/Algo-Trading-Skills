# Deep Workflow Reference — market-data-replay-harness-for-integration-testing

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Load Recorded Tick Session Log**:
   - Ingest sorted tick data containing symbol, timestamp, bid, ask, volume, and sequence ID.

2. **Configure Speed Factor**:
   - Set speed multiplier $S$ ($1.0$ for real-time, $10.0$ for fast-forward, or ASAP mode).

3. **Replay Event Loop**:
   - Compute wall-clock sleep delay $\Delta t_{\text{sleep}} = \frac{t_{i+1} - t_i}{S}$.
   - Dispatch tick to subscriber strategy callback functions.

4. **Audit Strategy Outputs**:
   - Verify generated order parameters against expected deterministic regression baselines.

## Production Implementation Reference

- Reference code: `scripts/replay_harness.py` (`MarketDataReplayHarness`, `ReplayTick`, `ReplaySessionSummary`).
- Automated unit tests: `scripts/test_replay_harness.py`.

# Deep Workflow Reference — stress-testing-against-historical-crash-scenarios

## Full Procedure

1. **Build Crash Scenario Library:**
   - Define named scenarios (2008 GFC, 2015 Flash Crash, 2020 COVID) with per-asset cumulative return shocks.
   - Include a DEFAULT fallback return for assets not explicitly listed.

2. **Replay Current Positions:**
   - For each scenario, compute $\Delta\text{NAV}_s = \sum_i V_i \cdot R_{i,s}$ where $V_i$ is position value.

3. **Identify Worst-Case:**
   - Select $\arg\min_s \Delta\text{NAV}_s$ as worst-case scenario.

4. **Enforce Gate:**
   - If $|\Delta\text{NAV}_{\text{worst}}| / \text{NAV} \ge \text{threshold}$, block new entries.

## Production Implementation Reference

- Code: `scripts/stress_tester.py` (`HistoricalStressTester`, `CrashScenario`, `StressTestReport`).
- Tests: `scripts/test_stress_tester.py`.

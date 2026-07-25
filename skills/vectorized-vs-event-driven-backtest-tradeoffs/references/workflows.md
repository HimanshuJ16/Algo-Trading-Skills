# Deep Workflow Reference — vectorized-vs-event-driven-backtest-tradeoffs

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Audit Strategy Execution Complexity**:
   - Assess trading frequency, limit orders, and path dependency to determine engine score.

2. **Execute Fast Vectorized Backtest**:
   - Perform array-based matrix return computations ($O(N)$ speed) for rapid parameter search.

3. **Execute Event-Driven Backtest**:
   - Process discrete bar/tick events through state machine with realistic fill delays and slippage.

4. **Quantify Execution Drag & Parity**:
   - Calculate Sharpe divergence $\Delta_{\text{Sharpe}} = \text{Sharpe}_{\text{vector}} - \text{Sharpe}_{\text{event}}$ and speedup factor.

## Production Implementation Reference

- Reference code: `scripts/engine_selector.py` (`DualBacktestEngineSelector`, `RecommendedEngine`, `DualEngineAuditReport`).
- Automated unit tests: `scripts/test_engine_selector.py`.

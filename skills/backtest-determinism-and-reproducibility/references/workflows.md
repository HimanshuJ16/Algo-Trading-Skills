# Deep Workflow Reference — backtest-determinism-and-reproducibility

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Inject Master Random Seeds**:
   - Seed `random.seed(S)`, `np.random.seed(S)`, and set `PYTHONHASHSEED=0`.

2. **Deterministic Event Sorting**:
   - Sort input event streams by `(timestamp, symbol, sequence_id)`.

3. **Simulated Clock Binding**:
   - Bind strategy time logic to event stream timestamps ($T_{\text{sim}}$) rather than system clock `time.time()`.

4. **Cryptographic SHA256 Audit Verification**:
   - Dump trade execution log to canonical JSON and compute SHA256 checksum. Assert $\text{Checksum}_1 == \text{Checksum}_2$.

## Production Implementation Reference

- Reference code: `scripts/reproducibility_engine.py` (`BacktestDeterminismEngine`, `TradeExecutionRecord`, `DeterministicAuditReport`).
- Automated unit tests: `scripts/test_reproducibility_engine.py`.

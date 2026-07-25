# Deep Workflow Reference — order-book-imbalance-signal-pipeline

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest L2 Book Depth directly on Fast-Path**:
   - Receive L2 top-of-book levels $(P_{\text{bid}}, V_{\text{bid}})$ and $(P_{\text{ask}}, V_{\text{ask}})$.

2. **Compute Imbalance & Micro-Price**:
   - Order Book Imbalance $I = \frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$.
   - Volume-Weighted Micro-Price $P_{\text{micro}} = \frac{V_{\text{bid}} P_{\text{ask}} + V_{\text{ask}} P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$.

3. **Signal Classification**:
   - $I \ge +0.60$: Emit `HIGH_BUY_PRESSURE`.
   - $I \le -0.60$: Emit `HIGH_SELL_PRESSURE`.

4. **Direct Fast-Path Execution Dispatch**:
   - Send signal directly to execution loop, bypassing intermediate message queues.

## Production Implementation Reference

- Reference code: `scripts/imbalance_pipeline.py` (`FastPathOBIPipelineEngine`, `L2OrderBookTop`, `OBISignalResult`).
- Automated unit tests: `scripts/test_imbalance_pipeline.py`.

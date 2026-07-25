# Pre-Flight / Sign-off Checklist — order-book-imbalance-signal-pipeline

Use this before considering the skill's implementation complete.

- [ ] **L2 Fast-Path Ingestion:** Confirm L2 top-of-book updates bypass standard OHLC logging queues.
- [ ] **Imbalance Calculation:** Confirm $I = \frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$ is computed in range $[-1.0, +1.0]$.
- [ ] **Micro-Price Calculation:** Confirm volume-weighted micro-price $P_{\text{micro}}$ is evaluated.
- [ ] **Zero Volume Guard:** Confirm $V_{\text{bid}} + V_{\text{ask}} = 0$ handles zero-division cleanly.
- [ ] **Automated Testing:** Run `python scripts/test_imbalance_pipeline.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

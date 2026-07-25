# Deep Workflow Reference — risk-control-latency-budget

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Record Pipeline Timestamps**:
   - Ingestion: $\Delta t_{\text{ingest}} = T_{\text{start}} - T_{\text{event}}$
   - Evaluation: $\Delta t_{\text{eval}} = T_{\text{finish}} - T_{\text{start}}$
   - Transmission: $\Delta t_{\text{send}} = T_{\text{order\_sent}} - T_{\text{finish}}$
2. **Calculate Total Latency**: $\Delta t_{\text{total}} = \Delta t_{\text{ingest}} + \Delta t_{\text{eval}} + \Delta t_{\text{send}}$.
3. **Audit Against Latency SLA**: Compare $\Delta t_{\text{total}}$ vs SLA budget $L_{\text{budget\_ms}}$ (e.g. 50 ms).
4. **Identify Latency Bottlenecks**: Determine largest contributing component (`INGESTION`, `EVALUATION`, `TRANSMISSION`).

## Production Implementation Reference

- Reference code: `scripts/risk_latency_budgeter.py` (`RiskControlLatencyBudgeter`, `RiskLatencyTrace`, `LatencyAuditSummary`).
- Automated unit tests: `scripts/test_risk_latency_budgeter.py`.

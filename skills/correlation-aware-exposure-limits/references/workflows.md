# Deep Workflow Reference — correlation-aware-exposure-limits

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Calculate Rolling Correlation Matrix:**
   - Compute pairwise Pearson correlation coefficients from price log-returns across a rolling historical window (e.g. 60–90 days).
   - Validate matrix freshness: track `matrix_timestamp` and flag matrices older than 7 days as stale before running trade approvals.

2. **Cluster Universe Construction:**
   - Group instruments into clusters using connected components where pairwise correlation $\ge \text{threshold}$ (e.g. 0.70) or where instruments share a common sector classification.
   - Re-cluster dynamically on scheduled intervals (e.g. weekly or pre-session).

3. **Evaluate Proposed Position against Limits:**
   - **Total Portfolio Cap:** Verify total portfolio notional after proposed trade does not breach `max_portfolio_notional`.
   - **Cluster Exposure Cap:** Identify the target cluster for the proposed symbol. Compute existing notional exposure across all active positions in the cluster:
     $$\text{Cluster Exposure} = \sum_{i \in \text{Cluster}} |\text{Notional}_i|$$
   - Evaluate proposed position: if $\text{Cluster Exposure} + \text{Proposed Notional} > \text{Max Cluster Limit}$, scale down proposed notional to:
     $$\text{Allowed Notional} = \max(0, \text{Max Cluster Limit} - \text{Cluster Exposure})$$

4. **Options Delta / Factor Exposure Weighting:**
   - For option contracts across strikes/expiries or sector ETFs, weight proposed notional by underlying delta $\Delta$:
     $$\text{Effective Proposed Notional} = |\text{Proposed Notional}| \times |\Delta|$$

5. **Audit Logging & Telemetry:**
   - Record every risk evaluation in `PositionAuditLog` with timestamp, symbol, proposed notional, approved notional, decision reason, and cluster ID.

## Failure Modes Observed in Production

- **Per-Instrument Tunnel Vision:** Approving multiple positions in the same sector (e.g., 5 bank stocks) because each individual position is below its single-instrument limit.
- **Stale Correlation Matrix:** Operating risk checks on correlation matrices calculated months ago, missing recent regime shifts and market structural changes.
- **Unweighted Derivative Exposure:** Treating different option strikes on the same underlying as independent, underestimating true directional risk.
- **Silent Order Drop:** Rejecting or scaling down orders without audit logging, rendering risk actions indistinguishable from signal generator failures.

## Production Implementation Reference

- Reference code: `scripts/exposure_limits.py` (`CorrelationExposureManager`, `PositionAuditLog`, `RiskCheckResult`).
- Automated unit tests: `scripts/test_exposure_limits.py`.

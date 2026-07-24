# Deep Workflow Reference — correlation-aware-exposure-limits

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Estimate Rolling Pearson Correlation Matrix:**
   - Compute pairwise correlations $C_{i,j}$ over historical return vectors.

2. **Form Correlation Clusters:**
   - Group symbols into connected clusters where pairwise correlation $C_{i,j} \ge \rho_{\text{threshold}}$.

3. **Compute Current Cluster Exposure:**
   - Sum position valuations for all constituents of cluster $k$:
     $$\text{ExposurePct}(G_k) = \frac{\sum_{i \in G_k} |Q_i \cdot P_i|}{\text{Portfolio NAV}}$$

4. **Validate Proposed Order Value:**
   - Check if $\text{ExposurePct}(G_k) + \frac{|V_{\text{proposed}}|}{\text{NAV}} > \text{MaxClusterExposurePct}$.
   - Veto or downsize order if limit is breached.

## Failure Modes Observed in Production

- **Single-Symbol Limit Blind Spot:** Setting 5% limits per ticker while holding 8 tech stocks (40% total tech exposure).
- **Static Correlation Assumption:** Assuming historical correlations remain static during market crises.

## Production Implementation Reference

- Reference code: `scripts/correlation_manager.py` (`CorrelationExposureManager`, `ClusterInfo`, `OrderValidationResult`).
- Automated unit tests: `scripts/test_correlation_manager.py`.

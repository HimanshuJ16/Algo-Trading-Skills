# Deep Workflow Reference — correlation-aware-exposure-limits

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not while deciding whether it applies.

## Full Procedure

1. **Estimate Rolling Pearson Correlation Matrix** (`update_correlation_matrix`):
   - Compute pairwise correlations $C_{i,j}$ over historical return vectors.
   - Input contract: chronological price series (oldest first), every price positive and finite, at least 2 prices per symbol, timestamp timezone-aware. Violations raise `ValueError` — bad data is never silently skipped or correlated.
   - Series of different lengths are correlated over their **most recent** overlapping returns (a 30-day recent listing vs 60-day names uses the last 29/30 returns of each), with a warning logged. Truncating at the oldest end would compare returns from different dates.
   - Zero-variance overlap (constant/pegged series) yields undefined correlation: logged and treated as 0.0 (uncorrelated), never clustered by accident.

2. **Form Correlation Clusters** (`_rebuild_clusters`):
   - Connected components over edges where $C_{i,j} \ge \rho_{\text{threshold}}$ — transitive: A–B and B–C at threshold form {A, B, C} even if A–C is below threshold.
   - Sector override: symbols sharing a non-None `sector_mapping` label are clustered together regardless of measured correlation (one risk pocket per sector).
   - Negative correlations do NOT cluster (threshold applies to raw $\rho$, not $|\rho|$): negatively correlated assets hedge rather than concentrate.

3. **Compute Current Cluster Exposure**:
   - Gross, delta-adjusted exposure of cluster $k$:
     $$\text{Exposure}(G_k) = \sum_{i \in G_k} |w_i \cdot \text{Position}_i|$$
     with $w_i$ = underlying delta (default 1.0 for cash instruments). Gross (no netting of longs against shorts) is deliberate: correlation hedges fail in stress. Percentage view: divide by NAV; the manager enforces absolute notional caps, so convert a NAV-% policy to notional at construction.

4. **Validate Proposed Order** (`evaluate_proposed_position`):
   - Fail closed first: no matrix built → raises `CorrelationMatrixUnavailableError`; matrix older than `max_matrix_age_days` (default 7) raises under `stale_matrix_policy="block"` or logs under `"warn"` (use `"block"` in production).
   - Portfolio cap: post-trade RAW gross notional must be within `max_portfolio_notional`. The proposed leg is counted whether or not the symbol already appears in `current_positions`, so opening orders consume aggregate headroom. Unlike the cluster cap this is deliberately not delta-adjusted — it is a capital/notional limit. Reductions that leave the portfolio over cap are approved (remediation required), increases are vetoed with `allowed_notional` = remaining headroom.
   - Cluster cap with exact post-trade math: a new position adds $|V_{\text{proposed}}|$; an increment against an existing position nets first ($|\text{Position}_i + V_{\text{proposed}}|$). Risk-REDUCING orders are approved even at breach (reason flags "remediation required"); exposure-increasing or neutral orders are vetoed with `RiskCheckResult(approved=False)` and an indicative `allowed_notional = remaining cap / delta weight`.
   - Symbols absent from the matrix are treated as their own single-symbol cluster (warning logged) — feed new listings into the next matrix refresh.
   - Every decision is appended to `audit_trail` (`PositionAuditLog`).

## Known Failure Modes

- **Single-Symbol Limit Blind Spot:** Setting 5% limits per ticker while holding 8 tech stocks (40% total tech exposure).
- **Static Correlation Assumption:** Assuming historical correlations remain static during market crises.
- **Fail-Open Gates:** Approving orders on a missing/stale matrix, silently disabling cluster limits.
- **Blocked De-Risking:** Veto logic that adds $|V|$ to current exposure refuses partial reductions of an over-cap cluster, exactly when reducing is most urgent.

## Production Implementation Reference

- Reference code: `scripts/exposure_limits.py` (`CorrelationExposureManager`, `RiskCheckResult`, `PositionAuditLog`, `CorrelationMatrixUnavailableError`; standalone helpers `cluster_by_correlation`, `cluster_exposure`).
- Automated unit tests: `scripts/test_exposure_limits.py`.

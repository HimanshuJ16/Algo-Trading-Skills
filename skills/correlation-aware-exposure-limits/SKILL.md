---
name: correlation-aware-exposure-limits
description: Use when building multi-asset portfolio risk systems to compute pairwise
  correlation matrices, cluster correlated instruments, and cap aggregate cluster
  exposure to prevent risk concentration
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- correlation-matrix
- exposure-limits
- cluster-risk
- concentration-risk
brokers_frameworks:
- PyPFcon
- Riskfolio-Lib
- Pandas
- NumPy
- Custom Portfolio Risk
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a portfolio trades multiple instruments within the same sector, asset class, or macro factor (e.g., holding multiple tech stocks `NVDA`, `AMD`, `MSFT`, or crypto assets `BTC`, `ETH`, `SOL`). Setting individual position limits per ticker alone creates hidden concentration risk: when correlated instruments fall in tandem during market sell-offs, total portfolio drawdown spikes unexpectedly. Estimating rolling return correlation matrices ($C_{i,j}$), grouping instruments into correlated clusters ($\rho \ge 0.70$), and capping total cluster exposure (e.g. $\le 30\%$ NAV) before approving order executions is mandatory.

## Prerequisites

- Historical return series for portfolio instruments over rolling lookback window (e.g. 60 days).
- Defined correlation threshold $\rho_{\text{threshold}}$ (default 0.70).
- Defined maximum allowed cluster exposure cap (default 30% of total portfolio NAV).

## Workflow

1. **Estimate Rolling Correlation Matrix**:
   - Compute pairwise Pearson correlation matrix $C$ over historical return vectors $R_1, R_2, \dots, R_K$:
     $$C_{i,j} = \frac{\text{Cov}(R_i, R_j)}{\sigma_i \cdot \sigma_j}$$

2. **Form Correlation Clusters**:
   - Group assets into clusters $G_1, G_2, \dots, G_m$ where pairwise correlation $C_{i,j} \ge \rho_{\text{threshold}}$.

3. **Compute Current Cluster Exposures**:
   - Calculate current dollar exposure for cluster $k$:
     $$\text{Exposure}(G_k) = \sum_{i \in G_k} |\text{Position}_i \cdot P_i|$$
   - Calculate percentage of portfolio NAV:
     $$\text{ExposurePct}(G_k) = \frac{\text{Exposure}(G_k)}{\text{Portfolio NAV}}$$

4. **Validate Proposed Order Execution**:
   - For proposed order on symbol $i$ with value $V_{\text{proposed}}$:
     - Compute projected cluster exposure: $\text{ProjectedExposurePct}(G_k) = \frac{\text{Exposure}(G_k) + |V_{\text{proposed}}|}{\text{Portfolio NAV}}$.
     - If $\text{ProjectedExposurePct}(G_k) > \text{MaxClusterExposurePct}$, veto or downsize order.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Per-Ticker Limit Blind Spot**: Assuming single-ticker limits (e.g. 5% per stock) prevent risk concentration across 8 tech stocks (40% total tech exposure).
- **Static Correlation Assumptions**: Using static historical correlations without updating rolling matrices, missing correlation breakdown during market crashes.
- **Ignoring Short Positions**: Failing to account for directional correlation when computing net cluster exposures.

## Verification

- Submit returns for highly correlated assets (`NVDA` & `AMD`, $\rho = 0.85$) and verify `CorrelationExposureManager` groups them into the same cluster.
- Submit proposed order that breaches 30% cluster exposure limit and verify order is vetoed with `CorrelationLimitBreachError`.
- Run unit test suite `python scripts/test_correlation_manager.py` and confirm 100% pass rate.

## Related Skills

- `broker-account-margin-call-handling`
- `survivorship-bias-free-universe-construction`
- `ensemble-signal-combination-without-overfitting`
---

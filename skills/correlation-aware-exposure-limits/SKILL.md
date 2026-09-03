---
name: correlation-aware-exposure-limits
description: >-
  Use when a book holds several instruments driven by the same sector or macro factor
  and per-symbol limits hide the real concentration; clusters instruments by pairwise
  correlation and caps aggregate exposure per cluster.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, correlation-matrix, exposure-limits, cluster-risk, concentration-risk
  brokers_frameworks: "PyPFcon; Riskfolio-Lib; Pandas; NumPy; Custom Portfolio Risk"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever a portfolio trades multiple instruments within the same sector, asset class, or macro factor (e.g., holding multiple tech stocks `NVDA`, `AMD`, `MSFT`, or crypto assets `BTC`, `ETH`, `SOL`). Setting individual position limits per ticker alone creates hidden concentration risk: when correlated instruments fall in tandem during market sell-offs, total portfolio drawdown spikes unexpectedly. Estimating rolling return correlation matrices ($C_{i,j}$), grouping instruments into correlated clusters ($\rho \ge 0.70$), and capping total cluster exposure (e.g. $\le 30\%$ NAV) before approving order executions is mandatory.

## When NOT to Use

- **As the only pre-trade control.** Cluster caps bound concentration, not leverage, margin adequacy, drawdown, or per-symbol size. Compose with the risk skills under Related Skills; SEC Rule 15c3-5 expects a control suite, not a single check.
- **When the correlation estimate cannot be trusted.** Fewer than ~30 overlapping returns, a newly listed instrument, or a regime break makes the Pearson estimate too noisy to cluster on. The module fails closed on a missing matrix, but it cannot detect a *statistically weak* one — widen the lookback or fall back to sector-only clustering.
- **For linear factor-risk budgeting.** Connected-component clustering answers "which names move together enough to share a cap," not "how much of my variance is one factor." Use a factor/covariance model for the latter; a single 0.70 edge can chain a long clustering chain into one pocket.
- **As a substitute for netting-aware margin math.** Exposure here is deliberately GROSS, so it will not match broker margin, which does grant offsets. Do not drive collateral decisions from these numbers.
- **For a single-instrument or deliberately paired mandate**, unless caps are set to what the mandate actually authorises.

## Prerequisites

- Historical return series for portfolio instruments over rolling lookback window (e.g. 60 days).
- Defined correlation threshold $\rho_{\text{threshold}}$ (default 0.70).
- Defined maximum allowed cluster exposure cap, expressed as an absolute notional (default 1,000,000; convert a NAV-percentage policy to notional as $\text{cap} = \text{pct} \times \text{NAV}$ at construction time).

## Workflow

1. **Estimate Rolling Correlation Matrix**:
   - Compute pairwise Pearson correlation matrix $C$ over historical return vectors $R_1, R_2, \dots, R_K$:
     $$C_{i,j} = \frac{\text{Cov}(R_i, R_j)}{\sigma_i \cdot \sigma_j}$$
   - Price series must be chronological, positive, finite, and date-aligned at their most recent point; differing lengths are correlated over their most recent overlapping returns. Bad data (zero/negative/NaN prices) is **rejected**, never silently correlated.

2. **Form Correlation Clusters**:
   - Group assets into connected clusters $G_1, G_2, \dots, G_m$ where pairwise correlation $C_{i,j} \ge \rho_{\text{threshold}}$ (transitively: A–B and B–C at threshold join A, B, C even if A–C is below). Symbols sharing a `sector_mapping` label are forced into one cluster regardless of measured correlation — sector co-membership is treated as one risk pocket.

3. **Compute Current Cluster Exposures**:
   - Calculate current GROSS dollar exposure for cluster $k$ (sum of absolute notionals; netting longs against shorts inside a correlated cluster is deliberately not done, because correlations converge in stress and the hedge fails exactly when it matters):
     $$\text{Exposure}(G_k) = \sum_{i \in G_k} |w_i \cdot \text{Position}_i|$$
     where $w_i$ is the underlying delta (options delta-adjusted to underlying-equivalent exposure). Divide by NAV for a percentage view.

4. **Validate Proposed Order Execution**:
   - Fail closed: if no correlation matrix has been built, evaluation **raises** — never approve orders against silently-empty clusters. A stale matrix (default > 7 days) either warns or blocks, per `stale_matrix_policy`.
   - Check the aggregate portfolio cap on post-trade GROSS notional, counting the proposed leg even when the symbol is not yet in the book — an opening order in a fresh symbol must consume portfolio headroom like any other. This cap is raw notional (not delta-adjusted); the cluster cap below is delta-adjusted.
   - For a proposed signed increment $V_{\text{proposed}}$ on symbol $i$, compute the exact post-trade cluster exposure: $\text{Exposure}(G_k) + |V_{\text{proposed}}|$ for a new position, or $|\text{Position}_i + V_{\text{proposed}}|$ netting against an existing one — risk-REDUCING orders are never vetoed, even when a cluster is already over cap (they are approved with a remediation flag).
   - If post-trade exposure exceeds the cap and the order does not reduce it, veto with `RiskCheckResult(approved=False)`; `allowed_notional` carries the indicative downsized size.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Per-Ticker Limit Blind Spot**: Assuming single-ticker limits (e.g. 5% per stock) prevent risk concentration across 8 tech stocks (40% total tech exposure).
- **Static Correlation Assumptions**: Using static historical correlations without updating rolling matrices, missing correlation breakdown during market crashes.
- **Fail-Open Risk Gates**: Approving orders when the correlation matrix is missing or stale turns every symbol into an uncorrelated singleton and silently disables cluster limits. Missing matrix must block; stale matrix should block in production (`stale_matrix_policy="block"`).
- **Vetoing De-Risking Orders**: Adding $|V_{\text{proposed}}|$ on top of current exposure vetoes position REDUCTIONS when a cluster sits near its cap. Net the increment against the existing position and only block exposure-increasing orders.
- **Asymmetric Delta Treatment**: Delta-adjusting the proposed option order but counting existing options at full notional overstates exposure — apply underlying delta weights to both sides.
- **Netting Longs Against Shorts in a Cluster**: Netted cluster exposure assumes the correlation hedge holds through the crash; gross (sum of absolute) exposure is the conservative basis for concentration caps.
- **Misaligned Return Windows**: Correlating truncated prefixes of different-length histories compares returns from different dates. Align on the most recent overlapping returns, and reject (don't skip) bad price data.
- **Aggregate Cap Blind to New Positions**: Computing the post-trade portfolio total by iterating only over symbols already held silently exempts every opening order from the aggregate cap — the book grows past the limit one new symbol at a time while each individual cluster check still passes. Count the proposed leg explicitly when the symbol is absent from the current book.
- **Check-Then-Trade Races**: two concurrent orders can each pass against the same cap and jointly breach it. The manager serializes its own matrix/audit state, but the caller must serialize check-then-place sequences when orders can arrive from multiple threads.

## Verification

- Submit returns for highly correlated assets (`NVDA` & `AMD`, $\rho = 0.85$) and verify `CorrelationExposureManager` groups them into the same cluster.
- Submit proposed order that breaches the cluster notional limit and verify it is vetoed: `RiskCheckResult(approved=False)` with the indicative `allowed_notional` for downsizing.
- Verify an opening order in a symbol not yet held is vetoed when it would push post-trade portfolio gross notional past `max_portfolio_notional`.
- Verify a risk-reducing order on an at-cap cluster is approved, and that evaluating any order before `update_correlation_matrix()` raises `CorrelationMatrixUnavailableError`.
- Run unit test suite `python -m unittest discover -s skills/correlation-aware-exposure-limits/scripts` and confirm 100% pass rate.

## Related Skills

- `broker-account-margin-call-handling`
- `survivorship-bias-free-universe-construction`
- `ensemble-signal-combination-without-overfitting`
---

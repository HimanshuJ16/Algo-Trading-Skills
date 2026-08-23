# Workflows for Counterparty Concentration Risk

1. **Exposure Accounting**:
   - For each broker $k$, sum $\text{Cash}_k + \text{Margin}_k + \text{Positions}_k$ (signed balances; shorts contribute negative position value).
   - Concentration is then measured on the **magnitude** $|\text{Exposure}_k|$: a net-debit or net-short balance is exposure *to* the broker, not headroom beneath its cap. Portfolio NAV remains the signed sum.
   - Unknown broker ids raise — never accept a silent 0.0 exposure for a counterparty you cannot identify.
   - Re-register a `BrokerProfile` to refresh balances (replacement semantics); route against current data, not opening-of-day snapshots.
2. **Pre-Trade Routing Evaluation** (`route_order`) — order value $V$ is added to the target broker's exposure with NAV held constant (margin-financed / externally-sourced convention):
   - Calculate projected weight: $w_{\text{proj}} = |\text{Exposure}_{K_1} + V| / \text{NAV}$ — the order nets against the existing signed balance, then the projected balance is taken on magnitude.
   - If NAV $\le 0$: concentration is unassessable — return a **blocked** decision; never substitute the order value as the denominator.
   - If $w_{\text{proj}} > \text{Max\_Limit}_{K_1}$ OR $\text{CDS}_{K_1} > \text{threshold}$:
     - Search secondary brokers $K_2, K_3$ for the lowest compliant projected weight (ties broken by broker_id for determinism).
3. **Failover Execution Dispatch**:
   - If a compliant secondary exists: re-route order $V$ to the selected broker (`is_rerouted=True`).
   - If none: `blocked=True` — route NOTHING and escalate to manual review; `selected_broker_id` names the original target for audit context only, not a routing instruction.
   - The decision is advisory: broker state is unchanged until the caller executes and re-registers updated balances.
4. **Broker HHI Metric**:
   - Compute $HHI = \sum w_k^2$ with $w_k = |\text{Exposure}_k| / \sum_j |\text{Exposure}_j|$ (1/n for equal exposure, 1.0 for a single broker). Magnitude shares keep the index inside $[1/n, 1]$; signed shares can exceed 1 and stop being a concentration measure at all.
   - A warning is logged when $HHI > 0.35$ (configurable `hhi_alert_threshold`) — treat the level as an engineering default, calibrated to the fund's counterparty policy.
   - If every broker is flat, the index is undefined and `compute_hhi` raises. Do not substitute 0.0: that is the value meaning *perfectly diversified*, and it silently clears any `hhi > threshold` alert.

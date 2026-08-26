# Workflows for Meta-Strategy Signal Arbitration

Arbitration runs per symbol, per pass, and is stateless: the engine holds only its
two configuration values and carries nothing between calls. Anything that must
persist across passes — the previous consensus, current positions, per-strategy
attribution — is the caller's responsibility.

1. **Fail-Closed Input Validation**:
   - Reject an empty signal batch.
   - Reject any signal whose `symbol` differs from the arbitrated symbol. Netting
     across symbols produces an order for an exposure no strategy requested.
   - Reject duplicate `strategy_id`s within a batch; a strategy submitting twice
     would double both its notional and its vote.
   - Require an explicit, finite, strictly positive `weight` for every signalling
     `strategy_id`. There is no fallback weight: a typo'd identifier that silently
     inherits one can outvote every correctly configured strategy and invert the
     consensus sign.
   - Reject non-finite values anywhere, and `raw_signal` / `conviction_score`
     outside $[-1.0, +1.0]$ and $[0.0, 1.0]$. NaN in particular defeats threshold
     comparisons — `nan < deadband` is `False` — so an unvalidated NaN passes the
     deadband gate and reaches an order size.
   - On failure raise `ValueError` and route nothing. The caller must treat this as
     "do not trade this symbol on this pass", not as a retryable condition, and must
     not fall back to the sub-strategies' raw un-netted orders.

2. **Risk-Off Veto Audit**:
   - Any `is_risk_veto == True` short-circuits arbitration. The veto is not a
     weighted input: it wins against unanimous, maximum-conviction alpha from
     strategies holding the entire remaining allocation.
   - Report `status = ARBITRATION_VETO_RISK_OFF`, `net_executable_notional_usd = 0.0`,
     `consensus_signal = 0.0`. Flat, not short — a downstream sizer reading $-1.0$
     would open the maximum short position the veto existed to prevent.
   - Escalation, liquidation of existing exposure, and portfolio-wide halting are
     out of scope here; this layer only declines to add exposure.

3. **Weighted Consensus & Netting**:
   - Consensus is the weight- and conviction-weighted mean of `raw_signal`,
     normalised by the *total weight present in the batch* rather than by strategy
     count, so weights need not sum to 1.0. With inputs validated, the result is
     guaranteed to lie in $[-1.0, +1.0]$.
   - Gross notional is $\sum_k |N_k|$; net is $\sum_k N_k$. The netted volume
     $\text{gross} - |\text{net}|$ is the notional that never reaches a venue.
   - Savings are `netted_volume * cost_bps / 10_000`, where `cost_bps` is a one-way
     all-in cost per unit of notional. Derived from a quoted spread, use the
     half-spread.
   - These figures are only meaningful if `target_notional_usd` carries an exposure
     *change*. Absolute position targets from a non-flat book inflate gross and
     savings, and the engine cannot detect the substitution.

4. **Deadband Filter Audit**:
   - Suppress rebalancing when $|S_{\text{consensus}} - S_{\text{current}}|$ is
     strictly below the threshold. A delta exactly equal to the threshold trades.
   - `current_consensus_signal` defaults to $0.0$, so the first pass for a symbol is
     compared against flat.
   - The gate is on *signal*, not notional: a materially larger requested notional at
     an unchanged consensus is suppressed. Where notional drift matters, compare
     it outside this module.
   - A suppressed pass reports zero netting savings — no order was routed, so netting
     avoided nothing. Attributing the netted volume here would inflate any TCA tally
     that sums the field.

5. **Audit Report Generation**:
   - Emit `MetaStrategyArbitrationReport` with `status`, both notionals, savings,
     consensus, the veto flag, and human-readable `audit_notes`.
   - Consumers branch on `status`. `net_executable_notional_usd == 0.0` appears on
     the veto path, the deadband path, and on a genuine full-offset net order;
     "route no order" and "flatten to zero exposure" are different instructions.

6. **Post-Arbitration Handoff** (outside this module, required in production):
   - Allocate resulting fills back to the contributing sub-strategies, or
     per-strategy P&L and performance measurement silently decay.
   - Apply venue-level self-match prevention and market-access pre-trade risk
     controls downstream; this layer enforces neither.

# Workflows for Cross-Chain Bridge Risk Management

1. **De-Peg Monitoring**:
   - Compute $\text{Depeg Pct} = |P_{wrapped} - P_{native}| / P_{native} \times 100\%$.
   - Decision point: non-positive, non-finite, or non-numeric prices are feed
     failures — raise and investigate, never treat as parity.
   - Compare the EXACT de-peg against the threshold; the 2dp figure on the
     decision is for reporting. Gating on the rounded value silently disables
     any threshold finer than ~0.005%.
   - Consequence: at the boundary the reported and gating values can disagree
     (a true 0.999997% de-peg is approved against a 1.0% threshold while
     reporting 1.0). Reconcile from prices, not from the rounded field.
2. **Cap, Finality & Audit Audit**:
   - Evaluate proposed transfer amount $V > 0$ against the configured
     in-flight NAV cap (default 15%; equality passes).
   - Verify finality latency $\le$ Max SLA Delay (default 120 min; canonical
     rollup L1 withdrawals run to days — Optimism a documented 7-day minimum
     (10,080 min), Arbitrum a 6.4-day assertion challenge window — while
     L2-native finality is minutes). Set the SLA per path.
   - Verify the bridge meets the audit-score floor (default 0 = off).
3. **Routing & Execution**:
   - Approve the transfer on the primary bridge if all checks pass.
   - On primary failure, reroute to the lowest-risk compliant secondary:
     ranked by audit score (desc), then finality (asc).
   - The decision is advisory; the engine mutates nothing.
4. **Book the Execution (caller's obligation)**:
   - After settlement, re-register the bridge with the updated
     `current_inflight_usd` — replacement is the update mechanism.
   - Decision point: an unconfirmed or timed-out transfer must be booked as
     OUTSTANDING, not dropped. Assuming failure understates in-flight capital
     precisely when the bridge is least trustworthy.
   - Skipping this step means every later evaluation is measured against a
     stale balance and the aggregate cap is never enforced.
5. **De-Peg Halt (systemic)**:
   - On de-peg $\ge$ threshold (default 1.0%): block ALL new transfers of the
     impaired wrapped asset — no reroute, the impairment is asset-level.
6. **Emergency Pause**:
   - Treat blocked-with-no-failover as an escalation trigger: rebalance
     routing topology or pause the strategy rather than forcing a transfer.

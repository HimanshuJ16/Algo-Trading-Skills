# Pre-Flight Checklist

- [ ] Are in-flight capital caps (default ≤ 15% NAV) configured for all active bridge protocols, calibrated to tail-loss tolerance ($2B across 13 bridge hacks as of Aug 2022 — historical justification, not a claim about the current threat mix)?
- [ ] Is there a post-execution hook that re-registers each bridge with its updated in-flight balance, and books unconfirmed transfers as outstanding rather than as no-ops?
- [ ] Is real-time wrapped-token de-peg monitoring active with a calibrated halt threshold (default 1.0%), and does the halt apply systemically (no reroute)?
- [ ] Are price feeds validated (positive, finite) — zero/NaN treated as data failures, not parity?
- [ ] Are finality SLAs set per transfer path (minutes for liquidity bridges; days for canonical rollup L1 withdrawals — Optimism a documented 7-day minimum, Arbitrum a 6.4-day challenge window; intermediary-trusting fast bridges shorter)?
- [ ] Do risk gates compare exact values rather than display-rounded ones, so a threshold finer than the reported precision still triggers?
- [ ] Does failover rank candidates by audit score (lowest-risk), not registration order?
- [ ] Is an audit-score floor configured, and understood as a coarse proxy for real audit diligence?
- [ ] Are transfer amounts validated positive and genuinely numeric (negative amounts evade caps; `True` is an int subclass and would pass as $1)?
- [ ] Are percentage-point fields (`max_depeg_threshold_pct`, `audit_score_pct`) kept distinct from the fractional `max_nav_pct_cap` (0.15 = 15%)?
- [ ] Does the blocked-with-no-failover outcome escalate (pause/rebalance) rather than force a transfer?

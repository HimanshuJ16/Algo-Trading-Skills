# Withdrawal Velocity & Anomaly Detection — Sign-off Checklist

## Threshold calibration (defaults are placeholders, not standards)
- [ ] **Measured before limiting**: 90 days of rolling 1h/24h USD totals plotted per account tier; caps set from that distribution, not from the shipped defaults.
- [ ] **Account caps** set above routine flow and below ruin; `account_hourly_limit_usd <= account_daily_limit_usd` (the engine refuses the inverse).
- [ ] **Global hot-wallet cap** derived from one-hour loss tolerance, *not* from the sum of per-account caps.
- [ ] **Hold period** exceeds the actual review SLA, not the aspirational one.
- [ ] **No threshold in this system is described to compliance or customers as a regulatory requirement.** No regulator sets these numbers.

## Clock and idempotency
- [ ] `evaluation_timestamp` comes from a trusted server clock; `WithdrawalRequest.timestamp` is never used to measure a window or an address age.
- [ ] Clock skew warnings are surfaced and alerted on, not discarded.
- [ ] Retries reuse the original `request_id`, so a gateway timeout replays the decision instead of scoring it twice.
- [ ] Rolling windows are verified closed at both ends — a future-dated entry does not leak into later windows.

## Destination address handling
- [ ] The allowlist record is **bound** to the request's `account_id` *and* `destination_address` before its age is trusted.
- [ ] `WHITELIST_RECORD_MISMATCH` is wired to an integration/tampering alert, and is never auto-released on elapsed time.
- [ ] The cooling period is sourced from the venue or firm policy, not assumed to be 24h (venue practice spans 12–72h).
- [ ] The venue's *own* whitelist is enabled — this gate does not constrain an attacker calling the custodian directly.

## Anomaly baseline
- [ ] `mu`/`sigma` computed point-in-time over a stated lookback, on a **lag**, excluding previously flagged amounts, so an attack cannot poison its own baseline.
- [ ] `min_profile_observations` satisfies the Grubbs bound for the chosen threshold — `(n-1)/sqrt(n) >= Z`; 3.0 needs `n >= 11`.
- [ ] Threshold calibrated against realised alert volume, acknowledging that withdrawal sizes are not Gaussian.
- [ ] `anomaly_zscore is None` renders as "did not run" everywhere, never as 0.0 or as a pass.
- [ ] Non-finite profile values raise rather than being scored.

## Circuit breaker and escalation
- [ ] The global breaker **latches**; a decayed rolling window does not silently re-arm it.
- [ ] `reset_hot_wallet_freeze` requires a named authoriser and the reset is logged with the prior freeze reason.
- [ ] `REJECTED_FREEZE` halts the automated signer queue and pages the SOC, in and outside office hours.
- [ ] The freeze-and-reset path has been rehearsed end to end in staging and timed.

## Held withdrawal accounting
- [ ] Every hold reaches an explicit exit: `release_held_withdrawal` or `cancel_held_withdrawal`.
- [ ] Released holds re-enter the velocity ledger, so a manual release consumes capacity.
- [ ] Cancelled holds cannot subsequently be released.
- [ ] Aged-out holds are monitored — a stuck hold is a stuck customer withdrawal.

## Operational integrity
- [ ] The velocity ledger is persisted and reloaded on startup; a restart does not zero every rolling window.
- [ ] The evaluate-then-submit sequence is serialised (the engine is not thread-safe).
- [ ] The USD price oracle is treated as a security dependency, and a stale or manipulated price cannot inflate available capacity.
- [ ] The equivalent server-side policy is configured at the custodian (Fireblocks TAP, BitGo wallet policy), and any disagreement between local and vendor limits has a documented authoritative side.

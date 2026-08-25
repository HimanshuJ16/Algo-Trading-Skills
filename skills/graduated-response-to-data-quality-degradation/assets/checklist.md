# Pre-Flight Checklist — Graduated Data-Quality De-Risking

## Inputs
- [ ] Is `stale_time_seconds` derived from the **source-side** tick timestamp, not local arrival time? (A replayed stale feed still ticks.)
- [ ] Is every metric this engine consumes actually measured? An unmeasured check leaves its penalty permanently at zero and silently inflates the score.
- [ ] Is the crossed-book check scoped to continuous trading, so auctions, halts and reopens do not de-risk every session?
- [ ] Is `bid_ask_spread_multiplier` relative to the measured normal spread for that instrument and session?
- [ ] Is there a watchdog on the collector itself? The engine cannot detect its own absence.

## Scoring and classification
- [ ] Is the quality score $Q \in [0, 100]$ deterministic and finite for every input, including `NaN` and infinity?
- [ ] Does a `NaN`, infinite, negative or wrongly-typed metric force **Tier 3** with `metrics_valid=False` — never Tier 0?
- [ ] Is the tier classified from the **exact** score, with only the reported score rounded (floored) for display?
- [ ] Tier 0 at $Q \ge 90$, Tier 1 at $70 \le Q < 90$, Tier 2 at $40 \le Q < 70$, Tier 3 at $Q < 40$, with lower bounds inclusive?

## Mandate integration
- [ ] Does the order path gate on `allow_new_entries` / `allow_risk_reducing_exits` / `cancel_resting_orders` / `flatten_positions` — **not** on `position_sizing_factor` alone?
- [ ] Is `position_sizing_factor` applied to new entries only? It is $0.0$ at both Tier 2 and Tier 3, and Tier 2 permits exits.
- [ ] Does Tier 3 cancel resting orders **before** attempting to flatten?
- [ ] Does the flatten path use a price collar from the last trusted mark rather than crossing an untrusted spread? (RTS 6 Art. 14(3): shut down "without creating disorderly trading conditions".)

## Anti-flap and state
- [ ] Is `recovery_hold_seconds` set from the observed duration of transient feed stalls, rather than left at the memoryless default of $0$?
- [ ] Does escalation still apply immediately while a de-escalation is being withheld?
- [ ] Are `instantaneous_tier` and `tier_held_by_recovery` logged, so an operator can tell a held tier from a live one?
- [ ] Is the engine confined to one process, or is the divergence between per-process recovery timers acceptable?

## Calibration and audit
- [ ] Have the thresholds and penalties been calibrated by replaying recorded feed telemetry, rather than copied from the defaults?
- [ ] Is it recorded that these numbers are engineering defaults with no regulatory basis, and that the score is not a compliance metric?
- [ ] Are `penalty_breakdown` and `triggered_conditions` persisted, so a post-incident review can identify which metric drove the halt?
- [ ] Is this gate paired with an independent P&L-driven kill switch, so data-quality and capital-loss failure axes are both covered?

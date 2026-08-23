# Pre-Flight Checklist

- [ ] Are primary fill event callbacks linked to real-time hedge order generators (one hedge order per fill event, not per completed order)?
- [ ] Does every primary fill carry a stable unique `fill_id`, and is a redelivered `fill_id` (gateway replay, FIX `PossResend`) rejected rather than hedged a second time?
- [ ] Is the hedge ratio expressed per primary unit INCLUDING the contract multiplier (e.g. 0.50 delta × 100 shares/contract = 50.0), and verified against adjusted/non-standard contracts?
- [ ] Is dynamic hedge ratio (Delta / Beta) updated continuously?
- [ ] Is dispatch latency captured via `mark_dispatched()` and monitored against the SLA (default 100 ms, calibrated per venue)?
- [ ] Is hedge fill latency ($\Delta t$) monitored against the same SLA?
- [ ] Are hedge partial fills accumulated (order stays pending until the cumulative fill reaches the target) with the residual exposure visible to risk?
- [ ] Is `enforce_unhedged_timeouts()` wired to a periodic timer so a never-filling hedge is force-unwound, with the unwind callback connected to the primary-leg cancel/flatten path?
- [ ] Does a *late fill* past the timeout route to the same unwind path as the timer sweep, so a late partial fill cannot take its residual exposure out of tracking?
- [ ] Are wrong-side fills, duplicate fills of completed orders, hedge quantities that round to zero, and NaN/zero quantities rejected before mutating hedge state?
- [ ] If the timeout sweep runs on its own thread, is hedge state synchronized against the fill-callback thread, and is the unwind handler called without holding that lock?
- [ ] Are aggressive repricing (on `SYNC_DELAY_BREACH`) and emergency unwinding (on `UNHEDGED_TIMEOUT_UNWIND`) handlers configured and tested?
- [ ] Has the timeout-unwind path been rehearsed (kill-switch style), including the callback-failure escalation path?

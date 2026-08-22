# Pre-Flight Checklist

- [ ] Is the correct **listing venue's** rule set selected (Nasdaq LOC 15:58 / MOC 15:55; NYSE 15:50, contra-side to a published imbalance until 16:00)?
- [ ] Are all timestamps timezone-aware and converted to `America/New_York` (no naive datetimes anywhere in the path)?
- [ ] Is the cutoff evaluated against the **intended submission time**, with a latency buffer covering feed lag, strategy time and broker hops?
- [ ] Is the system clock synchronized (PTP/NTP) and monitored for drift?
- [ ] Are non-positive or not-yet-disseminated near/far prices treated as *absent* rather than as a $0.00 limit price?
- [ ] Is every order limit-priced (LOC/IO), never an unpriced MOC on the imbalance side?
- [ ] Is the size acceptable as an **irrevocable** commitment, given the 15:50 cancel/modify freeze on both venues?
- [ ] Is participation capped both against the imbalance and against predicted auction volume (paired + imbalance)?
- [ ] Are non-tradable states handled: cross type ≠ `C`, imbalance direction `O` (insufficient orders) and `P` (paused)?
- [ ] Is the decision reason logged for every message, including the rejections?
- [ ] Is post-cross reconciliation in place against the official closing price, with unfilled quantity tracked as opportunity cost?

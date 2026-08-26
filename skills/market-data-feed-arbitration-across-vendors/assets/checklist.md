# Pre-Flight / Sign-off Checklist — market-data-feed-arbitration-across-vendors

Use this before considering the skill's implementation complete.

## Topology and inputs

- [ ] **Independent sources:** Confirm the two feeds are separate sources, not two lines of one stream. Identical A/B lines (e.g. CME MDP 3.0 Feed A / Feed B) are arbitrated by packet sequence number, not by price divergence.
- [ ] **Same price basis:** Confirm both vendors publish the same field (both last trade, or both quote midpoint) for every arbitrated symbol.
- [ ] **Single receipt clock:** Confirm timestamps are local receipt times from one clock, and that no vendor or exchange event timestamp is used for staleness.
- [ ] **Entitlements:** Confirm each vendor feed is licensed for the intended use.

## Validation

- [ ] **Non-finite prices:** Confirm `NaN` and `±inf` are rejected before entering state, not compared against the tolerance.
- [ ] **Non-positive prices:** Confirm zero and negative prices are rejected (they also collapse the midpoint denominator).
- [ ] **Unknown vendor:** Confirm an unrecognised vendor id raises instead of defaulting to a feed.
- [ ] **Out-of-order ticks:** Confirm a replayed tick cannot overwrite a newer observation for that vendor.

## Tolerance calibration

- [ ] **Tick-size floor:** Confirm the divergence tolerance is at least one minimum price increment expressed in percent for every instrument in the universe. (5 bps is narrower than one $0.01 tick below $20.)
- [ ] **Calibrated, not defaulted:** Confirm the tolerance was derived from recorded cross-vendor history for this vendor pair, not copied from the reference defaults.
- [ ] **Stale threshold vs liquidity:** Confirm `max_stale_seconds` exceeds the instrument's own quiet periods so an illiquid symbol is not permanently "stale".

## Behaviour under failure

- [ ] **Single-feed failover:** Confirm a stale vendor triggers failover and that the result is marked *not cross-verified*.
- [ ] **Total blackout:** Confirm a supervisor timer calls the health check below the stale threshold, and that both feeds going silent produces `NO_TRUSTED_FEED` with **no** price.
- [ ] **No last-known-value substitution:** Confirm the arbitrator never emits a stale cached price in place of "no price".
- [ ] **Quarantined survivor:** Confirm failover onto a quarantined feed is emitted untrusted rather than silently promoted.
- [ ] **Fast market:** Replay a real gap event (earnings, halt resumption) and confirm no vendor is quarantined while the second feed catches up inside the confirmation window.
- [ ] **Frozen feed:** Hold one vendor's price constant while the other moves and confirm the frozen vendor is quarantined on evidence.
- [ ] **Hysteresis:** Confirm a quarantine survives one clean comparison and releases only after the configured consecutive count.

## Downstream integration

- [ ] **Trust flag wired:** Confirm `is_trusted=False` actually gates order entry or sizing somewhere downstream — the arbitrator itself stops nothing.
- [ ] **Divergence never reported as zero:** Confirm dashboards distinguish "feeds agreed exactly" from "no comparison performed".
- [ ] **Transition-only alerting:** Confirm feed-state logging fires on transitions, not per tick, and that alert latency meets the firm's monitoring obligations (EU: RTS 6 Article 16, five seconds).
- [ ] **Session reset:** Confirm per-symbol state is reset at session boundaries.
- [ ] **Concurrency:** Confirm the arbitrator instance is safe for the threading model of the feed handlers that call it.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/market-data-feed-arbitration-across-vendors/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

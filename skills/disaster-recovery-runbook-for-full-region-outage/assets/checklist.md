# Pre-Flight Checklist

## Prepared before the incident
- [ ] Are failover DNS record TTLs set to 60–120s (and is the TTL counted inside the RTO budget)?
- [ ] Are client keepalive durations lowered so existing connections actually move?
- [ ] Are break-glass DR credentials and the five ARC cluster endpoints stored outside the primary region?
- [ ] Is failover driven by the routing-control data plane API with retries, not the console?
- [ ] Is `AuroraGlobalDBRPOLag` alarmed from the secondary, so lag is known before the decision?

## The two interlocks
- [ ] Is there positive evidence that primary-region writes are fenced — the fencing event observed, or applications confirmed offline?
- [ ] Are resting orders confirmed cancelled by **querying the venue**, not inferred from a dispatched cancel-all?
- [ ] Is it documented, per venue, what Cancel on Disconnect does *not* cover (GTC/GTD orders, graceful logouts)?

## The data-loss decision
- [ ] Is replication lag inside the RPO objective — and if not, is promotion an explicitly recorded `accept_data_loss` decision?
- [ ] Where several secondaries exist, was the one with the least lag chosen?
- [ ] Is there a plan to recover unreplicated writes from the point-of-failure snapshot afterwards?

## Configuration sanity
- [ ] Are the primary and secondary regions actually different?
- [ ] Are the RTO/RPO objectives your own, justified by business impact — not the module defaults copied unchanged?
- [ ] Does the runbook distinguish a *blocked* step from a *failed* one in its audit trail?

## Drills
- [ ] Are quarterly drills run, and after every topology change?
- [ ] Does each drill record where the sequence would have stopped, not just whether it "passed"?
- [ ] Was the drill's predicted RTO compared against real elapsed time, including TTL and connection draining?

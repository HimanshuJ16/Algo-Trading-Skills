# Pre-Flight Checklist — multi-region-failover-for-broker-connectivity

Complete before the failover path carries live order flow, and re-run after any change to
endpoints, credentials, entitlements or probe logic.

## Endpoints

- [ ] Every registered endpoint fronts **the same account** — verified against the
      broker, not assumed from the hostname.
- [ ] The backup's credentials, entitlements and supported order types have been
      confirmed by placing, amending and cancelling a real order through it.
- [ ] The broker's session-concurrency rule is known and written down. If it permits only
      one session per credential, the design accounts for that (second credential, or
      accepted logon latency inside the recovery time).
- [ ] Every backup has an explicit `priority`, and the ordering reflects a deliberate
      choice about latency, cost and shared failure domains.
- [ ] `validate_configuration()` is called at startup and its failure blocks the process.

## Health probing

- [ ] `health_check_fn` is supplied explicitly (the constructor has no default).
- [ ] The probe exercises authentication and the order path, not just reachability.
- [ ] A probe that raises has been tested and counts as a failure.
- [ ] Every endpoint is probed on the schedule, not only the active one.
- [ ] `max_health_age_seconds` comfortably exceeds the probe interval, and the margin was
      chosen rather than inherited.
- [ ] `failure_threshold` was derived from the primary's observed error distribution.

## Failover safety

- [ ] `require_fence=True`, or the exemption is documented against the broker's published
      single-session guarantee.
- [ ] A fence mechanism exists and has been exercised: process stop, credential
      revocation, session logout, or venue cancel-on-disconnect.
- [ ] The caller branches on `outcome`, not on whether an event object was returned.
- [ ] `requires_trading_halt` (`NO_TARGET_AVAILABLE`) is wired to an actual trading halt.
- [ ] `FailoverDecision.notes` reaches an alerting channel; a stale-health note is
      treated as a monitoring incident.
- [ ] Pre-trade risk controls sit above the path selector and apply identically on every
      endpoint.

## Reconciliation

- [ ] A reconciliation routine runs after **every** switch, before flow resumes.
- [ ] Open orders are re-read from the broker, never from the abandoned region's cache.
- [ ] Orders submitted without an acknowledgement are resolved explicitly rather than
      assumed lost.
- [ ] Position and balance state is re-established from the broker after the switch.

## Failback and flapping

- [ ] `failback_success_threshold` requires consecutive successes, not elapsed time.
- [ ] `cooldown_seconds` and the failback window were calibrated, not left at defaults.
- [ ] `FLAP_SUPPRESSED` escalates to a human and does not silently retry.
- [ ] Whether failback may occur during the trading session is an explicit decision.

## Drills (evidence required, not intent)

- [ ] Primary blocked at the network layer → failover fired, fence applied,
      reconciliation ran. Date: ____________
- [ ] Primary restored → failback waited for cooldown **and** stability. Date: __________
- [ ] Primary flapped deliberately → `FLAP_SUPPRESSED` escalated. Date: ____________
- [ ] Probe loop killed → staleness note raised, **no** failover occurred. Date: ________
- [ ] All endpoints failed simultaneously → trading halted. Date: ____________
- [ ] Measured end-to-end recovery time (connect + logon + re-subscribe + reconcile),
      not the engine's decision time: __________ seconds.

## Tests

- [ ] `python scripts/test_region_failover.py` — 100% pass rate.
- [ ] `python tools/validate_skills.py` passes.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Next drill due: ___________________________

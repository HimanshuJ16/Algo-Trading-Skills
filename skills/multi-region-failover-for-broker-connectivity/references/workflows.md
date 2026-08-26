# Deep Workflow Reference — multi-region-failover-for-broker-connectivity

## Scope

`scripts/region_failover.py` decides *whether* connectivity should move. It performs no
I/O. The surrounding system owns four things the engine deliberately does not:

| Owned by you | Why not the engine |
|---|---|
| The health probe | Only you know what "usable" means for your broker and order types |
| The fence | Stopping a process or revoking a credential is an action, not a decision |
| The switch | Reconnecting, re-authenticating and re-subscribing is transport work |
| Reconciliation | Only the broker knows what is resting; the engine has never spoken to it |

---

## Full procedure

### 1. Configure and gate at startup

```python
mgr = RegionFailoverManager(
    health_check_fn=probe_order_path,      # required — no default
    failure_threshold=3,
    cooldown_seconds=60.0,
    max_health_age_seconds=30.0,           # must exceed your probe interval
    failback_success_threshold=3,
    require_fence=True,
)
mgr.register_endpoint("east", "us-east-1", EAST_URL, is_primary=True)
mgr.register_endpoint("west", "us-west-2", WEST_URL, priority=10)
mgr.register_endpoint("eu",   "eu-west-1", EU_URL,   priority=50)
mgr.validate_configuration()               # fail loudly here, not silently at 09:30
```

`validate_configuration()` exists because the failure it catches is invisible at runtime:
a manager with no primary has no active endpoint and every evaluation is a no-op, which
looks exactly like a system with nothing wrong.

Priorities are explicit for the same reason — the region that is nearest, the region that
is cheapest and the region that shares a failure domain with the primary are three
different choices, and none of them should be decided by dictionary ordering.

### 2. Probe on a schedule

Probe **every** endpoint, not only the active one. A backup is only a failover target
while it has a successful probe inside `max_health_age_seconds`; a backup you stop
probing silently stops being a backup.

Design the probe to exercise what you actually need:

| Probe | Proves | Misses |
|---|---|---|
| TCP connect / ping | The host is routable | Auth, entitlements, order acceptance |
| Vendor status page | The vendor thinks it is up | Your credential, your network path |
| Authenticated read (balance, open orders) | Credential and path work | Order-entry capacity |
| Session heartbeat on the order path | The order path is live | Matching-engine acceptance |

A probe that raises is a failed probe — `probe_health()` catches `Exception`, counts it,
and records it in `last_probe_error`. `BaseException` still propagates so an operator
interrupt is never swallowed.

### 3. Evaluate, and branch on the outcome

```python
decision = mgr.evaluate_failover()

if decision.outcome is FailoverOutcome.NO_ACTION:
    pass                                    # keep trading
elif decision.outcome is FailoverOutcome.FENCE_REQUIRED:
    fence(mgr.get_active_endpoint())        # stop the process / revoke / close session
    decision = mgr.evaluate_failover(fence_confirmed=True)
elif decision.outcome is FailoverOutcome.NO_TARGET_AVAILABLE:
    halt_trading(decision.reason)           # nowhere to go — this is not "nothing to do"
```

`NO_ACTION` and `NO_TARGET_AVAILABLE` are the same non-event to a caller checking whether
an event object came back, and they call for opposite responses. Read `outcome`.

`decision.notes` carries staleness warnings. Surface them: a stalled probe loop freezes
the last state at `HEALTHY`, and the engine will not fail over on that, by design — a
monitor that died is not an endpoint that died, and failing over on monitor failure turns
a monitoring bug into a trading outage.

### 4. Fence, then switch

The fence exists because a `DOWN` verdict means *this monitor* cannot reach the endpoint.
A partition between the monitor and the primary region produces exactly that verdict
while the primary's trading process continues submitting orders happily. Promoting the
backup beside it puts two live paths in front of one account.

Acceptable fences, strongest first:

1. The trading process in the outgoing region is confirmed stopped.
2. Its credential or API key is revoked at the broker.
3. The session is logged out, or the venue's cancel-on-disconnect has fired.
4. The broker enforces single-session-per-credential, so the new connection provably
   displaces the old (see `references/standards.md` §2). This is the case, and the only
   routine case, for `require_fence=False`.

"The health check failed" is **not** a fence.

### 5. Reconcile before resuming flow

Non-negotiable, and outside this module. Before the strategy sends anything from the new
region:

- Query the broker for orders resting under this account.
- Match them against what the abandoned region believed it had working.
- Resolve anything the abandoned region submitted without receiving an acknowledgement —
  a 5xx or a read timeout leaves execution status genuinely unknown.
- Re-establish market data and position state from the broker, not from the old region's
  cached copy.

### 6. Failback, gated three ways

```python
decision = mgr.evaluate_failback()          # only meaningful while off the primary
```

| Outcome | Meaning | Response |
|---|---|---|
| `NO_ACTION` | Already on the primary, or it has not recovered | Continue |
| `COOLDOWN_ACTIVE` | Too soon after the last switch | Continue |
| `NOT_STABLE_YET` | Primary healthy, but not for enough consecutive probes | Continue |
| `FLAP_SUPPRESSED` | Failback budget exhausted; the primary is oscillating | Escalate to an operator |
| `FENCE_REQUIRED` | Ready, pending evidence the backup is quiesced | Fence, re-evaluate |
| `SWITCHED` | Flow returned to the primary | Reconcile, then resume |

Failback is a second unplanned switch, at a time nobody chose, onto infrastructure that
just failed. Nothing forces it to happen during the session at all — deferring it to a
maintenance window is a legitimate choice, and the engine never initiates it on its own.

**The asymmetry is intentional:** failover is not gated by cooldown and is never
suppressed by the flap limiter, because refusing an involuntary switch leaves order flow
pinned to a dead path. Failback is gated by both, because refusing a voluntary switch
only costs you the preferred path.

### 7. Drill it

The whole mechanism is untested until it has been exercised. At minimum, per quarter and
after any change to endpoints, credentials or entitlements:

- Block the primary at the network layer and confirm the failover fires, the fence is
  applied, and reconciliation runs.
- Confirm the backup can actually place, amend and cancel a live order — the probe
  proving reachability does not prove that.
- Restore the primary and confirm failback waits for cooldown *and* stability.
- Flap the primary deliberately and confirm `FLAP_SUPPRESSED` escalates rather than
  looping.
- Kill the probe loop and confirm the staleness note appears and no failover occurs.

See `chaos-engineering-for-trading-infrastructure` for running these as scheduled
exercises rather than one-off tests.

---

## Failure modes this design accepts

- **Both regions partitioned from the monitor** — no probe succeeds, outcome is
  `NO_TARGET_AVAILABLE`, and the correct response is to halt rather than guess.
- **A backup that is reachable but not tradeable** — the engine believes your probe. Make
  the probe strong enough to catch this.
- **Recovery time** — connect, logon and re-subscription happen outside this module and
  usually dominate. Measure them in a drill; do not infer them from the decision latency.

## Production implementation reference

- Code: `scripts/region_failover.py` (`RegionFailoverManager`).
- Tests: `scripts/test_region_failover.py`.

---
name: multi-region-failover-for-broker-connectivity
description: >-
  Use when a bot reaches its broker over more than one path and something must decide
  unattended which path carries orders; refuses to fail over onto a path nobody has
  probed. Failing over to a different broker account is
  broker-failover-secondary-account-routing.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment, failover, high-availability, multi-region, broker-connectivity, split-brain-prevention, flap-suppression
  brokers_frameworks: "Binance Spot REST API (multi base endpoint); IBKR TWS API / Client Portal Web API (single-session); AWS / GCP / Azure multi-region; MiFID II RTS 6 (Reg (EU) 2017/589); SEC Rule 15c3-5"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a trading system reaches its broker over more than one path — two
cloud regions, two base endpoints of the same API, a leased line with an internet
fallback — and something has to decide, unattended, which path is carrying order flow
right now.

The decision looks like a circuit breaker and is not one. A circuit breaker guards
idempotent reads, where the cost of being wrong is a retry. Here the two error cases are
asymmetric and both expensive:

> **Failing over too late** leaves flow pinned to a dead path with positions open.
> **Failing over too early** puts two live paths in front of one account.

`scripts/region_failover.py` is a decision engine. It opens no sockets, resolves no DNS
and sends no orders: you supply the health probe and perform the switch, it decides
whether a switch is warranted and refuses the ones that are not evidenced.

## When NOT to Use

- **When your broker permits only one session per credential.** IBKR's Web API
  documentation states that "only a single active brokerage session can exist for any
  username across all IBKR services", and its TWS API documentation that "it is not
  possible to login to multiple trading applications simultaneously with the same
  username". On such a broker there is no warm standby to fail over *to* — the second
  region's login is what kills the first. The mechanism is then session competition, not
  health-probed failover; see `references/standards.md` before designing around it.
- **As reconciliation.** Moving the network path does nothing to the orders that were in
  flight when it broke. Binance's own guidance on 5xx is that "the execution status is
  UNKNOWN and could have been a success" — an order sent from the region you just
  abandoned may be resting, filled, or never received. Resolve it with the broker's order
  state before resuming flow; see `order-placement-idempotency`.
- **As failover between broker *accounts*.** Positions do not net across accounts, so
  sending a closing order to a different account opens a second position instead of
  flattening the first. That is a different skill —
  `broker-failover-secondary-account-routing`.
- **As failover between order-entry sessions at one venue** (FIX Active/Standby, iLink
  fault-tolerant groups) — `exchange-gateway-redundancy-and-failover-testing`, which owns
  sequence-number resolution and `PossResend`.
- **As a full-region disaster recovery runbook.** Database promotion, RPO acceptance and
  DNS switchover are `disaster-recovery-runbook-for-full-region-outage`. This module
  moves connectivity only.
- **As a risk control.** Pre-trade limits sit above the path selector and must apply
  identically on every path. A backup endpoint is another market-access path, not an
  exemption — `sec-rule-15c3-5-risk-controls-us`.

## Prerequisites

- **Two or more endpoints that front the same account.** Verify this rather than assume
  it: a regional hostname may front a different legal entity, a different account, or a
  different entitlement set. An endpoint you cannot trade your positions through is not a
  backup.
- **A health probe that exercises the path you actually trade over.** A TCP connect or a
  200 from a status page proves neither authentication nor order acceptance. The probe is
  supplied by you; the engine only counts its results.
- **A fence** — a way to make the outgoing path unable to submit orders (stop the
  process, revoke its credential, close the session, rely on venue cancel-on-disconnect).
  Required by default; see step 3.
- **A calibrated `max_health_age_seconds`** comfortably larger than your probe interval.
  The engine cannot see your scheduler and cannot validate this for you.
- **A reconciliation path** that answers "what is resting at the broker right now",
  callable from either region.

## Workflow

1. **Register endpoints, then gate the configuration at startup.**
   Give every backup an explicit `priority` (lowest first) — the nearest region and the
   cheapest region are not the same choice, and leaving it implicit means the order is
   whatever the dictionary happened to hold. Call `validate_configuration()` before the
   probe loop starts: a config with no primary, or with nothing to fail over to, never
   errors at runtime, it just silently never acts, which is indistinguishable from a
   healthy system.

2. **Probe, and treat a raising probe as a failure.**
   Connection refused, DNS failure and read timeout arrive as *exceptions*, not as
   `False`. `probe_health()` catches them and counts them; an exception allowed to escape
   would leave the failure counter untouched and the endpoint frozen at its last known
   state — the endpoint dies and is never marked DOWN.
   - **Decision point — an endpoint reaches DOWN only at `failure_threshold` consecutive
     failures.** Below it the state is DEGRADED and no failover is warranted. One failed
     probe is noise; abandoning a working path on noise is its own outage.

3. **Before flow moves, fence the path it is moving off.**
   A DOWN verdict means *this monitor* cannot reach the endpoint. It does not mean the
   trading process in that region cannot reach the broker — a partition between monitor
   and region produces exactly that split. `evaluate_failover()` returns
   `FENCE_REQUIRED` and changes nothing until the caller re-evaluates with
   `fence_confirmed=True`.
   - **Decision point — set `require_fence=False` only when the broker fences for you**,
     i.e. it enforces one session per credential so that connecting from the new region
     provably disconnects the old. That is a property of your broker, verified in its
     documentation, not a default to assume.

4. **Select a target that has actually answered recently.**
   Eligibility is *fresh* `HEALTHY` — a successful probe inside `max_health_age_seconds`
   — not "not known to be broken". A registered-but-never-probed endpoint is `UNKNOWN`
   and is never a target.
   - **Decision point — read `outcome`, not the presence of an event.** `NO_ACTION`
     ("the active path is fine") and `NO_TARGET_AVAILABLE` ("the active path is dead and
     there is nowhere to go") are the same non-event to a caller checking for `None`, and
     they demand opposite responses. `requires_trading_halt` is true for exactly one of
     them.

5. **Reconcile before resuming flow, every time.**
   The switch is connectivity only. Orders in flight at the abandoned endpoint are
   unresolved by definition and must be settled against the broker's own view of the book
   before the strategy sends anything from the new region.

6. **Treat failback as a second unplanned outage, because it is.**
   `evaluate_failback()` is gated three ways: cooldown elapsed, the primary stable across
   `failback_success_threshold` *consecutive* probes, and the flap limiter untripped.
   - **Decision point — elapsed time is not recovery evidence.** A single successful
     probe after a 60-second cooldown is one packet. A primary that alternates up and
     down never accumulates consecutive successes, which is the point.
   - **Decision point — failover is never gated by cooldown or the flap limiter; failback
     always is.** The asymmetry is deliberate: suppressing a voluntary switch costs you
     the preferred path, suppressing an involuntary one leaves flow on a dead path.
   - `FLAP_SUPPRESSED` is an escalation, not a resting state. It means the primary is
     oscillating and a human should look at it.

7. **Do not auto-fail-over on stale health.**
   A stalled probe loop leaves the last state frozen at `HEALTHY`, which reads as health
   to anything checking `state` alone. The engine reports staleness in
   `FailoverDecision.notes` and takes no action on it: the monitor dying is not evidence
   that the endpoint died, and failing over on monitor failure is how a working system
   gets switched off.

> Full procedure: see `references/workflows.md`.
> Standards and sourcing: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing over onto an endpoint nobody has ever probed.** If endpoints default to
  `HEALTHY` and eligibility means "state is HEALTHY", the backup you configured last
  quarter and never exercised is a valid target — and the failover moves live order flow
  onto a path whose credentials, entitlements and reachability have never been confirmed.
  This engine defaults new endpoints to `UNKNOWN` for that reason.
- **Letting the health probe raise.** The realistic failure of a network probe is an
  exception, not a `False` return. If it escapes the probe function, the failure counter
  never increments, the endpoint never reaches DOWN, and the failover machinery sits idle
  through the entire outage.
- **Constructing the manager with a default "always healthy" probe.** It reports a
  permanently healthy system, never fails over, and passes every smoke test. This engine
  requires `health_check_fn` rather than defaulting it.
- **Collapsing "nothing to do" and "nowhere to go" into one return value.** Both are the
  absence of a failover event; one means keep trading, the other means halt.
- **Measuring cooldown on the wall clock.** `time.time()` steps when NTP corrects it. A
  forward step releases the failback gate early; a backward step can hold it shut far
  longer than intended. All interval arithmetic here uses `time.monotonic()`.
- **Treating a health-probe timeout as proof the old region is harmless.** It proves the
  monitor cannot reach it. Both regions trading one account is the split-brain case, and
  the fence is what prevents it.
- **Believing the switch resolved in-flight orders.** It resolved nothing. A 5xx or a
  read timeout leaves execution status unknown, and the new region has no way to
  recognise an order the old one submitted.
- **Assuming backup endpoints are equal-quality.** Binance documents its `api1`-`api4`
  base endpoints as endpoints that "should give better performance but have less
  stability" — the vendor's own alternatives differ in stability. Size and validate the
  backup under realistic load before relying on it.
- **Failing back on one good probe.** Cooldown limits how *often* the system switches;
  it says nothing about whether the primary recovered. Without a consecutive-success
  requirement and a flap limiter, an intermittently failing primary produces an endless
  switch loop, each cycle abandoning in-flight orders.

## Verification

- Build a manager with `failure_threshold=2`, register a primary and an unprobed backup,
  and drive the primary DOWN: `evaluate_failover()` must return
  `FailoverOutcome.NO_TARGET_AVAILABLE` with `requires_trading_halt` true, and
  `active_endpoint` must still be the primary. Probe the backup once, then re-evaluate:
  the outcome becomes `SWITCHED`.
- With `require_fence=True`, the same sequence must return `FENCE_REQUIRED` and leave
  `failover_history` empty until `evaluate_failover(fence_confirmed=True)` is called.
- Point the health probe at a function that raises `ConnectionResetError`: two probes
  must take the endpoint to `DEGRADED` then `DOWN`, and `last_probe_error` must name the
  exception. A `KeyboardInterrupt` must still propagate.
- With `cooldown_seconds=600` and a recovered primary, patch `time.time()` forward by a
  day: the outcome must remain `COOLDOWN_ACTIVE`, proving the gate is on the monotonic
  clock.
- With `failback_success_threshold=3`, alternate the primary success/success/failure
  repeatedly: no cycle may produce `SWITCHED`.
- Negative checks that must raise: a missing or non-callable `health_check_fn`,
  `failure_threshold < 1`, a negative cooldown, a blank endpoint field, a duplicate
  endpoint name, a second primary, `validate_configuration()` with no primary or a single
  endpoint, and any evaluation with no active endpoint registered.
- Run `python -m unittest discover -s skills/multi-region-failover-for-broker-connectivity/scripts` and confirm 100% pass rate.

## Related Skills

- `broker-failover-secondary-account-routing`
- `exchange-gateway-redundancy-and-failover-testing`
- `disaster-recovery-runbook-for-full-region-outage`
- `order-placement-idempotency`
- `smart-order-router-failover-on-venue-outage`
- `multi-region-active-active-tick-ingestion`
- `websocket-subscription-reconciliation-after-reconnect`
- `blue-green-deployment-for-live-strategy-updates`
- `chaos-engineering-for-trading-infrastructure`
- `sec-rule-15c3-5-risk-controls-us`

---
name: chaos-engineering-for-trading-infrastructure
description: >-
  Use when failover, timeout and gap-recovery paths have never actually run under fault;
  injects reproducible latency, message loss and simulated process death at an I/O
  boundary, behind an activation gate and never near live capital.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, chaos-engineering, resilience, fault-injection, failover, grey-failure
  brokers_frameworks: ""
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a trading system has recovery paths that have never been
executed under fault: a secondary FIX session that has never taken over, a
sequence-gap recovery that has only ever run in a unit test, a heartbeat timeout
nobody has watched fire. Network jitter, dropped messages and process death are not
hypothetical in production — the question is only whether the first real occurrence
is also the first *observed* occurrence.

`ChaosInjector` wraps a callable that stands in for a network or IPC boundary (a FIX
send, a REST call, a websocket read, a queue publish) and injects three fault classes
against it:

1. **Added latency and jitter** — the "grey failure" case: the connection stays open
   and keeps answering, just far too slowly. This is the fault that bypasses TCP
   disconnect handling entirely and is usually more dangerous than a hard failure.
2. **Message loss** (`ConnectionAbortedError`) — exercises reconnect logic, sequence
   gap-fill, and the decision about whether an unacknowledged order was actually sent.
3. **Simulated process death** (`SimulatedProcessCrash`) — exercises heartbeat
   detection, circuit breakers, and whatever is supposed to flatten or halt when a
   dependency disappears.

Use it in CI, against an isolated integration environment, as a standing regression
suite: the value comes from experiments running on every build, not from a quarterly
exercise.

## When NOT to Use

- **Against anything that can reach a live gateway or real capital.** This tool
  creates faults; it does not contain them. The activation gate is a last-resort
  backstop, not a substitute for environment isolation. For firms in scope of MiFID II
  RTS 6, testing that does not affect the production environment is a regulatory
  requirement, not a preference — see `references/standards.md`.
- **Before the recovery path exists.** Chaos engineering *validates* a hypothesis
  about recovery. If there is no failover, no heartbeat timeout and no kill switch,
  the experiment will simply confirm that; write the control first.
- **For network-layer realism.** This is an application-level wrapper. Kernel
  buffering, TCP retransmission, half-open sockets, and true network partitions need
  `tc`/`netem`, a proxy such as toxiproxy, or venue-provided test facilities.
- **For sub-millisecond latency work.** `time.sleep()` resolution is OS-dependent
  (roughly 1-2 ms on Windows, finer on Linux). Latency targets below ~1 ms are not
  meaningful here; see `colocation-latency-budget-accounting`.
- **As a load or capacity test.** Injecting faults is not the same as injecting
  volume. RTS 6 Article 10 stress testing is a separate exercise — see
  `load-testing-before-scaling-to-new-instrument-universe`.

## Prerequisites

- An environment that is separated from production and cannot route to a live venue:
  distinct credentials, distinct endpoints, distinct accounts.
- A **written, measurable steady state** to compare against — "the order gateway
  sustains 100 orders/sec at p99 < 5 ms and zero unacknowledged orders" — captured
  *before* the experiment starts. Without it the experiment has no verdict.
- Observability with resolution finer than the faults being injected. Injecting 100 ms
  of latency into a system whose metrics are 1-minute averages tells you nothing.
- A working kill switch and pre-trade risk layer, independent of the system under
  test (`kill-switch-and-drawdown-circuit-breakers`).
- Somewhere to record the experiment: hypothesis, seed, fault profile, outcome. The
  seed is what makes a failure debuggable.
- An agreed abort condition and the ability to stop the experiment immediately.

## Workflow

1. **Write the steady state and the hypothesis first, as a falsifiable statement.**
   "If the feed handler stops responding, the trading engine detects the missing
   heartbeat within 3 s, cancels working orders through the OMS, and enters HALTED."
   A hypothesis you cannot fail is not an experiment.
2. **Bound the blast radius before configuring the fault.** Confirm the target
   environment cannot reach a live gateway. Then construct the injector explicitly:
   `ChaosInjector(config, enabled=True, name="fix-session-a")` in a test harness, or
   leave `enabled=None` and let CI set `CHAOS_ENGINEERING_ENABLED`. Left alone, the
   injector is inert and passes calls straight through.
3. **Always set a seed.** `ChaosConfig(..., seed=20260821)`. An unreproducible failure
   in a chaos run costs more time than the run saved. The injector logs a warning when
   a probabilistic profile has no seed.
4. **Start with the grey failure, not the crash.** `latency_ms=50, jitter_ms=150`
   models a 50-200 ms degraded link. A connection that answers slowly bypasses
   disconnect handling completely, so it reaches code paths a hard kill never touches.
5. **Escalate one fault class at a time.** Latency, then loss, then death. Injecting
   all three at once produces a failure you cannot attribute.
6. **Set `latency_ms` deliberately relative to the client's own timeout.** A drop
   raises *after* the configured delay, so a latency above the client timeout is what
   exercises the timeout branch; a latency below it exercises the slow-but-successful
   branch. These are different tests with different bugs.
7. **Treat an unacknowledged order as ambiguous, never as unsent.** When a send is
   dropped, the correct recovery is to query order state by client order ID, not to
   resubmit — see `order-placement-idempotency`. An experiment that produces a
   duplicate order has found a real defect, not a harness artefact.
8. **Check `injector.stats` before believing the result.** `faults_injected == 0`
   means the run proved nothing: a 10% drop rate over 20 calls injects no drop about
   12% of the time. Assert on the counters, not just on the absence of exceptions.
9. **Compare against the hypothesis and record the verdict**, including the seed and
   fault profile, whether it passed or failed. A passing experiment is evidence for
   the annual review; a failing one is a defect ticket with a reproducer attached.
10. **Automate it into CI and re-run it after every material change** to the recovery
    path, the broker adapter, or the venue's session configuration.

> Full procedure, including environment topology and verdict criteria: see
> `references/workflows.md`.
> Regulatory touchpoints (EU/UK RTS 6 Articles 7, 10, 14) and engineering standards,
> with their jurisdictional limits: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Running the experiment where it can reach real capital.** The discipline's own
  canonical guidance ("Chaos strongly prefers to experiment directly on production
  traffic") was written for consumer web services whose worst case is a failed page
  load. In trading the worst case is an unmanaged open position, and for firms in
  scope of RTS 6 the regulation resolves the tension explicitly: tests must not affect
  the production environment.
- **Testing only hard crashes.** Process death is the *easy* failure — the socket
  closes and every handler notices. The expensive failure is the connection that
  stays open and responds in 30 seconds, because it defeats disconnect handling and
  quietly fills queues until something upstream blocks.
- **Reading "no exception" as "resilient".** With a 10% drop rate and a short run, the
  most likely outcome is that nothing was injected at all. Assert on
  `injector.stats.faults_injected`.
- **Re-seeding the process RNG to get determinism.** Calling `random.seed()` to make a
  chaos run reproducible re-seeds the generator the *system under test* uses for retry
  backoff and jitter, so every client retries in lockstep and the experiment measures
  an artefact of its own instrumentation. This injector seeds only generators it owns.
- **Simulating a crash with `SystemExit`.** Raised inside a worker thread it is
  swallowed silently by `threading` — no traceback, no failed test, a green run that
  injected a crash nobody saw. Reaching the interpreter, it looks like a clean
  shutdown in the logs. `SimulatedProcessCrash` derives from `BaseException` so it
  still bypasses `except Exception`, but it is reported and attributable.
- **Resubmitting a dropped order.** A dropped send means the outcome is unknown, not
  that the order was not sent. Retrying without an idempotent client order ID is how a
  chaos experiment creates a real duplicate position.
- **Running chaos experiments during a deployment freeze or a market event.** See
  `deployment-freeze-windows-around-market-events`.
- **Quarterly manual exercises.** An experiment that runs once a quarter validates a
  system that no longer exists. Automate it.

## Verification

Run the unit suite:

```
python -m unittest discover -s skills/chaos-engineering-for-trading-infrastructure/scripts
```

Then confirm the two safety properties by hand:

```python
from chaos_monkey_trading_simulator import ChaosConfig, ChaosInjector, MockFixClient

client = MockFixClient()

# 1. Fail-closed: with CHAOS_ENGINEERING_ENABLED unset, the wrapper is transparent.
inert = ChaosInjector(ChaosConfig(latency_ms=100, drop_probability=1.0))
assert inert.execute(client.send_order, "ORD-1") == "ACK-ORD-1"
assert inert.stats.faults_injected == 0

# 2. Enabled: 100 ms of latency and a 10% drop rate, reproducibly.
chaos = ChaosInjector(
    ChaosConfig(latency_ms=100, drop_probability=0.10, seed=20260821),
    enabled=True, name="fix-session-a")
delivered = 0
for i in range(100):
    try:
        chaos.execute(client.send_order, f"ORD-{i}")
        delivered += 1
    except ConnectionAbortedError:
        pass  # the consumer's gap-recovery path belongs here
print(chaos.stats, delivered)
```

The run must show a non-zero `drops_injected`, `total_delay_ms` of at least
`100 * calls`, and identical results on a second run with the same seed.

**Migration from v1 (breaking):** a simulated crash now raises `SimulatedProcessCrash`
instead of `SystemExit`; injection requires `enabled=True` or
`CHAOS_ENGINEERING_ENABLED` in the environment; `ChaosConfig` validates its arguments
and no longer touches the global `random` module.

## Related Skills

- `circuit-breaker-for-downstream-service-calls`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `sequence-number-gap-detection-for-feeds`
- `websocket-reconnection-with-state-recovery`
- `exchange-gateway-redundancy-and-failover-testing`
- `disaster-recovery-runbook-for-full-region-outage`
- `graceful-degradation-priority-during-partial-outage`
- `position-limit-breach-simulation-fire-drills`
- `load-testing-before-scaling-to-new-instrument-universe`
- `feed-handler-canary-deployment`
- `deployment-freeze-windows-around-market-events`

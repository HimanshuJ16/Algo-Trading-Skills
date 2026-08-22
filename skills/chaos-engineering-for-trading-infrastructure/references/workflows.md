# Workflows for Chaos Engineering Against Trading Infrastructure

## 1. Environment setup

Stand up the trading engine, market-data feed handler and OMS in an isolated network
(for example, a Docker compose network in CI). The environment must be incapable of
reaching a live venue — separate credentials, separate endpoints, separate accounts, no
route to production. For EU/UK firms in scope of RTS 6 this separation is an obligation,
not a convenience; see `references/standards.md` §1.

Record the environment's deviations from production (host count, data volume, network
topology). They bound what the experiment can prove.

## 2. Define steady state, then the hypothesis

Steady state is a **measured output**, captured before the experiment starts and
expressed with a tolerance:

> Order gateway: 100 orders/sec sustained, p99 ack latency < 5 ms, zero unacknowledged
> orders older than 2 s, engine state `ACTIVE`.

The hypothesis is a falsifiable statement about that steady state under a named fault:

> If the feed handler stops responding, the trading engine detects the missing heartbeat
> within 3 s, cancels all working orders via the OMS, and enters `HALTED`.

Write down the abort condition at the same time — what observation ends the experiment
early, and who can call it.

## 3. Configure the fault profile

```python
from chaos_monkey_trading_simulator import ChaosConfig, ChaosInjector

config = ChaosConfig(
    latency_ms=50,      # degraded link floor
    jitter_ms=150,      # uniform [0, 150) added on top -> 50-200 ms
    drop_probability=0.10,
    crash_probability=0.0,
    seed=20260821,      # record this with the experiment
)
injector = ChaosInjector(config, enabled=True, name="fix-session-a")
```

Rules that make the run interpretable:

- **One fault class at a time.** Latency first, then loss, then process death.
  Simultaneous faults produce failures you cannot attribute.
- **Always seed**, and record the seed alongside the hypothesis.
- **Set latency relative to the client's own timeout.** A dropped call raises *after*
  the configured delay, so `latency_ms` above the client timeout exercises the timeout
  branch and below it exercises the slow-but-successful branch. Run both.
- **Leave `enabled` unset outside a test harness.** With `enabled=None` the injector
  activates only when `CHAOS_ENGINEERING_ENABLED` is set in the environment, so a
  wrapper that escapes into a shipped code path is inert and transparent.

## 4. Execute

The CI pipeline runs the experiment against the isolated environment. Faults are applied
at the boundary under test:

- **Latency / grey failure** — wrap the FIX send or feed read in `injector.execute()`.
- **Message loss** — the same wrapper raises `ConnectionAbortedError`; the consumer's
  reconnect and sequence gap-fill paths must handle it.
- **Process death** — `crash_probability` raises `SimulatedProcessCrash`, or, for a
  container-level experiment, send `SIGKILL` to the target container. The in-process
  injector models the *caller's* view of a dependency dying; a container kill models the
  dependency actually dying. They surface different bugs; the container kill is the
  stronger test where the harness supports it.

## 5. Validate

After the fault window, query the system and compare against the hypothesis:

| Observation | Verdict |
|---|---|
| State `HALTED`, working orders 0, within the stated detection budget | PASS |
| State `ACTIVE` or working orders > 0 after the budget | FAIL — resilience defect |
| Recovery happened, but outside the stated time budget | FAIL — the budget is part of the hypothesis |
| Duplicate orders after a dropped send | FAIL — an ambiguous send was treated as unsent; see `order-placement-idempotency` |
| `injector.stats.faults_injected == 0` | INCONCLUSIVE — nothing was injected; re-run with a higher rate or more calls |

Check `injector.stats` **before** reading the verdict. A green run that injected nothing
is the most common false pass in chaos engineering.

## 6. Record and teardown

Record: hypothesis, environment, fault profile, seed, `stats` snapshot, observed
behaviour, verdict, and any defect raised. This record is what makes an annual business
continuity review (RTS 6 Art. 14(4), where applicable) evidenced rather than asserted.

Restore the environment to baseline, confirm no residual positions or working orders in
the test account, and confirm the injector is disabled in every artefact leaving CI.

## 7. Failure follow-up

A failed experiment is a defect with a reproducer attached. Replay it with the same seed;
because each fault channel has its own stream, you can set `crash_probability=0.0` on the
replay and still get the identical sequence of drops, which usually isolates the trigger
in one pass. Add the reproducing configuration to the regression suite before fixing the
defect, so the fix is demonstrated rather than assumed.

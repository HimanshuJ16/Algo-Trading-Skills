# Chaos Experiment Pre-Flight Checklist

Complete before every experiment. An unchecked box in "Blast radius" is a stop.

## Blast radius (stop conditions)

- [ ] The target environment is separated from production and **cannot route to a live
      venue** — distinct credentials, distinct endpoints, distinct accounts.
- [ ] No real capital, real positions or real client orders are reachable from the
      environment under test.
- [ ] The kill switch and pre-trade risk controls are working and are **not** the subject
      of a live-path experiment.
- [ ] The experiment is not scheduled inside a deployment freeze window or around a
      market event.
- [ ] An abort condition is written down, and a named person can stop the run.
- [ ] The injector is fail-closed everywhere else: no artefact leaving CI has
      `enabled=True` hard-coded or `CHAOS_ENGINEERING_ENABLED` set.

## Experiment design

- [ ] Steady state is written as a **measured** output with a tolerance, captured before
      the run.
- [ ] The hypothesis is falsifiable and names both the fault and the recovery budget.
- [ ] Exactly one fault class is being varied.
- [ ] The fault profile is seeded, and the seed is recorded with the hypothesis.
- [ ] The run is long enough that the configured fault rate is expected to fire several
      times (a 10% drop rate over 20 calls injects nothing about 12% of the time).
- [ ] `latency_ms` has been set deliberately relative to the client's own timeout — both
      the above-timeout and below-timeout cases are covered by separate runs.

## Fault coverage

- [ ] **Grey failure** tested: a connection that stays open and answers very slowly, not
      only a hard kill. This is the case that bypasses TCP disconnect handling.
- [ ] **Message loss** tested, and the consumer recovers missing sequence numbers rather
      than silently continuing.
- [ ] **Process death** tested, and it is *reported* — a simulated crash that a worker
      thread swallows silently proves nothing.
- [ ] The ambiguous-send case is covered: after a dropped order send, the system queries
      order state by client order ID and does **not** blindly resubmit.
- [ ] Backpressure under sustained added latency does not block the main loop or grow a
      queue without bound.

## Verdict and evidence

- [ ] `injector.stats.faults_injected > 0` — otherwise the run is inconclusive, not a
      pass.
- [ ] Recovery is compared against the stated time budget, not merely "it recovered".
- [ ] No duplicate orders, no orphaned working orders, no residual positions in the test
      account after teardown.
- [ ] Hypothesis, environment, fault profile, seed, stats snapshot and verdict are
      recorded and retained.
- [ ] The experiment runs in CI on every build, not on a quarterly manual schedule.
- [ ] Any failure has a reproducing configuration added to the regression suite before
      the fix lands.

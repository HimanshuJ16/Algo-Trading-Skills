# Pre-Flight Checklist — Downstream-Service Circuit Breaker

## Scope

- [ ] The wrapped call is **not** order submission, order cancellation, a kill switch, or
      a mandatory pre-trade risk check.
- [ ] There is one breaker per independently failing resource — not one shared across
      venues, shards or accounts.
- [ ] The breaker instance lives for the process lifetime, not per call.

## Configuration

- [ ] Every wrapped client call has an explicit connect **and** read timeout.
- [ ] `slow_call_duration_sec`, if set, is below the client timeout.
- [ ] `expected_exceptions` names infrastructure faults only — no `Exception`, no
      `OSError`, no `requests.RequestException`.
- [ ] The exception tuple was checked against the *actual* client library
      (`requests.Timeout` is not `TimeoutError`).
- [ ] `failure_threshold` was chosen against the call rate; `failure_window_sec` is set if
      the call is infrequent.
- [ ] `recovery_timeout_sec` reflects the dependency's realistic recovery time, and the
      escalated ceiling is a duration you are willing to stay degraded through.
- [ ] `jitter_ratio` is non-zero if more than a handful of processes share the dependency.
- [ ] Recovery timing uses a monotonic clock (the default), not a wall clock.

## Behaviour

- [ ] The circuit opens after the configured number of **consecutive** failures.
- [ ] A success in CLOSED resets the failure count.
- [ ] Business-logic exceptions propagate without touching the failure counter.
- [ ] When OPEN, the wrapped callable is provably not invoked (assert on an invocation
      counter, not just on the raised exception).
- [ ] HALF_OPEN admits at most `half_open_max_calls` probes concurrently; other callers
      keep failing fast.
- [ ] A failed probe returns the circuit to OPEN and lengthens the next window.
- [ ] Closing the circuit resets the backoff to the base timeout.
- [ ] A nested breaker's `CircuitBreakerOpenException` does not trip the outer breaker.

## Caller

- [ ] `CircuitBreakerOpenException` is handled distinctly from the dependency's own
      errors — the former guarantees nothing was sent.
- [ ] A fallback exists and has been exercised: cached value, degraded mode, or a
      deliberate halt. It is never a silently empty result.
- [ ] Any retry layer above the breaker treats the open circuit as terminal and does not
      burn its retry budget against it.
- [ ] No lock, position or order-bearing resource is held across the wrapped call.

## Operations

- [ ] State transitions are published to metrics and alerting via `on_state_change`.
- [ ] `total_short_circuits` is recorded and reviewed — it is the count of decisions made
      on fallback data.
- [ ] `force_open()` / `reset()` are available to operators and access-controlled like any
      other live-trading control.
- [ ] The open-circuit path has been exercised end to end in staging, including the alert.

---
name: circuit-breaker-for-downstream-service-calls
description: >-
  Use when a trading process calls a non-order dependency (reference data, an alt-data
  vendor, an internal microservice) that can hang and exhaust threads and connection
  pools. Never wrap order submission or cancels with it.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, circuit-breaker, resilience, fail-fast, downstream-dependency, graceful-degradation, thread-safety
  brokers_frameworks: "Generic Infrastructure; Python; requests"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading process makes **synchronous calls to a downstream service
it does not control** — a reference-data or corporate-action API, a historical/alt-data
vendor, a sentiment endpoint, an internal risk-annotation microservice — and that service
can become slow or unavailable without the trading system being obliged to stop.

The failure this prevents is not the failed call. It is the *queue behind it*: N threads
blocked on a dependency that takes 30 seconds to time out will hold N connections, N
threads and N slots in whatever pool they came from, and the outage propagates into
components that never touched the sick service. The breaker converts a slow failure into
a fast, explicit one that the caller can handle — cached value, degraded mode, skip.

`CircuitBreaker` in `scripts/circuit_breaker.py` is the reference implementation:
thread-safe, monotonic-clocked, with a single-probe HALF_OPEN, escalating backoff, and
optional slow-call detection.

## When NOT to Use

- **On the order path.** Never wrap order submission, order cancellation, or a kill
  switch. Fast-failing a *cancel* is strictly worse than failing slowly: it turns a slow
  dependency into an uncancelled live order. For an EU/UK firm in scope of MiFID II RTS 6,
  Article 12 requires the ability to cancel unexecuted orders immediately as an emergency
  measure; a client-side breaker in front of that path works against the obligation. See
  `references/standards.md`.
- **In front of mandatory pre-trade risk controls.** A US broker-dealer's SEC Rule
  15c3-5 controls must be applied, not skipped because a service was slow. If a required
  check cannot run, the correct behaviour is to stop trading, not to fail open.
- **When the caller has no fallback.** A breaker that raises into code which has no
  degraded path has only moved the outage earlier. Decide what happens on
  `CircuitBreakerOpenException` *before* installing the breaker.
- **When the failure is deterministic.** HTTP 400, malformed symbol, bad credentials,
  insufficient funds — these fail identically forever. Opening a circuit on them delays
  the fix and hides the real error.
- **Across independently failing resources.** One breaker over several venues, shards or
  accounts blocks healthy ones because an unhealthy one failed. One breaker per resource
  that can fail on its own.
- **Instead of a timeout.** The breaker cannot interrupt a call already in flight; see
  Prerequisites.

## Prerequisites

- **A client-side timeout on every wrapped call.** This is the hard prerequisite. The
  breaker only learns of a failure when the call raises; with no timeout the first
  `failure_threshold` threads block indefinitely and the circuit never opens. In
  `requests`, that is an explicit `timeout=(connect, read)` — the library has no default.
- **A decided fallback** for the open state: last-known-good cached value, degraded
  feature, skipped enrichment, or a deliberate halt.
- **The correct exception tuple.** `expected_exceptions` must name infrastructure faults
  only. Note that `requests` exceptions do *not* derive from the builtin `TimeoutError` /
  `ConnectionError`, so the default tuple will not catch them.
- **Per-dependency instances**, one per independently failing resource, held for the
  process lifetime — a breaker constructed per call remembers nothing.
- **Somewhere for state changes to go**: a metrics gauge and an alert. A circuit that
  opens silently is an outage nobody is investigating.

## Workflow

1. **Confirm the call is optional.** If the trading loop cannot proceed without the
   answer, a breaker converts a slow degradation into a fast one — that may still be the
   right call, but the correct response to the open circuit is then to halt, not to
   continue with missing data. Order and cancel paths are out of scope entirely.
2. **Set the client timeout first**, and set it shorter than the loop's tolerance. The
   breaker is what stops you *making* the call; the timeout is what stops it hanging.
3. **Choose the exception tuple against the actual client library.** With `requests`,
   pass `(requests.Timeout, requests.ConnectionError)`. Do **not** pass
   `requests.RequestException` or `OSError`: both subsume `requests.HTTPError`, so a
   deterministic HTTP 400 raised by `raise_for_status()` would open the circuit.
4. **Size the threshold against the call rate, not against a feeling.** A 3-failure
   threshold on a service called 500 times a second opens in milliseconds of noise; the
   same threshold on a once-a-minute call takes three minutes to react. Use
   `failure_window_sec` when the call is infrequent, so evidence from an hour ago does
   not combine with today's.
5. **Decide whether slow counts as failed.** For a service whose whole value is
   timeliness, set `slow_call_duration_sec` below the client timeout. Without it, a
   dependency that answers correctly but far too late never trips anything.
6. **Wrap the call** with `call()`, `decorate()`, or the `guard()` context manager.
   Never hold a position-, order- or lock-bearing resource across the wrapped call.
7. **Handle `CircuitBreakerOpenException` distinctly from the dependency's own errors.**
   The former means *nothing was attempted* — the fallback is safe and no request reached
   the service. The latter means the request may well have been received. For anything
   with a side effect, that distinction decides whether a retry is safe.
8. **Do not retry into an open circuit.** If a retry layer sits above the breaker, it
   must treat `CircuitBreakerOpenException` as terminal for that attempt and back off,
   not consume its retry budget against a breaker that is refusing by design.
9. **Let recovery be a single probe.** `half_open_max_calls=1` is the default for a
   reason: a recovering service that gets the entire backlog on the first successful
   response goes straight back down. Raise `half_open_success_threshold` for a dependency
   known to flap.
10. **Publish `snapshot()` as a gauge and alert on the OPEN transition**, using
    `on_state_change`. Include `retry_after_sec` and `total_short_circuits` — the count of
    calls the breaker refused is the number your business logic silently degraded.
11. **Keep a manual override.** `force_open()` takes a known-bad dependency out of the
    path without a deployment; `reset()` restores service without waiting out an escalated
    backoff. Both belong behind the same access control as any other live-trading control.

> Full procedure, including tuning and monitoring: see `references/workflows.md`.
> Engineering standards and the regulatory boundary: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **No timeout on the wrapped client.** The single most common way a circuit breaker
  provides no protection at all. Threads pile up on a hung socket, nothing raises, the
  circuit stays CLOSED, and the process dies exactly as it would have without it.
  Microsoft's Azure Architecture Center lists this failure explicitly.
- **Catching too much.** `expected_exceptions=(Exception,)` — a tempting default, and the
  one this skill's implementation used to ship — counts a `ValueError` from your own
  parsing code as evidence the dependency is down. The breaker then hides your bug behind
  a fake outage.
- **Catching the wrong hierarchy.** `requests.ConnectionError` is **not** a subclass of
  the builtin `ConnectionError`, and `requests.Timeout` is not a subclass of
  `TimeoutError`; both derive from `RequestException(OSError)`. A tuple of builtins
  silently never fires against `requests`. Conversely `OSError` catches everything
  `requests` raises — including `HTTPError` for a 4xx.
- **Counting cumulative rather than consecutive failures.** A counter that never resets on
  success trips on three unrelated blips spread across a session. Reset on success, and
  age out stale evidence with `failure_window_sec`.
- **Using `time.time()` for the recovery timer.** A wall clock steps. An NTP correction
  can make the recovery window appear to have elapsed instantly, or to never elapse.
  Use `time.monotonic()`.
- **A HALF_OPEN state with no lock.** Without one, every thread waiting on the open
  circuit becomes a probe the moment the timer expires, and the recovering service is
  flooded by exactly the herd the pattern exists to prevent.
- **A fixed recovery timeout against a flapping dependency.** The circuit thrashes
  OPEN → HALF_OPEN → OPEN forever at a constant rate. Escalate the timeout on each
  re-open, and add jitter when many processes share the same dependency — otherwise the
  whole fleet probes on the same second.
- **Treating a per-process breaker as a global one.** Twenty processes with
  `half_open_max_calls=1` send twenty probes per window. If the dependency cannot take
  that, the coordination has to live outside the process.
- **Nested breakers that cascade.** If an outer breaker counts the inner breaker's
  `CircuitBreakerOpenException` as a failure, one sick leaf dependency opens every circuit
  above it. The reference implementation never counts it, whatever the exception tuple
  says.
- **Swallowing the open-circuit exception.** Degrading silently is how a strategy runs a
  full session on stale reference data. Log it, count it, alert on the transition.
- **Assuming an open circuit means the request never happened *at the venue*.** It means
  *this* process did not send it. That is only the same thing if nothing else — a retry
  layer, a sibling process, an earlier attempt — already did.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/circuit-breaker-for-downstream-service-calls/scripts`
- Wrap a callable that always raises `ConnectionError`, call it `failure_threshold` times,
  then assert that the next call raises `CircuitBreakerOpenException` **and that the
  callable's invocation counter did not increase** — proving no I/O was attempted.
- Interleave a success between failures and assert the circuit stays CLOSED; a breaker
  that opens here is counting cumulatively.
- Drive the recovery timeout with an injected clock, not `sleep`. Assert the probe is
  refused at `t = timeout - ε` and admitted at `t = timeout`.
- Fail the probe and assert the next window is `backoff_multiplier` times longer, and that
  it returns to the base timeout once the circuit closes.
- Start a slow probe in one thread and assert a concurrent call is refused with
  `state == HALF_OPEN` — this is the single-probe guarantee, and it is the property a
  lock-free implementation silently loses.
- Raise a business exception through the breaker and assert `failure_count` is unchanged.
- Point the breaker at a real staging endpoint, kill the endpoint, and confirm the alert
  fires from `on_state_change` and the gauge reflects OPEN in your dashboard — an untested
  alert path is the normal reason an open circuit goes unnoticed.

## Related Skills

- `graceful-degradation-to-polling-fallback`
- `vendor-outage-fallback-data-source-hierarchy`
- `graceful-degradation-priority-during-partial-outage`
- `smart-order-router-failover-on-venue-outage`
- `broker-status-page-monitoring-integration`
- `multi-broker-rate-limit-handling`
- `chaos-engineering-for-trading-infrastructure`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `log-aggregation-and-centralized-observability`

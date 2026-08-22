# Workflows for the Downstream-Service Circuit Breaker

## 1. Decide what deserves a breaker

- List the synchronous calls the trading loop makes to services it does not control.
- For each, answer: *if this call fails fast, what does the system do?* If the answer is
  "nothing sensible", the breaker is not the missing piece — the fallback is.
- Exclude, always: order submission, order cancellation, kill-switch instructions, and any
  mandatory pre-trade risk check. See `references/standards.md`.
- Group by **independently failing resource**. Two endpoints on the same host that fail
  together share a breaker; two venues do not.

## 2. Set the client timeout before the breaker

The breaker only reacts to exceptions the client raises. With no timeout, nothing raises.

```python
session.get(url, timeout=(1.0, 2.0))   # (connect, read); requests has NO default
```

Choose the timeout from the trading loop's tolerance, not from the vendor's SLA. A
2-second read timeout on a 100 ms loop is already a broken loop.

## 3. Configure

| Parameter | How to choose it |
|---|---|
| `expected_exceptions` | Infrastructure faults of *this client library*. With `requests`: `(requests.Timeout, requests.ConnectionError)`. Never `Exception`, `OSError`, or `requests.RequestException`. |
| `failure_threshold` | Low (2–3) for a high-rate call, where three failures is real evidence. Higher, or paired with `failure_window_sec`, for a low-rate call where three failures may span an hour. |
| `failure_window_sec` | Set it for infrequent calls so stale failure evidence expires. Leave `None` when calls are frequent enough that a success will reset the counter naturally. |
| `recovery_timeout_sec` | The realistic time for this dependency to recover — a restarting service, not a network blip. Too short and you thrash; too long and you stay degraded after recovery. |
| `backoff_multiplier` / `max_recovery_timeout_sec` | Defaults 2.0 and 300 s. Cap it at the longest outage you are willing to stay dark through without a human deciding. |
| `jitter_ratio` | `0.0` for a single process or a deterministic test. 0.1–0.3 across a fleet, so N processes do not all probe on the same second. |
| `half_open_max_calls` | `1` unless the dependency demonstrably handles more. Remember this is per process. |
| `half_open_success_threshold` | `1` normally; 2–3 for a dependency known to flap, so one lucky response cannot re-admit full traffic. |
| `slow_call_duration_sec` | Set below the client timeout for latency-critical dependencies. Leave `None` when a late answer is still a useful answer. |

## 4. Wrap the call

Three equivalent forms:

```python
breaker.call(session.get, url, timeout=(1.0, 2.0))       # direct

@breaker.decorate                                        # decorator
def fetch_reference_data(symbol): ...

with breaker.guard():                                    # context manager
    response = session.get(url, timeout=(1.0, 2.0))
    response.raise_for_status()
```

Handle the two outcomes differently:

```python
try:
    data = breaker.call(fetch_reference_data, symbol)
except CircuitBreakerOpenException as exc:
    # Nothing was sent. The fallback is unambiguously safe.
    metrics.increment("refdata.short_circuited")
    data = cache.last_known_good(symbol, max_age_sec=exc.retry_after_sec + 60)
except requests.Timeout:
    # The request may have been received. For a read that is fine; for anything
    # with a side effect, it is not — see order-placement-idempotency.
    data = cache.last_known_good(symbol)
```

## 5. Monitor

- Publish `breaker.snapshot()` on a schedule as gauges: `state` (0/1/2), `failure_count`,
  `retry_after_sec`, `total_short_circuits`, `total_slow_calls`.
- Wire `on_state_change` to the alerting path. Alert on the transition to OPEN — an
  outage's start is the actionable moment. Report CLOSED as a recovery, not a new alert.
- Track `total_short_circuits` as a *business* metric: it counts how many decisions ran on
  fallback data. A strategy that spent a session on cached reference data needs to be
  reviewed, not just the service that was down.
- The callback runs on the calling thread. Do not do I/O in it — enqueue, do not publish.

## 6. Exercise it

- Unit-test the state machine with an injected clock (see `scripts/test_circuit_breaker.py`);
  never with `sleep`.
- In staging, block the dependency at the firewall (not by stopping it cleanly — you want
  the connect-timeout path, which is the one that hangs threads) and confirm: the circuit
  opens, the alert fires, the fallback engages, and the circuit closes again after the
  dependency returns.
- Repeat with the dependency made *slow* rather than dead, to validate
  `slow_call_duration_sec` and the client timeout.
- Include the breaker in game-day exercises — see `chaos-engineering-for-trading-infrastructure`.

## 7. Review after each open

For every OPEN transition, record: what tripped it, how long the circuit stayed open, how
many calls were short-circuited, what the fallback returned, and whether any trading
decision was made on degraded data. That record is the input to retuning the thresholds —
and, for firms in scope, evidence of the business-continuity arrangements RTS 6 Article 14
requires.

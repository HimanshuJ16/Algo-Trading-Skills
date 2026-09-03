# Deep Workflow Reference — multi-broker-rate-limit-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Model each budget as a set of windows

`MultiBrokerRateLimiter` keys budgets as `"<broker>:<endpoint_category>"`. Register
every documented window against a counter, not just the fastest one:

```python
limiter = MultiBrokerRateLimiter(strict=True)

# Kite Connect: distinct per-endpoint limits.
limiter.register_endpoint_bucket("kite", "quote",      rate_per_sec=1.0,  capacity=1.0)
limiter.register_endpoint_bucket("kite", "historical", rate_per_sec=3.0,  capacity=3.0)
limiter.register_endpoint_bucket("kite", "order",      rate_per_sec=10.0, capacity=10.0)

# Fyers v3: stacked windows on one counter.
limiter.register_endpoint_windows("fyers", "all", [(10, 1.0), (200, 60.0), (100_000, 86_400.0)])

# Alpaca: one account-wide cap covering every endpoint.
limiter.register_endpoint_bucket("alpaca", "order", rate_per_sec=50.0, capacity=50.0)
limiter.register_endpoint_bucket("alpaca", "quote", rate_per_sec=50.0, capacity=50.0)
limiter.register_account_bucket("alpaca", [(200, 60.0)])
```

`register_account_bucket()` windows are consumed **in addition to** the endpoint
budget, and the two are merged into a single all-or-nothing group so a call can never
debit the endpoint window and then fail the account window.

`strict=True` makes an unregistered `broker:category` raise `UnregisteredBudgetError`
instead of silently inheriting a permissive default. Prefer it in production: a typo
(`"quotes"` where you registered `"quote"`) otherwise gets 10 req/sec where Kite
allows 1.

### 2. Classify calls by criticality tier

| Tier | Enum | Traffic | Admission |
|---|---|---|---|
| 0 | `TIER_0_KILL` | Kill switch, risk-breach cancels | Never waits; dispatches even on an empty budget, alerting |
| 1 | `TIER_1_ORDER` | New orders, modifications | Waits, ahead of Tiers 2–3 |
| 2 | `TIER_2_STATUS` | Order status, margin/position checks | Waits, ahead of Tier 3 |
| 3 | `TIER_3_DATA` | Quotes, historical backfill | Waits last |

```python
limiter.execute_call("kite", "order", CallTier.TIER_1_ORDER, lambda: kite.place_order(**params))
```

### 3. Strict-priority admission

`_PriorityGate` makes a waiter ineligible to attempt consumption while any
strictly-lower-numbered tier is pending. This is the part a priority *queue* cannot
do: sorting a queue does not stop an already-admitted low-priority call from taking
the token a Tier 1 order is waiting for.

The consequence is deliberate and must be understood: while a Tier 1 order is blocked
on an empty budget, Tier 2 and Tier 3 waiters are held behind it. It is bounded by
`max_wait_sec` (default 30 s), after which the waiter raises `RateLimitWaitTimeout`
naming the binding window. A status poll that can no longer be answered in time
belongs in the reconciliation path as an error, not blocked in a queue.

### 4. Structural rate-limit classification

`default_rate_limit_classifier` inspects, in order: `RateLimitError`; a `status_code`
attribute; a `response` object carrying `status_code`/`status` and headers. HTTP 429
(RFC 6585 §4) and 503 count as throttles. It **never** inspects message text.

If the broker SDK raises opaque errors, wrap them at the adapter boundary:

```python
try:
    return broker_sdk.call(...)
except broker_sdk.ApiError as exc:
    if exc.http_status == 429:
        raise RateLimitError(str(exc), retry_after=exc.headers.get("Retry-After")) from exc
    raise
```

or pass a broker-specific `classify_fn=` to the limiter. Do not loosen the default
classifier: everything it cannot classify is re-raised unretried, which is the
behaviour that keeps an ambiguous failure from becoming a duplicate order.

### 5. Backoff and server-directed pacing

Delay is `random(0, min(cap, base * 2**attempt))` — full jitter. A parseable
`Retry-After` overrides it. Values beyond `max_retry_after_sec` (default 60 s)
escalate through `alert_fn` and re-raise rather than parking a worker.

### 6. Telemetry

`snapshot()` returns per-tier call counts, per-tier 429 counts, honoured
`Retry-After` count, wait timeouts, cumulative wait and backoff seconds, and the
registered budget labels. Ship it to the metrics pipeline; rising Tier 3 429s are the
signal to lower polling frequency *before* the limit becomes a ban.

## Known Failure Modes

- **Substring 429 detection.** `"429" in str(exc)` matches order id `429123` and limit
  price `429.50`. The retry that follows can duplicate an order the broker accepted.
- **Single-window pacing.** Satisfying 10 req/sec while sending 600 req/min against a
  200 req/min counter.
- **Shared cap modelled per endpoint.** Three 200/min buckets against Alpaca's single
  200/min account cap.
- **Conflated global limiters.** One bucket across quote polling and order execution,
  letting market-data bursts exhaust order bandwidth.
- **Silent Tier 0 backoff.** Retrying an emergency cancel with a long delay during a
  flash crash, instead of dispatching immediately and alerting.
- **Uncapped or lockstep backoff.** Doubling without a ceiling, or additive jitter
  under a cap which returns exactly `cap` for every client in a throttled fleet.
- **Unbounded waits.** Spinning on an exhausted bucket with no deadline — and, with a
  zero or negative configured refill rate, never returning at all.
- **Shared API credential collisions.** Multiple processes under one API key, each
  believing it holds the full quota.

## Production Implementation Reference

- Reference code: `scripts/rate_limiter.py` — `MultiBrokerRateLimiter`, `TokenBucket`,
  `CallTier`, `RateLimitError`, `RateLimitWaitTimeout`, `RateLimiterMetrics`,
  `parse_retry_after`, `full_jitter_backoff`.
- Automated unit tests: `scripts/test_rate_limiter.py`.
- Verified per-broker limits and sources: `references/standards.md`.

## Migration note (v1 → v2)

`TokenBucket`, `TieredCallQueue`, `CallTier`, the `TIER_*` constants,
`register_endpoint_bucket()` and the positional signature of `execute_call()` are
unchanged. Two behaviours did change deliberately:

1. **Rate limits are no longer detected from error text.** Callers that raised
   `Exception("HTTP 429 ...")` must now raise `RateLimitError`, expose a
   `status_code`, or supply a `classify_fn`. Otherwise the error propagates unretried
   — which is the safe direction.
2. **Tiers 1–3 now wait against a deadline** (`max_wait_sec`, default 30 s) and raise
   `RateLimitWaitTimeout` instead of spinning indefinitely.

# Pre-Flight / Sign-off Checklist — multi-broker-rate-limit-handling

Use this before considering the skill's implementation complete.

## Budget configuration

- [ ] **Limits re-verified at source.** Every configured limit was read from the
      broker's own current documentation, not from `references/standards.md` alone —
      that table records what was true on 2026-08-26 and brokers change limits
      without notice.
- [ ] **Every window registered.** For each counter, the per-second *and* per-minute
      *and* per-day/per-30min windows are all declared via
      `register_endpoint_windows()`. Pacing only the fastest window is the default
      mistake.
- [ ] **Account-wide caps declared separately.** Brokers that meter all endpoints
      against one counter (Alpaca, ICICI Breeze) use `register_account_bucket()`,
      not N per-endpoint buckets.
- [ ] **Endpoint isolation where it exists.** Brokers with genuinely distinct
      per-endpoint limits (Kite: quote 1/s, historical 3/s, order 10/s) have
      distinct buckets registered.
- [ ] **Tier 0 headroom verified, not assumed.** Confirmed from the broker's docs
      whether order/cancel endpoints actually have separate capacity. This is true
      for Kite, reversed for Upstox, and absent for Breeze and Alpaca.
- [ ] **`strict=True` in production.** An unregistered `broker:category` raises
      rather than silently inheriting a permissive default rate.

## Runtime behaviour

- [ ] **Tier classification complete.** Every outbound call site passes an explicit
      `CallTier`; no call reaches the broker outside `execute_call()`.
- [ ] **Tier 0 never blocks.** Kill-switch and risk-breach cancels dispatch even on
      an exhausted budget, and raise an operator alert when they do.
- [ ] **Tier 0 alert has a live destination.** `alert_fn` reaches a human on a path
      that does not depend on the throttled broker connection, and the manual
      intervention path (broker terminal, dealer phone) is documented.
- [ ] **Structural 429 classification.** Broker errors expose `status_code`, or are
      wrapped in `RateLimitError`, or a `classify_fn` is supplied. No code path
      classifies a throttle by searching the message text for "429".
- [ ] **Ambiguous errors are not retried.** Timeouts and connection resets propagate
      unretried to the idempotency layer — see `order-placement-idempotency`.
- [ ] **Backoff capped and fully jittered.** `max_backoff_sec` is set; delay is
      `random(0, min(cap, base * 2**attempt))`, not additive jitter under a cap.
- [ ] **`Retry-After` honoured.** Both `delay-seconds` and `HTTP-date` forms parse;
      values beyond `max_retry_after_sec` escalate rather than parking a worker.
- [ ] **Wait deadlines set.** `max_wait_sec` is tuned per tier so a Tier 2 status
      poll surfaces `RateLimitWaitTimeout` to reconciliation instead of blocking.

## Observability

- [ ] **Telemetry shipped.** `snapshot()` output (per-tier calls, per-tier 429s,
      wait timeouts, honoured `Retry-After` count, cumulative wait/backoff) reaches
      the metrics pipeline.
- [ ] **Alerting on Tier 0/1 429s.** Any 429 on Tier 0 or Tier 1 pages someone;
      Tier 3 429s trend on a dashboard for polling-frequency tuning.

## Multi-process safety

- [ ] **Single process per API key**, or a distributed limiter is in place. This
      implementation is in-process only: two bots under one key each believe they
      hold the full quota.

## Testing

- [ ] **Automated tests pass.** Run
      `python -m unittest discover -s skills/multi-broker-rate-limit-handling/scripts`
      and confirm all tests pass.
- [ ] **Load-tested against sandbox/paper.** Simulated Tier 3 bursts confirm Tier 0/1
      calls still complete within the agreed latency bound.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Brokers configured & limits re-verified on: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

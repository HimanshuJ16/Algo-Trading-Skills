---
name: multi-broker-rate-limit-handling
description: >-
  Use when API call volume approaches a broker's limits and risk-critical calls such as
  cancels must never queue behind data polling. Multi-window token buckets per endpoint
  class and account, strict tier priority, and structural 429 handling.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, rate-limiting, token-bucket, http-429, fyers-api-v3, zerodha-kite-connect
  brokers_frameworks: "Fyers API v3; Zerodha Kite Connect; ICICI Breeze API; Upstox API v2; Alpaca Trading API; IBKR API"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this when integrating any broker's REST API for anything beyond a handful of manual calls, or when several strategies/instruments multiply call volume against one broker account. Brokers meter requests differently — some per endpoint class, some against a single account-wide counter, most across several stacked time windows at once — and a single global rate limiter is wrong in both directions: it throttles risk-critical calls that had headroom, and it fails to protect the endpoint whose limit is actually binding.

The skill covers four mechanisms: **multi-window token buckets** so a per-minute or per-day counter is respected as well as the per-second one, **strict tier priority** so a kill-switch cancel is never queued behind a quote burst, **structural 429 classification** so a retry never duplicates a live order, and **`Retry-After`-aware full-jitter backoff** so a 429 does not escalate into a ban.

## When NOT to Use

- **When the constraint is not shaped like a rate.** IBKR's historical-data rules include "no identical request within 15 seconds" and "no 6+ requests for the same Contract/Exchange/Tick Type within 2 seconds". A token bucket cannot express request-identity rules; those need a request-fingerprint cache alongside the limiter.
- **Across multiple processes or hosts.** Limits are enforced per API key or per account, not per process. Two bots sharing credentials each believe they hold the full quota and will together exceed it. This limiter is in-process only — a shared budget needs a distributed limiter (Redis token bucket or equivalent).
- **For bulk historical backfill as the primary job.** Pacing a multi-year backfill needs chunk checkpointing and resume semantics this skill does not provide — see `historical-data-backfill-rate-limit-management`.
- **As a substitute for pre-trade risk controls.** A client-side limiter prevents bans. It is not an SEC Rule 15c3-5 or MiFID II RTS 6 control — see `sec-rule-15c3-5-risk-controls-us`.
- **As a substitute for idempotency.** This limiter retries only errors it can positively classify as throttles. Ambiguous failures (timeouts, resets) are the domain of `order-placement-idempotency`.

## Prerequisites

- The documented limits for each broker in use, including **every** window (per-second *and* per-minute *and* per-day) and whether the counter is per endpoint or account-wide. `references/standards.md` has verified figures and sources for the six brokers above.
- A priority classification for every call type the bot makes: kill/cancel > order placement > order status > quotes/data.
- Broker errors that expose an HTTP status **structurally** — an exception with `status_code`, or a `response` object carrying one. If the SDK only raises opaque strings, wrap it before it reaches the limiter.

## Workflow

1. **Register each budget with all of its windows.**
   - `register_endpoint_windows(broker, category, [(10, 1.0), (200, 60.0), (100_000, 86_400.0)])` for a Fyers-shaped limit.
   - **Decision point — one window is not a budget.** Pacing only the per-second window passes bursts the per-minute counter will reject. The windows are consumed all-or-nothing: if any window would refuse, none is debited, otherwise every rejected attempt silently leaks capacity from the faster window.

2. **Declare account-wide caps separately from endpoint caps.**
   - Alpaca (200 req/min per account) and ICICI Breeze (100 req/min, 5,000 req/day across every endpoint) meter all traffic against one counter. Use `register_account_bucket()`; it is consumed *in addition* to the endpoint budget.
   - **Decision point — do not model a shared cap as per-endpoint buckets.** Two 100/min endpoint buckets against a shared 100/min account cap issue 200 req/min and get you banned.

3. **Classify every outbound call by criticality tier before it enters admission control.**
   - **Tier 0** — kill-switch, risk-breach cancellations. Never queued or delayed.
   - **Tier 1** — new order placement, modification.
   - **Tier 2** — order status polling, position/margin checks.
   - **Tier 3** — quote polling, historical backfill.

4. **Do not assume the order endpoint has spare headroom for Tier 0 — verify it.**
   - It is often claimed that brokers give order/cancel endpoints more generous limits than data endpoints. That is true for Kite Connect (10/sec orders vs 1/sec quotes) and **false or meaningless for most others**: Upstox gives *data* endpoints 50/sec against 10/sec for regular-algo order placement, while Breeze and Alpaca have no endpoint split at all.
   - **Decision point — Tier 0 dispatches even with an empty budget, and that is deliberate.** Eating a 429 on a kill-switch cancel beats not sending it. The limiter alerts instead of blocking, because the broker may still reject it and an operator needs to reach the manual path (broker terminal, phone) immediately.

5. **Give Tiers 1–3 strict-priority admission, not a sorted queue.**
   - Sorting one FIFO queue does not stop an in-flight quote poll from taking the token a Tier 1 order is waiting for. A waiter must be made *ineligible* while a higher-priority waiter is pending.
   - **Decision point — strict priority needs a deadline.** While a Tier 1 order is blocked on an empty budget, Tier 2/3 are held behind it. That is correct, and it is why every wait carries `max_wait_sec` and raises `RateLimitWaitTimeout` rather than spinning. A status poll that can no longer be answered in time should surface as an error to reconciliation, not block forever.

6. **Classify a throttle structurally, then back off with full jitter and honour `Retry-After`.**
   - **Decision point — never detect a 429 by substring.** `"429" in str(exc)` matches order id `429123` and limit price `429.50`. A false positive retries a call the broker may already have executed. Classify on `status_code`, and re-raise anything unclassifiable unretried.
   - Backoff is $t = \text{random}\left(0,\ \min\left(t_{\text{cap}},\ t_{\text{base}} \times 2^{\text{attempt}}\right)\right)$. A parseable `Retry-After` (RFC 9110 §10.2.3 — `delay-seconds` **or** `HTTP-date`) always wins over the computed value.
   - **Decision point — cap what you will sleep through.** A `Retry-After` beyond `max_retry_after_sec` means the broker wants minutes; escalate and let the caller decide, rather than parking a worker.

7. **Log which endpoint, tier and window each rejection hit.** `snapshot()` reports calls and 429s per tier plus the binding window. This is what lets you lower polling frequency before production 429s rather than discovering the limit live during market hours.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Verified per-broker limits with sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Detecting a rate limit by searching the error message for "429".** It matches order ids, prices and quantities. Misclassifying a rejection as a throttle retries a submission the broker may already have accepted — a duplicate order, from a component whose job was to protect you.
- **Pacing only the per-second window.** Fyers meters 10/sec *and* 200/min *and* 100,000/day against one counter. A limiter that satisfies 10/sec happily sends 600/min.
- **Modelling an account-wide cap as per-endpoint buckets.** Alpaca's 200 req/min is per *account*; three endpoint buckets of 200/min issue 600/min against it.
- **Assuming the order endpoint has headroom the data endpoint doesn't.** Reversed on Upstox, absent on Breeze and Alpaca. Verify per broker; the whole Tier 0 bypass strategy rests on this and it is not a general rule.
- **Additive jitter under a cap.** `min(cap, base * 2**n + jitter)` returns exactly `cap` for every client once the exponential term passes the cap, so a throttled fleet retries in lockstep at the moment the herd is largest. Use full jitter across the whole interval.
- **Uncapped backoff.** Doubling without a ceiling delays order-status confirmation past the point where `order-placement-idempotency`'s reconciliation needs an answer.
- **Retrying a 429 too soon, or ignoring `Retry-After`.** A self-computed delay shorter than the broker's stated reset window is precisely what escalates a 429 into an IP or key ban.
- **Silently defaulting an unregistered endpoint.** A typo — `"quotes"` where you registered `"quote"` — inheriting a permissive default instead of Kite's real 1 req/sec is a ban waiting to happen. Run with `strict=True` in production.
- **Waiting without a deadline.** A spin loop on an exhausted bucket blocks the calling thread indefinitely; with a misconfigured (zero or negative) refill rate it never returns at all.
- **Treating all 429s identically regardless of tier.** A Tier 3 quote poll should back off silently; a Tier 0 kill-switch cancel that is throttled needs an alert and an alternate path, not silent backoff.
- **Counting per-process rather than per-key.** Two bot processes under one API key double the effective call rate against a limit neither can see.

## Verification

- **Multi-window:** a budget of 10/sec + 200/min grants exactly 10 in the first second, then 0 until the second refills — and after 60 s a 200-call burst still yields only 10.
- **All-or-nothing:** with windows 100/sec + 5/min, 20 attempts after the per-minute window is spent must leave the per-second window above 90 tokens. (Sequential consumption would drain it.)
- **Account cap:** two full endpoint buckets under a 4/min account cap grant exactly 4 calls in total, then refuse both endpoints.
- **Classification:** `Exception("Order 429123 rejected: insufficient margin")` must produce exactly **one** attempt and zero retries; an exception with `status_code = 429` must be retried.
- **Backoff:** 400 samples of `full_jitter_backoff(8, base=1, cap=16)` all fall in $[0, 16)$ with mean $\approx 8$ and more than 50 distinct values. (Additive jitter returns exactly 16.000 on all 400.)
- **`Retry-After`:** a numeric header is honoured exactly; an `HTTP-date` 120 s out parses to $\approx$ 120 s; a past date clamps to 0; malformed values fall back to jitter, never to an immediate retry; a value beyond `max_retry_after_sec` escalates without sleeping.
- **Tier priority:** with 8 Tier 3 callers confirmed parked at the gate and one Tier 1 order arriving after them, the Tier 1 call must be served **first**. Without the priority gate it wins roughly 1 time in 9 — this is the regression test, not a coin flip.
- **Concurrency:** 32 threads racing a capacity-4 budget must be granted exactly 4.
- **Deadlines:** a second call against a 1-per-hour budget raises `RateLimitWaitTimeout` naming the binding window, rather than blocking.
- **Tier 0:** with the budget fully drained, the call still executes and raises exactly one alert.
- Load-test against the broker's sandbox/paper environment with simulated Tier 3 bursts and confirm Tier 0/1 calls still complete within an acceptable latency bound.
- Confirm logs show zero 429s on Tier 0 calls over a multi-day live run.
- Run `python -m unittest discover -s skills/multi-broker-rate-limit-handling/scripts` and confirm all tests pass.

## Related Skills

- `token-lifecycle-live-probing`
- `order-placement-idempotency`
- `kill-switch-and-drawdown-circuit-breakers`
- `historical-data-backfill-rate-limit-management`
- `broker-side-order-throttle-detection`
- `order-to-trade-ratio-fee-penalty-avoidance`

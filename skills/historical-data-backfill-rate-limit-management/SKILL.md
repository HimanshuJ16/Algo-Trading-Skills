---
name: historical-data-backfill-rate-limit-management
description: >-
  Use when backfilling multi-year historical tick/bar data from a rate-limited vendor REST API: token-bucket request pacing, HTTP 429 handling with full-jitter exponential backoff, RFC 9110 Retry-After compliance, error classification, and per-chunk checkpointing so an interrupted backfill resumes instead of restarting.
domain: Data Management Global
subdomain: Historical Data Backfills & Rate Limit Pacing
tags: ["historical-data", "backfill", "rate-limiting", "token-bucket", "http-429", "exponential-backoff", "checkpointing"]
brokers_frameworks: ["Polygon.io (now Massive)", "Alpha Vantage", "Binance Spot REST API", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a data pipeline must pull a large historical range out of a vendor REST API that meters requests — five years of 1-minute bars is thousands of paged calls, and an un-paced ingester will hit HTTP 429, then an IP ban, long before it finishes. It covers the four mechanisms that make such a job survivable: **token-bucket pacing** so requests leave at the vendor's rate, **full-jitter exponential backoff** that honours `Retry-After`, **error classification** so a revoked API key fails fast instead of consuming the retry budget, and **per-chunk checkpointing** so a failure at 99% resumes at chunk 99, not chunk 0.

## When NOT to Use

- **For live/streaming market data.** This paces bulk REST paging. A real-time feed's problem is reconnect and sequence-gap recovery, not quota — see `websocket-reconnection-with-state-recovery` and `sequence-number-gap-detection-for-feeds`.
- **When the binding constraint is a daily quota rather than a rate.** A token bucket models one requests-per-interval window. Alpha Vantage's free tier is [25 API requests per day](https://www.alphavantage.co/support/); no pacing strategy fits a five-year minute-bar backfill into that, and the engine will not detect the ceiling. Check the daily budget before writing code.
- **When the vendor prices requests by weight.** Binance assigns each route a `weight` and reports consumption via `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`. One-token-per-request understates heavy multi-symbol routes; pass a per-call weight to `TokenBucketLimiter.acquire(tokens=...)`.
- **Across multiple processes or hosts.** Vendor limits are enforced per IP or per API key, not per process. Two backfill workers sharing credentials each believe they hold the full quota and will together exceed it. That needs a shared/distributed limiter — see `multi-broker-rate-limit-handling`.
- **For risk-critical calls.** Never queue a cancel or kill-switch call behind a backfill's pacing delay. Backfill is the lowest-priority tier by definition.

## Prerequisites

- The vendor's documented limits: requests per interval, burst allowance, whether a **daily** cap also applies, and whether requests are weighted.
- Which HTTP statuses the vendor uses for throttling (429 per RFC 6585) and for auto-bans (Binance uses 418).
- A fetch callback that performs the HTTP call and reports back `FetchOutcome(success, records, status_code, retry_after, retryable)` — **including the raw `Retry-After` header**, without which server-directed pacing cannot work.
- Writable durable storage for the checkpoint file if the job is longer than you are willing to repeat.

## Workflow

1. **Chunk the date range**:
   - `generate_date_chunks(start, end, chunk_days)` produces contiguous, non-overlapping chunks with **both bounds inclusive**: chunk $n$ ends on day $d$, chunk $n+1$ starts on $d+1$.
   - **Decision point — the boundary day is the classic silent corruption.** Vendor history endpoints are almost always inclusive of both bounds, so the hand-rolled "advance the cursor by `chunk_days` and use it as the next start" duplicates a full day of bars into the store on every chunk boundary. Verify the vendor's inclusivity before trusting either convention.

2. **Pace every request through the bucket**:
   - Refill rate $\text{rate\_hz} = \frac{\text{requests\_per\_minute}}{60}$, capacity = burst allowance.
   - **Decision point — a computed wait is not permission to send.** `acquire_token()` is a *probe*: a non-zero return means no token was taken. Dispatching anyway — or sleeping some shorter fixed interval and then dispatching — converts the limiter into decoration. Use the blocking `acquire()`, which re-checks after sleeping because a concurrent worker may have taken the token the wait was sized for.
   - Use a **monotonic** clock. A wall clock can step backwards under NTP correction, which makes the elapsed interval negative and silently *removes* tokens.

3. **Classify the failure before retrying**:
   - Retryable: 429, 500, 502, 503, 504, and transport-level timeouts (which carry no status at all — flag them explicitly).
   - **Permanent: every other 4xx.** A 401, 403, or 404 will return the same answer on every attempt; retrying it three times just spends quota that the next chunk needs.
   - **HTTP 418 from Binance is an auto-ban, not a retry signal.** Bans "scale in duration for repeat offenders, from 2 minutes to 3 days". Defer the chunk, checkpoint, and exit — do not hold the process open inside a retry loop.

4. **Back off with full jitter, and let the server override**:
   - $t_{\text{wait}} = \text{random}\left(0,\ \min\left(t_{\text{cap}},\ t_{\text{base}} \times 2^{\text{attempt}}\right)\right)$
   - **Decision point — a parseable `Retry-After` always wins.** RFC 9110 §10.2.3 permits either `delay-seconds` or an `HTTP-date`; parse both. A self-computed delay shorter than the server's stated reset window is precisely what escalates a 429 into a ban.
   - **Decision point — cap what you will sleep through.** A `Retry-After` beyond `max_retry_after_sec` means the vendor wants hours; the correct response is to checkpoint and defer, not to block a worker for the afternoon.

5. **Checkpoint every terminal chunk outcome**:
   - Persist after each chunk, not at the end. On resume, chunks recorded `COMPLETED` are skipped and counted as resumed; `FAILED` and `DEFERRED_RETRY_AFTER` chunks are re-attempted.
   - Write to a temp file and `os.replace` it into position, so a crash mid-write leaves the last good checkpoint rather than a truncated file that fails to parse.
   - Emit a `BackfillExecutionReport` carrying completed/failed/deferred/resumed counts and the pacing and backoff time actually spent.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A rate limiter that computes the wait and then doesn't wait it.** Sleeping a fixed short interval (`time.sleep(min(0.01, wait_sec))`) and dispatching regardless turns a 1 req/sec budget into ~100 req/sec. The bucket must *consume* a token before the request leaves, and the post-sleep re-check is what makes it enforcement rather than telemetry.
- **Retrying a 429 too soon.** Binance states plainly that "repeatedly violating rate limits and/or failing to back off after receiving 429s will result in an automated IP ban (HTTP status 418)". The 429 is the warning; the ban is what follows.
- **Additive jitter under a cap gives zero jitter where it matters.** `min(cap, base * 2**attempt + random(0.1, 0.5))` returns *exactly* `cap` for every client once the exponential term passes the cap — so a fleet throttled together retries in lockstep at the moment the herd is largest. Use full jitter over the whole interval (AWS, Brooker 2015).
- **Parsing `Retry-After` as a float only.** RFC 9110 permits an `HTTP-date`; a float-only parser silently discards it and falls back to a much shorter delay.
- **Retrying non-retryable errors.** A tight loop against a 401 burns the quota the rest of the backfill needs and, on some vendors, counts toward the ban threshold.
- **Checkpointing only at the end of the job.** A 5-year backfill that dies at 99% and restarts at 0% is the failure this skill exists to prevent — and an in-memory `status` field is not a checkpoint.
- **Assuming per-process limits.** Running two backfill workers against one API key doubles the request rate against a quota neither worker can see.
- **Duplicate `chunk_id` values.** Checkpoint state is keyed by chunk id; a duplicate can mark an unfetched range as complete, leaving a permanent hole in the history that no error ever reports.
- **Sizing pacing to a per-minute limit while a daily cap is the real constraint** — the job runs politely for hours and then dies at the daily ceiling with no partial-day handling.

## Verification

- `generate_date_chunks("2026-01-01", "2026-01-10", chunk_days=3)` must yield exactly four chunks — `01-01..01-03`, `01-04..01-06`, `01-07..01-09`, `01-10..01-10` — contiguous, non-overlapping, and truncated at the end bound.
- **Pacing:** a 1 token/sec bucket with capacity 2, driven for 10 acquisitions against an injected clock, must record **8.0 s** of waiting. (The pre-fix implementation recorded 0.08 s — a 100× under-throttle.)
- **Jitter:** 400 samples of `calculate_backoff_delay(8)` with base 1 s / cap 16 s must all fall in $[0, 16)$ with mean $\approx 8$. (The pre-fix implementation returned exactly 16.000 on all 400.)
- **Retry-After:** a numeric header is honoured exactly; an `HTTP-date` 120 s in the future parses to $\approx$ 120 s; a past date clamps to 0; malformed values fall back to jitter rather than to an immediate retry.
- **Classification:** HTTP 404 produces exactly one fetch attempt and zero backoff; HTTP 503 with `max_retries=2` produces exactly three; HTTP 418 defers without retrying.
- **Checkpointing:** after a completed run the store holds every chunk id as `COMPLETED`; a second run over the same range performs **zero** fetches and reports `resumed_chunks_count == 4`. With one chunk failed, only that chunk is re-fetched on resume.
- **Concurrency:** 16 threads racing `try_acquire()` on a capacity-4 bucket with a frozen clock must grant exactly 4.
- Run `python -m unittest discover -s skills/historical-data-backfill-rate-limit-management/scripts` and confirm all tests pass.

## Related Skills

- `multi-broker-rate-limit-handling`
- `historical-tick-data-storage-and-compaction`
- `vendor-outage-fallback-data-source-hierarchy`
- `broker-side-order-throttle-detection`

---
name: historical-data-backfill-rate-limit-management
description: >-
  Quantitative data engineering pipeline engine for managing historical tick/bar backfills, implementing Token Bucket rate limiting, handling HTTP 429 with exponential backoff + jitter, and checkpointing paged chunks.
domain: Data Management Global
subdomain: Historical Data Backfills & Rate Limit Pacing
tags: ["historical-data", "backfill", "rate-limiting", "token-bucket", "http-429", "exponential-backoff", "checkpointing"]
brokers_frameworks: ["Polygon.io", "Alpha Vantage", "Binance REST API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative data pipelines, historical backtesting data ingesters, and financial REST API backfill managers. Fetching multi-year historical tick or bar data (e.g. 5 years of 1-minute bars) requires thousands of REST API calls. Unregulated requests trigger HTTP 429 Too Many Requests errors, temporary IP bans, or account suspensions. This module implements a **Token Bucket rate limiter**, date-range chunking, checkpointing, and **exponential backoff with jitter** to backfill historical data at maximum safe throughput without hitting vendor quotas.

## Prerequisites

- Vendor API rate limit specifications (`requests_per_minute`, `max_burst_capacity`, `max_retries`).
- Target backfill specifications (`symbol`, `start_date_iso`, `end_date_iso`, `chunk_days`).

## Workflow

1. **Date-Range Chunking & Task Paging**:
   - Divide total backfill period $[T_{\text{start}}, T_{\text{end}}]$ into $N$ contiguous date-chunk pages (e.g. 30-day chunks).
2. **Token Bucket Rate Limiting**:
   - Maintain token bucket filling at rate $\text{rate\_hz} = \frac{\text{requests\_per\_minute}}{60.0}$.
   - Paces request dispatch with delay $\Delta t = \frac{1}{\text{rate\_hz}}$.
3. **HTTP 429 Handling & Exponential Backoff**:
   - If HTTP 429 / 503 error occurs, honor `Retry-After` header or calculate:
     $$t_{\text{wait}} = \min(t_{\text{max}}, t_{\text{base}} \times 2^{\text{retry}} + \text{jitter})$$
4. **Checkpointing & Audit Logging**: Output structured `BackfillExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Paced High-Concurrency Requests**: Spawning hundreds of thread pool workers to request historical REST data simultaneously, causing instant HTTP 429 bans.
- **Ignoring Retry-After Headers**: Retrying immediately after receiving an HTTP 429 response without waiting for the server-specified reset window.
- **Un-Checkpointing Interrupted Jobs**: Failing to save progress checkpoints, forcing full restart of 5-year backfills when a network error occurs at 99% completion.

## Verification

- Instantiate `HistoricalBackfillManagerEngine`. Config 60 req/min (1 req/sec). Backfill 1-year range split into 12 monthly chunks. Inject 2 simulated HTTP 429 errors $\implies$ verify engine applies token bucket pacing, executes exponential backoff retries, completes all 12 chunks, and generates checkpoint state.
- Run `python scripts/test_historical_data_backfill_rate_limit_management.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `vendor-outage-fallback-data-source-hierarchy`
---

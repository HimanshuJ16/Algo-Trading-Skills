# Workflows for Historical Data Backfill Rate Limit Management

1. **Establish the quota before writing any code**:
   - Record the vendor's requests-per-interval limit, burst allowance, whether a **daily**
     cap also applies, and whether routes are weight-priced.
   - Compute the job's total request count: $N = \lceil \text{days} / \text{chunk\_days} \rceil$
     per symbol, times the symbol universe. If $N$ exceeds a daily cap, pacing cannot help —
     the job needs a different plan, a longer schedule, or a bulk/flat-file endpoint.

2. **Chunk the date range**:
   - `generate_date_chunks(start_date_iso, end_date_iso, chunk_days)` returns contiguous,
     non-overlapping `BackfillChunk` objects with **both bounds inclusive**; chunk $n$ ends
     on day $d$ and chunk $n+1$ starts on $d+1$. The final chunk is truncated at the end
     bound rather than overshooting it.
   - Raises `BackfillConfigurationError` on a reversed range, `chunk_days < 1`, or a
     non-ISO-8601 date.
   - Chunk size is a trade-off, not a constant: larger chunks mean fewer requests but a
     larger re-fetch when one fails, and most vendors cap the rows returned per call — a
     chunk that exceeds the cap is silently truncated, which looks like missing history
     rather than an error. Verify the row cap and size chunks below it.

3. **Configure the engine**:
   ```python
   engine = HistoricalBackfillManagerEngine(
       requests_per_minute=300,        # from the vendor's published limit
       max_burst_capacity=10,          # instantaneous burst the vendor tolerates
       max_retries=3,                  # retryable statuses only
       base_retry_delay_sec=1.0,
       max_retry_delay_sec=16.0,
       max_retry_after_sec=300.0,      # longer server-directed waits are deferred
       checkpoint_store=JsonFileCheckpointStore("backfill_AAPL.json"),
   )
   ```
   - The constructor rejects `requests_per_minute <= 0`, `max_burst_capacity < 1`,
     `max_retries < 0`, `base_retry_delay_sec <= 0`, and
     `max_retry_delay_sec < base_retry_delay_sec`. A zero refill rate previously divided
     by zero inside the limiter; it now fails at construction.

4. **Token bucket pacing**:
   - Tokens refill at $\text{rate\_hz} = \frac{\text{requests\_per\_minute}}{60}$, capped at
     `max_burst_capacity`, on a **monotonic** clock.
   - `try_acquire(tokens)` consumes and returns `0.0`, or returns the seconds until the
     bucket can satisfy the request **without consuming anything**.
   - `acquire(tokens)` blocks until the tokens are genuinely consumed, re-checking after
     each sleep. Under a shared limiter another worker may take the token the wait was
     sized for, so the re-check — not the sleep — is what enforces the limit.
   - The limiter is `threading.Lock`-guarded, so one instance may be shared across a
     worker pool. It is **not** shared across processes.

5. **Fetch and classify**:
   - The callback returns `FetchOutcome(success, records, status_code, retry_after, retryable, error_message)`.
     The legacy `(success, is_429, records)` tuple is still accepted, but it carries no
     `Retry-After` and no status detail, so server-directed pacing degrades to computed
     backoff.
   - Classification:

     | Outcome | Handling |
     |---|---|
     | `success=True` | Chunk `COMPLETED`, records counted, checkpointed. |
     | 429 / 500 / 502 / 503 / 504 | Retryable. Backoff, retry up to `max_retries`. |
     | 418 (Binance IP auto-ban) | Chunk `DEFERRED_RETRY_AFTER`. **No retry.** Resume after the ban window with a lower `requests_per_minute`. |
     | `Retry-After` > `max_retry_after_sec` | Chunk `DEFERRED_RETRY_AFTER`. Do not hold the worker open. |
     | Any other 4xx | Permanent. Fail the chunk immediately — no retry, no quota burned. |
     | Callback raises | Treated as a retryable transport failure and logged. |
     | Retry budget exhausted | Chunk `FAILED`. |

6. **Backoff**:
   - $t_{\text{wait}} = \text{random}\left(0,\ \min\left(t_{\text{cap}},\ t_{\text{base}} \times 2^{\text{attempt}}\right)\right)$ — full jitter across the whole interval.
   - `parse_retry_after` accepts `delay-seconds` **and** `HTTP-date` (RFC 9110 §10.2.3);
     an HTTP-date is resolved against wall-clock UTC and a past date clamps to `0.0`.
     Unparseable or negative values return `None`, so a malformed header falls back to
     computed backoff rather than to an immediate retry.
   - A parseable `Retry-After` overrides the computed delay unconditionally.

7. **Checkpointing and resume**:
   - `CheckpointStore` is the in-memory base; `JsonFileCheckpointStore` persists to disk,
     writing to `<path>.tmp` and `os.replace`-ing it into position so a crash mid-write
     cannot truncate the last good checkpoint. An unreadable checkpoint is logged and
     treated as empty — the backfill re-fetches rather than aborting.
   - State is saved after **every terminal chunk outcome**, so an interrupt costs at most
     the chunk in flight.
   - On resume, `COMPLETED` chunks are skipped (counted in `resumed_chunks_count`);
     `FAILED` and `DEFERRED_RETRY_AFTER` chunks are re-attempted.
   - `chunk_id` values must be unique — state is keyed by them, and a duplicate can mark
     an unfetched range complete. `execute_backfill` rejects duplicates up front.

8. **Audit reporting**:
   - `BackfillExecutionReport` carries `completed_chunks_count`, `failed_chunks_count`,
     `deferred_chunks_count`, `resumed_chunks_count`, `total_records_ingested`,
     `total_rate_limit_throttles`, `total_pacing_wait_sec`, `total_backoff_wait_sec`, and
     `execution_time_sec`.
   - Status: `BACKFILL_SUCCESS` (nothing unresolved), `BACKFILL_PARTIAL` (some completed,
     some failed or deferred), `BACKFILL_FAILED` (none completed).
   - **`total_pacing_wait_sec` is the tuning signal.** If it dominates run time the
     configured rate is below the vendor's actual allowance; if throttles are non-zero it
     is above. Neither is visible without the report.

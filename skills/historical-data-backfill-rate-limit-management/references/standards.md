# Standards for Historical Backfill Rate Limiting

| Metric | Engineering Standard |
|---|---|
| Rate Limiter Algorithm | Token Bucket algorithm MUST be used for request pacing. |
| HTTP 429 Retry Strategy | Exponential backoff with random jitter MUST be used on rate limit errors. |
| Checkpoint Persistence | Progress MUST be checkpointed after every successful date chunk. |

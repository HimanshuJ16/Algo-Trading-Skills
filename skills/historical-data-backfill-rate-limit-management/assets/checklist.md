# Pre-Flight Checklist

- [ ] Is vendor API rate limit quota (requests/min) configured?
- [ ] Is Token Bucket algorithm enforcing pacing delays between requests?
- [ ] Is exponential backoff + jitter active on HTTP 429 errors?
- [ ] Is job progress checkpointed per completed date chunk?

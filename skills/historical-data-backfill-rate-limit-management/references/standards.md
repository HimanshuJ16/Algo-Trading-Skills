# Standards for Historical Backfill Rate Limit Management

## What is standardised, and what is not

Only the **HTTP semantics** below are normative. No regulator, exchange, or standards
body mandates a token bucket, a jitter formula, a chunk size, or a checkpoint cadence —
those are engineering choices, and the earlier "MUST use Token Bucket" framing in this
file overstated them. What *is* binding is each vendor's own contract and published
limits, which vary by plan and change without a deprecation cycle.

| Item | Source | Status |
|---|---|---|
| `429 Too Many Requests` | [RFC 6585 §4](https://www.rfc-editor.org/rfc/rfc6585.html) | Normative. "The 429 status code indicates that the user has sent too many requests in a given amount of time." The response "MAY include a Retry-After header"; responses "MUST NOT be stored by a cache". |
| `Retry-After` | [RFC 9110 §10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after) | Normative. `Retry-After = HTTP-date / delay-seconds` — **both** forms must be parsed. |
| `503 Service Unavailable` | RFC 9110 §15.6.4 | Normative. A server generating a 503 "SHOULD send a Retry-After header field to help clients determine when to retry". |
| `RateLimit` / `RateLimit-Policy` headers | [draft-ietf-httpapi-ratelimit-headers-11](https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/) | **Internet-Draft, not an RFC** (v11 expires Nov 2026). Read these headers opportunistically if a vendor emits them; do not depend on them or describe them as a standard. |
| Full-jitter backoff | AWS Architecture Blog, Marc Brooker, ["Exponential Backoff And Jitter"](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) (4 Mar 2015) | Widely adopted engineering reference, not a standard. `sleep = random(0, min(cap, base * 2 ** attempt))`; the article finds Full Jitter completes with less client work than Equal Jitter and comparable time to Decorrelated Jitter. |
| Token bucket pacing, 30-day chunking, per-chunk checkpointing | — | Engineering defaults in this skill. Tune per vendor. |

## Vendor limits (verified August 2026 — re-verify before relying on them)

These figures move. Treat the table as a worked example of *what to look up*, not as a
cached configuration.

| Vendor | Published limit | Throttle / ban behaviour |
|---|---|---|
| [Binance Spot REST](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) | Weight-based per route; consumption reported in `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`. Limits are **per IP, not per API key**. | `429` on breaking a rate limit; `418` once the IP is auto-banned "for continuing to send requests after receiving 429 codes". `Retry-After` is sent with both, in seconds. Bans "scale in duration for repeat offenders, from 2 minutes to 3 days". The docs are explicit: "When a 429 is received, it's your obligation as an API to back off and not spam the API." |
| [Alpha Vantage](https://www.alphavantage.co/support/) | Free tier: **25 API requests per day** (unlimited for verified open-source/educational projects). Premium plans raise the ceiling. | A **daily** cap, which per-minute pacing cannot satisfy. Per-minute figures circulate in secondary sources but are not stated on the vendor's own support page — confirm against your plan. |
| [Polygon.io — now Massive](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis) | Free/Basic: **5 API requests per minute**. Paid plans: unlimited, with a recommendation to stay "under 100 requests per second". | Note the corporate rebrand: `polygon.io` URLs now 301-redirect to `massive.com`. Update hard-coded documentation links; API hostnames should be verified separately. |

## Engineering defaults in this implementation

| Parameter | Default | Rationale |
|---|---|---|
| `requests_per_minute` | 60 | Placeholder. Must be set from the vendor's published limit. |
| `max_burst_capacity` | 10 | Bucket capacity = burst allowance. Set to what the vendor tolerates in one instant, not to the sustained rate. |
| `max_retries` | 3 | Applies only to *retryable* statuses (429, 500, 502, 503, 504) and transport timeouts. |
| `base_retry_delay_sec` / `max_retry_delay_sec` | 1.0 / 16.0 | Full-jitter interval bounds. |
| `max_retry_after_sec` | 300.0 | Ceiling on a server-directed wait that will be slept through in-process. Beyond it the chunk is deferred and checkpointed — the correct response to a vendor asking for hours. |

## References

- RFC 6585, *Additional HTTP Status Codes* (April 2012) — §4, 429 Too Many Requests.
- RFC 9110, *HTTP Semantics* (June 2022) — §10.2.3 Retry-After, §15.6.4 503 Service Unavailable.
- draft-ietf-httpapi-ratelimit-headers-11, *RateLimit header fields for HTTP* — Internet-Draft.
- Brooker, M., "Exponential Backoff And Jitter", AWS Architecture Blog, 4 March 2015.

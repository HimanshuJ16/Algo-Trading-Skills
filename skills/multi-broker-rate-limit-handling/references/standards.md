# Broker & Framework Coverage — multi-broker-rate-limit-handling

All figures below were read from the cited primary source on **2026-08-26**. Broker
limits change without notice and several are negotiable per account, so treat this
table as a starting point to be re-verified against the broker's live documentation
before it is encoded in a config file — not as a substitute for reading it.

## Documented limits

| Broker / Framework | Documented limits | Shape | Source |
|---|---|---|---|
| **Zerodha Kite Connect** | Quote **1 req/sec**; Historical candle **3 req/sec**; Order placement **10 req/sec**; all other endpoints **10 req/sec**. Separately: **400 orders/min**, **10 orders/sec**, **5,000 orders/day** per user/API key, and a maximum of **25 modifications per order**. | Per endpoint class, plus order-count caps per API key | [Kite Connect v3 — Exceptions & rate limiting](https://kite.trade/docs/connect/v3/exceptions/) |
| **Fyers API v3** | **10 req/sec**, **200 req/min**, **100,000 req/day**. | Stacked windows on one counter. Whether the counter is global or per endpoint is **not stated** in the sources retrieved — see "Unverified" below | [Fyers community — API v3 rate limits](https://fyers.in/community/questions-5gz5j8db/post/apiv3---rate-limits-AFLejjYhJQDw9yI), [Fyers community — Rate Limit v3](https://fyers.in/community/api-algo-trading-bihtdkgq/post/rate-limit---v3-U6UAB9DjULwdnae) |
| **Upstox API** | Order placement (Place / Modify / Cancel / Multi-Order / GTT): **10/sec, 500/min, 2,000/30min** for regular algos, **50/sec** for SEBI-registered algos. Standard APIs (holdings, positions, funds, historical data): **50/sec, 500/min, 2,000/30min**. IPO applications: **1/sec, 10/min, 300/30min**. | Per API, per user; stacked windows | [Upstox — Rate limiting](https://upstox.com/developer/api-documentation/rate-limiting/) |
| **ICICI Breeze API** | **100 calls/min** and **5,000 calls/day** per user, applied **across all endpoints** (quotes, option chain and order placement share one budget). | Single global budget, two stacked windows | [ICICIdirect FAQ — Breeze rate limit](https://www.icicidirect.com/faqs/fno/what-is-the-rate-limit-for-breeze-api) |
| **Alpaca Trading API** | **200 requests/min per account**; breach returns HTTP 429. Alpaca states it will raise this to 1,000/min for accounts moved to non-retail status. | Single global account budget | [Alpaca — Is there a usage limit for the number of API calls?](https://alpaca.markets/support/usage-limit-api-calls) |
| **IBKR TWS API** | **50 messages/sec** from client to TWS. Historical data additionally: no identical request within **15 seconds**; no **6 or more** requests for the same Contract + Exchange + Tick Type within **2 seconds**; no more than **60 requests in any 10-minute period** (BID_ASK counts twice). | Socket message rate, plus non-bucket historical pacing rules | [TWS API — Introduction](https://interactivebrokers.github.io/tws-api/introduction.html), [TWS API — Historical data limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html) |

## What this table implies for the design

Three points matter more than the individual numbers:

1. **"Order endpoints get more headroom" is not a general rule.** It holds for Kite
   (10/sec orders vs 1/sec quotes). It is *reversed* for Upstox, where standard/data
   endpoints get 50/sec and regular-algo order placement gets 10/sec. And it is
   meaningless for Breeze and Alpaca, which meter every endpoint against one budget.
   A Tier 0 cancel therefore has guaranteed headroom on **no** broker in this table
   until you have checked that broker's own documentation.

2. **A limit is usually several windows on one counter, not one number.** Fyers
   (10/sec + 200/min + 100,000/day), Upstox (per-sec + per-min + per-30min) and
   Breeze (100/min + 5,000/day) all stack windows. Pacing only the per-second window
   passes bursts that the per-minute or per-day counter will reject — use
   `register_endpoint_windows()`.

3. **Some limits are not token buckets at all.** IBKR's historical-data rules are a
   sliding window plus an identical-request-dedup rule plus a per-contract burst
   rule. A token bucket cannot express "no identical request within 15 seconds"; that
   needs request-fingerprint caching alongside the limiter.

## Unverified / explicitly qualified

- **Fyers per-endpoint vs global scope.** The official Fyers API v3 documentation site
  (`myapi.fyers.in/docsv3`) did not resolve when checked on 2026-08-26. The 10/sec,
  200/min, 100,000/day figures come from Fyers-hosted community and support posts,
  which are first-party but not the API reference. The *scope* of the counter is not
  stated in any retrieved source. Treat Fyers as a single global budget until
  confirmed — that is the conservative assumption, since assuming per-endpoint
  budgets when the counter is shared over-issues.
- **Whether Breeze cancels/modifies count toward the 100/min budget** is not stated in
  the cited FAQ. Assume they do.
- **The "10 orders per second" figure sometimes quoted for Breeze is not an API rate
  limit.** ICICIdirect's FAQ presents it as the threshold above which a strategy must
  be classified as an algo. Do not encode it as a throughput budget; see
  `india-sebi-algo-trading-tagging-requirements`.

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category
rolls up across the full skill library.

## Protocol standards referenced by the implementation

| Standard | Section | Use |
|---|---|---|
| RFC 6585 | §4 | Defines HTTP 429 Too Many Requests, the status brokers return on throttle. |
| RFC 9110 | §10.2.3 | Defines `Retry-After`, permitting **either** `delay-seconds` **or** an `HTTP-date`. Both forms are parsed by `parse_retry_after()`; a float-only parser silently discards the date form. |

## Regulatory & Operational Notes

Rate-limit design intersects with broker API terms of service, exchange fair-access
messaging limits, and exchange order-to-trade ratio (OTR) penalty regimes — see
`order-to-trade-ratio-fee-penalty-avoidance`. Note that a client-side limiter
protects against *bans*; it does not by itself satisfy any regulatory pre-trade
control obligation (SEC Rule 15c3-5, MiFID II RTS 6), which is a separate concern
handled in `sec-rule-15c3-5-risk-controls-us` and `mifid-ii-algo-trading-compliance-eu`.

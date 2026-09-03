# Broker Integration Standards — broker-side-order-throttle-detection

## Venue signalling behaviour

Whether throttling is *silent* is a per-venue, per-transport property, and it decides
whether this skill applies at all.

| Venue / transport | Behaviour on excess message rate | Silent? | Source |
|---|---|---|---|
| IBKR TWS API (socket) | "The TWS is designed to accept up to fifty messages per second coming from the client side." Excess messages are queued and delayed rather than rejected. | Yes | [TWS API introduction](https://interactivebrokers.github.io/tws-api/introduction.html), [order limitations](https://interactivebrokers.github.io/tws-api/order_limitations.html) |
| IBKR TWS API with `+PACEAPI` | TWS paces the client at 50 msg/s instead of disconnecting it. Set via `SetConnectOptions("+PACEAPI")` before `eConnect`. | Yes | [TWS API 2022 release notes](https://ibkrguides.com/releasenotes/api/tws/prod-2022.htm) |
| IBKR Web API (REST) | HTTP 429 on breach; violating IPs may be placed in a 10-minute penalty box, with permanent blocks for repeat offenders. | No | [Web API pacing limitations](https://www.interactivebrokers.com/docs/web-api/trading/usage-and-availability/pacing-limitations) |
| Binance Spot REST | HTTP 429 on a rate-limit breach, HTTP 418 once an IP is auto-banned for continuing after 429s. `Retry-After` gives the wait in seconds. Order-count breaches return 429 *without* `Retry-After`. | No | [Binance REST API limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) |

**Rule:** where the venue signals explicitly, obey the signal. This skill's latency
inference is weaker evidence and must never shorten a `Retry-After` wait.

## Detector parameters

None of these are set by a regulator, exchange or broker. They are engineering defaults
to be calibrated against your own measured ACK RTT distribution.

| Parameter | Default | Basis | Description |
|---|---|---|---|
| `alpha` | 0.1 | Tuning | EWMA smoothing factor, 0 < alpha <= 1. Higher adapts faster and tolerates more jitter as normal; lower holds a longer memory. |
| `z_score_threshold` | 3.0 | Convention | Deviations above baseline marking `SILENT_THROTTLE`. A 3-sigma convention, not a distributional guarantee — ACK RTT is right-skewed, so the false-positive rate is not the Gaussian one. |
| `elevated_z_threshold` | 1.0 | Tuning | Deviations above baseline marking `ELEVATED_LATENCY`. Must be <= `z_score_threshold`. |
| `max_absolute_rtt_ms` | 500.0 | **Placeholder** | Baseline-independent ceiling. No standard defines it; calibrate against your own p99.9 before relying on it. |
| `min_variance_clamp` | 1.0 | Tuning | Floor on the **variance**, in ms². Applied *before* the square root: sigma = sqrt(max(EWMVar, clamp)). Prevents a deterministic network driving sigma to ~0, where 1 ms of jitter would score an unbounded z. |
| `min_samples_for_detection` | 20 | Tuning | Admitted samples required before the z-test is trusted. Until then the state is `WARMUP`; only the absolute ceiling and ACK timeouts can fire. |
| `ack_timeout_ms` | 5000.0 | **Placeholder** | Age at which an unacknowledged order is reported `ACK_TIMEOUT`. Set from the venue's own acknowledgment SLA where one exists. |
| `min_backoff_ms` | 10.0 | Tuning | Floor on an **active** penalty. Backoff is exactly 0 when healthy; this is not a resting delay. |
| `max_backoff_ms` | 2000.0 | Tuning | Ceiling on the recommended delay. |
| `backoff_multiplier` | 2.0 | AIMD | Multiplicative decrease of dispatch rate on a congestion signal. |
| `backoff_additive_decrease_ms` | 20.0 | AIMD | Additive increase of dispatch rate per healthy ACK, expressed as delay reduction. |
| `elevated_increment_ms` | 10.0 | Tuning | Small additive penalty on `ELEVATED_LATENCY`. |
| `rebaseline_after_consecutive` | 0 (off) | Policy | If > 0, re-anchor the baseline after this many consecutive throttled samples. Off by default so a sustained throttle keeps alarming until a human decides the new level is acceptable. |

## Method sources

| Claim | Source |
|---|---|
| EWMA/EWMVar recurrence `variance := (1 - alpha) * (variance + diff * incr)` | Finch, T. (2009), *Incremental calculation of weighted mean and variance*, University of Cambridge Computing Service, §9 eq. 143 and the accompanying code form. [PDF](https://fanf2.user.srcf.net/hermes/doc/antiforgery/stats.pdf) |
| This is **not** Welford's algorithm | Welford's is the equal-weight incremental variance with no forgetting factor — Knuth, *The Art of Computer Programming* vol. 2, §4.2.2, cited as reference [1] of the Finch paper. |
| AIMD control law | Chiu, D.-M. & Jain, R. (1989), *Analysis of the increase and decrease algorithms for congestion avoidance in computer networks*, Computer Networks and ISDN Systems 17(1), 1–14. |

## Regulatory context

Jurisdiction: **EU** (and UK as assimilated law). Applies to investment firms engaged in
algorithmic trading; it does not universalise to other jurisdictions.

| Requirement | Source | Bearing on this skill |
|---|---|---|
| "maximum messages limits, which prevent sending an excessive number of messages to order books pertaining to the submission, modification or cancellation of an order" | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 15(1)(d) — pre-trade controls on order entry | **Mandatory, and this skill does not satisfy it.** A latency-derived backoff is advisory and reactive; Article 15(1)(d) requires a hard pre-trade counter against a known limit. See `matching-engine-throttle-and-message-gapping-detection`. |
| Real-time monitoring of algorithmic trading activity for signs of disorderly trading; "Real-time alerts shall be generated within five seconds after the relevant event." | RTS 6, Article 16 and Article 16(5) | Bounds how long a detection may sit unreported. Sweep and alert on `SILENT_THROTTLE` / `ACK_TIMEOUT` well inside five seconds; a sweep interval longer than that cannot meet it. |

Sources: [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589).

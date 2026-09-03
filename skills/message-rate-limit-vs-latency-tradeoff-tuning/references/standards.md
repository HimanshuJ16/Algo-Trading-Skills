# Standards — message-rate-limit-vs-latency-tradeoff-tuning

## What is actually mandatory

| Requirement | Source | Applicability |
|---|---|---|
| An investment firm engaged in algorithmic trading shall carry out pre-trade controls including "**maximum messages limits**, which prevent sending an excessive number of messages to order books pertaining to the submission, modification or cancellation of an order." | Commission Delegated Regulation (EU) [2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj) (**RTS 6**), Art. 15(1)(d) | Mandatory — EU investment firms engaged in algorithmic trading. The UK retains this as assimilated law. |
| "An investment firm shall **immediately include all orders sent to a trading venue** into the calculation of the pre-trade limits referred to in paragraph 1." | RTS 6, Art. 15(2) | Mandatory, same scope. This is why `baseline_session_mps` exists: the limit is per firm/venue flow, not per strategy. |
| Repeated automated execution throttles that disable the strategy after a pre-determined number of repeated executions, until re-enabled by a designated staff member. | RTS 6, Art. 15(3) | Mandatory, same scope. Out of scope for this module — a separate control. |
| Real-time monitoring of all algorithmic trading activity during the hours orders are sent. | RTS 6, Art. 16 | Mandatory, same scope. Pair with `latency-monitoring-percentile-based-slas`. |
| Trading venues must limit the ratio of unexecuted orders to transactions (OTR). | MiFID II Art. 48(6); Commission Delegated Regulation (EU) [2017/566](https://eur-lex.europa.eu/eli/reg_del/2017/566/oj) (**RTS 9**) | Mandatory on **venues**, which then bind members. Venue-set, instrument- and member-category-specific — a separate regime from the MPS ceiling. See `order-to-trade-ratio-fee-penalty-avoidance`. |

RTS 6 mandates that a maximum message limit **exist and block**; it does not prescribe a
numeric value, a safety-buffer percentage, or a repricing delay. Every threshold in this
skill is an engineering choice, not a regulatory one.

## Venue message limits — configure, do not assume

There is **no universal messages-per-second ceiling**. The `exchange_max_mps` default of
`500.0` in `TuningConfig` is a placeholder, not a published standard. Source the real
number from the venue's own session documentation for the specific session, product, and
entitlement in use.

### CME Globex (iLink)

Source: CME Group Client Systems Wiki, [Messaging Controls](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540/Messaging+Controls).

| Fact | Detail |
|---|---|
| Enforcement level | Transactions per second (TPS), measured and enforced **at the iLink session level**. |
| Two-tier thresholds | Exceeding a **Reject** threshold causes subsequent messages to be rejected via Business Level Reject (tag 35-MsgType=`j`) until the rate falls back below it. Exceeding the higher **Terminate** threshold **terminates the iLink session**. Budget against the Reject threshold. |
| Administrative messages | A separate control: rejection above an average of **100 administrative MPS over a three-second window**; CME automatically closes the ports of a session exceeding **200 administrative MPS over a three-second window**. |
| Measurement window | Windowed average, not an instantaneous cap — see the burst caveat in SKILL.md "When NOT to Use". |
| Application thresholds | Published per session/product in the venue's own configuration; CME does not publish a single figure that applies to all sessions. Obtain yours from CME Client Systems Support before configuring `exchange_max_mps`. |

CME also runs a **[Messaging Efficiency Program](https://www.cmegroup.com/globex/files/revisedmep.pdf)**,
which is economic rather than protective: a Globex Firm ID whose Volume Ratio (messaging
score ÷ volume during Regular Trading Hours) exceeds the quarterly Product Group Benchmark
is charged a surcharge of **$1,000 per product group** once exemptions are exhausted.
Staying under the MPS ceiling does **not** keep you under this benchmark — they are
independent constraints, and this module addresses only the former.

### Binance

Source: Binance Open Platform, [REST API limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits).

| Fact | Detail |
|---|---|
| Limit types | `REQUEST_WEIGHT`, `ORDERS`, and `RAW_REQUESTS`, discoverable at runtime from the `rateLimits` array of `GET /api/v3/exchangeInfo`. |
| Order limits | Published as **50 orders per 10 seconds** and **160,000 orders per 24 hours** (spot). |
| Weight limit | `REQUEST_WEIGHT` of **6,000 per minute** for REST and WebSocket API. |
| Feedback headers | `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)` and `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`; breaching returns **HTTP 429**. |
| Modelling caveat | These are **interval quotas** (per 10s / per minute / per day), not a per-second rate. Converting one to an MPS figure for `exchange_max_mps` discards the burst allowance the quota grants — a 50-per-10s quota is not the same constraint as 5 MPS. Prefer a quota-aware limiter for Binance; see `multi-broker-rate-limit-handling`. |

Because these values are venue-configurable and revised over time, treat every figure above
as of the date it was verified (August 2026) and re-check against the primary source before
relying on it.

## Engineering standards enforced by this module

| Metric | Engineering Standard |
|---|---|
| Safety Buffer | The target rate MUST be a fraction of the venue's Reject threshold, in $(0, 100]\%$. The 80% default is a convention, not a published requirement. |
| Reprice Delay | The delay MUST be derived from the budget remaining after co-resident session flow, and MUST be rounded **up** to the reported precision. |
| Feasibility | A configuration whose required delay exceeds the staleness ceiling MUST be reported as `RATE_LIMIT_TARGET_UNREACHABLE`, never as a tuned pass. |
| Adverse Selection | The exposure heuristic MUST be tracked across volatility regimes, and MUST NOT be presented as a currency cost. |

## Quantitative note on the exposure heuristic

`adverse_selection_exposure_score` $= \Delta t_{\text{ms}} \times \sigma_{\text{bps}}$ is an
ordinal ranking heuristic with units of bps-milliseconds. The theoretical framing is
Copeland & Galai (1983), "Information Effects on the Bid-Ask Spread," *Journal of Finance*
38(5), 1457–1469 ([DOI](https://doi.org/10.1111/j.1540-6261.1983.tb03834.x)), which
characterises a resting quote as a free option written to informed traders. Under a
driftless diffusion, the expected absolute mid-price displacement over a staleness window
$\Delta t$ is $\sigma\sqrt{\Delta t}\sqrt{2/\pi}$ — i.e. pick-off exposure grows with
$\sqrt{\Delta t}$, so the linear score **overstates** the marginal penalty of longer
delays. It is retained as a monotone comparator for candidate configurations of a single
symbol; it is not calibrated, and it is not a cost.

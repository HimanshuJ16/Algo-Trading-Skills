# Standards for Iceberg Order Routing

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Support classification | Iceberg support MUST be recorded per **broker-and-exchange pair** as `NATIVE_EXCHANGE` / `BROKER_SIMULATED` / `UNSUPPORTED`. A boolean conflates "the API accepts a display-size field" with "the matching engine holds the reserve". |
| Queue priority | Refill-driven priority loss MUST be modelled in **all** iceberg modes. No venue examined preserves time priority across replenishment. |
| Display quantity | The displayed peak MUST be validated against the venue's minimum display size and lot size before submission, and any adjustment MUST be reported to the caller. |
| Time in force | Native iceberg parameters MUST be validated against venue time-in-force restrictions before submission. The caller's time in force MUST NOT be silently rewritten to satisfy them. |
| Slice randomisation | Synthetic child slices SHOULD vary in size to defeat fixed-size pattern matching. This is a detection-cost measure, **not** concealment — it does not defeat volume-versus-depth detection. |
| Schedule tail | A final synthetic slice below the venue minimum display size MUST be merged into its predecessor rather than sent alone. |
| Determinism | Synthetic slice schedules MUST be reproducible from a recorded seed, for backtest reruns and post-trade investigation. |
| Message rate | The child-order count of a synthetic schedule MUST be bounded and surfaced, for order-to-trade-ratio and message-rate fee purposes. |
| Refill latency | Client-side refill latency MUST be derived from the operator's own telemetry. Venue-internal refill latency MUST be reported as unknown, never as zero. |
| Plan vs execution | Planned child orders MUST be labelled as planned. A routing plan MUST NOT report unsent slices as filled. |

## Venue behaviour (verified against primary sources)

| Venue | Parameter | Documented behaviour | Source |
|---|---|---|---|
| CME Globex | tag 1138 `DisplayQty` | On replenishment "the Display Quantity order's priority is refreshed to be the lowest of the remaining orders at the price level (order is placed at the end of the queue)." A resting display-quantity order cannot be modified to a non-display-quantity order, or vice versa. Minimum display quantity is product-specific. | [CME Globex Matching Algorithm Steps](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457218521) |
| Nasdaq | Reserve Order (Equity 4, Rule 4703(h)) | "At the time of entry, the displayed size of such an order must be one or more normal units of trading; an order with a displayed size of a mixed lot will be rounded down to the nearest round lot." On replenishment, "a new timestamp is created for the replenished portion of the order each time it is replenished from reserve, while the reserve portion retains the time-stamp of its original entry." | [SEC Release approving SR-NASDAQ amendments to Equity 4 §4703(h)](https://www.federalregister.gov/documents/2021/02/18/2021-03214/self-regulatory-organizations-the-nasdaq-stock-market-llc-order-approving-a-proposed-rule-change-to) |
| Deutsche Boerse T7 (Xetra / Eurex) | Iceberg peak volume | "Minimum peak value and minimum overall value of iceberg orders are specified per security." A minimum and maximum peak volume may optionally be set to **randomise the peak natively** on replenishment. The new peak "is entered into the book with a new time stamp"; orders at the same limit are executed before it. | [T7 Release 12.0 Market Model — Xetra](https://www.cashmarket.deutsche-boerse.com/resource/blob/3647718/507083ed04de8d13b027202f4660c7df/data/T7_Release_12.0_-_Market_Model%20_Xetra.pdf) |
| Binance Spot | `icebergQty` | Supported on `LIMIT`, `LIMIT_MAKER`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT_LIMIT`. "Any order with an `icebergQty` MUST have `timeInForce` set to `GTC`." | [Binance Spot API REST documentation](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md) |
| Interactive Brokers | TWS Display Size | The Iceberg/Reserve attribute submits a large order in increments while publicly displaying only a specified portion. IBKR documents order types as native or simulated **per product and exchange**; confirm the specific exchange before classifying the venue as `NATIVE_EXCHANGE`. | [IBKR Order Types — Iceberg](https://www.interactivebrokers.com/en/trading/orders/iceberg.php) |

## Regulatory context (EU)

Iceberg and reserve orders are lit orders whose hidden portion sits in a trading venue's order management facility pending disclosure. In the EU this is the pre-trade transparency waiver at **MiFIR (Regulation (EU) No 600/2014) Article 4(1)(d)**, with the qualifying order types and minimum sizes specified in **RTS 1 (Commission Delegated Regulation (EU) 2017/587) Article 8**. RTS 1 Article 8 conditions the waiver on a minimum order size, which for orders other than reserve orders is the venue's own pre-set minimum tradable quantity.

**Not verified here:** the specific numeric threshold Article 8 applies to reserve orders. Confirm it against the current consolidated text of RTS 1 before relying on a figure, and note that MiFIR was amended by the 2024 review — check whether the provision you are relying on is still in force in your jurisdiction.

## Cross-references

- Detection side of the same mechanism: `iceberg-order-simulation-and-detection`
- Venue capability inventory: `broker-order-type-capability-matrix`
- Message-rate and fee consequences: `order-to-trade-ratio-fee-penalty-avoidance`
- Lot and minimum-size handling: `minimum-fill-size-and-lot-rounding-logic`

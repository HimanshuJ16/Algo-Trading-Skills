# Pre-Flight / Sign-off Checklist — post-only-and-maker-taker-fee-optimization

Use this before considering the skill's implementation complete.

## Venue correctness

- [ ] **One venue's spelling per payload.** No payload carries another venue's post-only field (`execInst` on a REST venue, a `POC` time-in-force, `post_only` alongside `timeInForce`). An ignored post-only flag submits a plain limit order that crosses at the taker rate.
- [ ] **Spelling verified against the venue's own docs**, not from memory: Binance spot `type="LIMIT_MAKER"` (no post-only TIF), Binance USD-M futures `timeInForce="GTX"`, Bybit v5 `timeInForce="PostOnly"` (+ mandatory `category`, string `qty`/`price`), Coinbase `order_configuration.limit_limit_gtc.post_only`, Kraken `oflags="post"`, FIX tag 18 = `"6"`.
- [ ] **FIX carries the wire value `6`**, not the label `ParticipateDoNotInitiate`.
- [ ] **Rejection semantics handled for this venue** — synchronous rejection, asynchronous `EXPIRED` update, or cancel. A successful submission response is not treated as proof the order is resting.

## Order safety

- [ ] **Side is validated as an enum.** A free-text side that matches neither BUY nor SELL cannot reach the payload builder.
- [ ] **Marketability bounds are inclusive** — a buy at exactly `best_ask` and a sell at exactly `best_bid` are treated as crossing.
- [ ] **Locked/crossed books are rejected**, not repriced into a price the venue will cancel.
- [ ] **Quantity, prices and tick alignment validated**; non-finite and non-positive values rejected rather than propagated.
- [ ] **Caller-supplied fields cannot overwrite** the post-only flag, the price, or the quantity.
- [ ] **Post-only is NOT applied** to stop-loss, risk-liquidation, hedge or margin-call orders, where execution matters more than the fee.

## Fee accounting

- [ ] **Maker and taker rates come from the account's actual schedule**, not a default. Confirmed that a differential exists at this tier at all.
- [ ] **Signed, unclamped differential** — an inverted schedule reads as a negative number and warns.
- [ ] **Taker counterfactual is priced at the touch** the order would have crossed against, not at the order's own limit.
- [ ] **Estimates are never accumulated.** Realized totals move only on reported fills, on filled quantity.
- [ ] **Fills are deduplicated** by the venue's execution id before they accrue.
- [ ] **Fee savings are not presented as execution savings.** Adverse selection and non-fill risk are accounted for separately.

## Operational

- [ ] **Rejections are classified, not retried in a loop.** Either an explicit taker order or a drop; order-to-trade ratio monitored.
- [ ] **Automated testing:** run `python -m unittest discover -s skills/post-only-and-maker-taker-fee-optimization/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

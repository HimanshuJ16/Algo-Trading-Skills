# Workflows for Multi-Order Netting Before Routing

The full procedure behind `SKILL.md`. Each step names the failure it prevents.

## 0. Assemble the batch and validate it as a whole

1. Collect the internal orders for **one symbol** within the batching window. The window is a policy choice: longer windows net more and delay more, and a strategy whose alpha decays inside the window is paying for the netting in slippage rather than in fees.
2. Validate every order against the batch, not just against itself:
   - `symbol` must equal the quote's symbol.
   - `order_id` must be unique within the batch.
   - `side` must resolve to `BUY` or `SELL` after trimming and upper-casing.
   - `quantity` must be a positive integer; `limit_price`, if present, must be positive.
3. Raise on any failure. Do not drop, do not default, do not "handle it downstream".

**Prevents:** a stray symbol netted into the wrong book; a replayed batch double-counting one side; an order with an unrecognised side vanishing between the netting and the router with every total still looking consistent.

## 1. Guard the reference price before anything is matched against it

1. Age the quote: `now - quote.as_of` against `max_quote_age_seconds`. This is an engineering threshold sized from your own tick-to-decision latency, not a figure from any rule.
2. If the quote has no timestamp, record that the age is unverified. Do not treat undated as fresh.
3. If the quote is stale, or the book is crossed (`bid > ask`), cross nothing: return every order to the caller and emit **no** residual order.
4. Treat a locked book (`bid == ask`) as valid — the mid is the touch and the spread saving is zero.

**Prevents:** an internal fill priced outside the current NBBO — the route by which an internal cross becomes a trade-through for a broker-dealer that is itself a "trading center" under Reg NMS — and a "saving" computed from a negative spread. Emitting a residual from a rejected quote is the subtler failure: it keeps the position-sizing decision that the same rejected data produced.

## 2. Compute the mid in exact decimal arithmetic

1. $P_{\text{mid}} = (P_{\text{bid}} + P_{\text{ask}}) / 2$. Division by two terminates, so the mid is exact for any realistic price.
2. Do **not** round it to the quoting increment. Record whether it landed finer than a cent and carry that flag into the audit trail.
3. Keep every downstream fee and saving in the same decimal type.

**Prevents:** a systematic half-cent-per-share transfer to whichever side the rounding favours, and a fill price that cannot be reconciled against a clearing statement. Rule 612 constrains the increments in which orders and quotations are displayed, ranked or accepted — the sub-penny flag is there so the *order-entry* side of that rule is checked separately, not so the execution price gets rounded.

## 3. Partition the batch by limit-price eligibility

1. A buy is crossable where `limit_price is None` or `limit_price >= mid`; a sell where `limit_price is None` or `limit_price <= mid`. Equality is crossable — a limit at the mid is satisfied at the mid.
2. Everything else leaves the netting entirely: excluded from the cross, excluded from the residual, returned with a reason.
3. Aggregate the eligible quantity per side. These, not the submitted totals, are the netting inputs.

**Prevents:** filling a strategy through its own limit; and — the case that is easy to miss — netting an ineligible buy against a sell that consequently never reaches the market at all.

## 4. Match and allocate

1. $Q_{\text{matched}} = \min(Q_{\text{buy}}^{\text{eligible}}, Q_{\text{sell}}^{\text{eligible}})$.
2. Allocate $Q_{\text{matched}}$ across each side by the configured policy:
   - **Pro-rata (default).** $q_i = \lfloor Q_{\text{matched}} \cdot Q_i / \sum Q \rfloor$, then distribute the remaining $Q_{\text{matched}} - \sum q_i$ shares one each to the largest fractional remainders, breaking ties on `order_id`. The result sums to exactly $Q_{\text{matched}}$ and two identical batches allocate identically.
   - **Time priority.** Fill in batch order. Legitimate only where that priority is a written, disclosed policy.
3. Emit one internal fill per order that received a non-zero allocation, all at the mid.

**Prevents:** an allocation that over- or under-fills the matched quantity (plain per-order rounding does both), non-deterministic allocations that cannot be reproduced in a post-trade review, and the silent day-after-day transfer from late strategies to early ones that arrival-order filling produces.

## 5. Classify the cross by beneficial ownership

1. Take the participants that actually received an internal fill — not everyone in the batch.
2. One distinct `beneficial_owner_id` → book transfer. Two or more → reportable cross. Any `None` → unclassified.
3. Treat unclassified as reportable.
4. For a reportable cross in a US NMS stock, hand the execution to the trade-reporting path immediately: FINRA Rule 6380A allows no later than 10 seconds after execution.

**Prevents:** manufacturing a transaction record for a movement that changed no beneficial ownership and reached no market — Rule 5210 requires a member to believe any transaction it reports was a bona fide purchase or sale — and the mirror error of suppressing a genuine execution report because the fill "never left the building". Classifying over the whole batch rather than the matched participants produces a third error: an order that received no fill turning a single-owner transfer into a reportable cross.

## 6. Size and price the residual

1. Residual = eligible dominant-side quantity less matched. Only one side can be non-zero.
2. Identify the contributing orders — those with quantity left after allocation. The set depends on the allocation policy, so compute it from the allocation, not from the batch.
3. Price the residual at the most conservative contributing limit: lowest for a buy, highest for a sell. Emit `MARKET` only when no contributor carried a limit, and flag it.
4. Flag a residual whose contributors span multiple beneficial owners: the external fill will need splitting back across accounts under a written allocation policy.
5. Hand the residual to lot/minimum sizing (`minimum-fill-size-and-lot-rounding-logic`) before dispatch — netting frequently produces odd lots.

**Prevents:** a limit order silently promoted to a market order; a conservative contributor filled through its limit by an aggregate order priced off a more aggressive one; and a bunched fill allocated across accounts by whatever order the fills arrived in.

## 7. Estimate the saving honestly

1. Gross fee saving $= 2 \cdot Q_{\text{matched}} \cdot \text{fee}$ — both sides avoid the venue access fee.
2. Spread saving $= Q_{\text{matched}} \cdot \text{spread}$ — each side is filled half a spread better than the touch it would have crossed.
3. On a reportable cross, subtract the per-matched-share cost that survives internalisation (Section 31, TAF, TRF/clearing). Where it was not supplied, say so rather than reporting gross as net.
4. Record the counterfactual with the number: it assumes both sides would have removed liquidity at the touch. Passive orders would not have paid the spread, and on a maker-rebate venue internalising forgoes the rebate.

**Prevents:** a cost-saving report that is off by roughly half on inter-owner crosses, and a TCA narrative built on a counterfactual nobody wrote down.

## 8. Close the loop

1. Persist the whole report — fills, exclusions, allocation, classification, warnings, and the quote it was computed from. It is the audit artefact for both the best-execution review and any self-trade surveillance query.
2. Feed internalised executions into the best-execution review: a member internalising customer flow reviews order-by-order or regular-and-rigorously (FINRA Rule 5310.09), and "we crossed at the mid" is an input to that review, not a conclusion.
3. Keep venue-level SMP configured regardless (`exchange-self-match-prevention-configuration`). This engine sees one batch; it cannot see the firm's own resting orders or a second venue.

**Prevents:** a netting engine that reduces fees while quietly removing the evidence trail that the fee reduction was compatible with best execution — and a firm that believes pre-routing netting has solved self-matching when it has only solved it for orders that happened to arrive in the same batch.

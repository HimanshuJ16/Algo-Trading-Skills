# Multi-Order Netting Pre-Flight Checklist

## Batch integrity

- [ ] Does every order in the batch carry the same symbol as the quote, with a mismatch raising rather than being netted?
- [ ] Is a repeated `order_id` rejected, so a replayed batch cannot double-count one side?
- [ ] Is an unrecognised `side` rejected rather than filtered out — with a test proving the order does not silently disappear?
- [ ] Are zero, negative, boolean and fractional quantities rejected at the boundary?
- [ ] Is the batching window a deliberate choice, sized against the decay of the fastest strategy in it?

## Reference price

- [ ] Does the quote carry an `as_of` timestamp, so it can actually be aged?
- [ ] Is `max_quote_age_seconds` set from your own tick-to-decision latency, and documented as an engineering threshold rather than a regulatory one?
- [ ] On a stale or crossed quote, does the engine cross nothing **and** emit no residual order?
- [ ] Is a locked book (`bid == ask`) handled as a valid cross at the touch with zero spread saving?
- [ ] Is the mid computed in exact decimal arithmetic and left unrounded?
- [ ] Is a sub-penny mid surfaced, and has the order-entry side been checked separately against the venue's minimum pricing increment?

## Eligibility and allocation

- [ ] Does the engine read each order's `limit_price` before crossing it — buy at or above the mid, sell at or below?
- [ ] Is an ineligible order excluded from the residual as well as from the cross, and returned to the caller intact?
- [ ] Is the allocation policy explicit and disclosed to every book it affects?
- [ ] Does the allocation sum to exactly the matched quantity in whole shares, with a deterministic tie-break?
- [ ] Has anyone checked which strategies systematically receive the mid-price fill over a month, rather than assuming the policy is fair?

## Beneficial ownership

- [ ] Is `beneficial_owner_id` populated wherever it is knowable?
- [ ] Is the classification computed over the participants that actually received a fill?
- [ ] Does unknown ownership fail safe to "reportable" rather than to "transfer"?
- [ ] Is a same-owner netting kept out of the trade-reporting path entirely — no fabricated print for a movement that changed no beneficial ownership and reached no market?
- [ ] Is a cross-owner execution wired to the reporting path, within the applicable deadline (10 seconds for a FINRA member in an NMS stock)?
- [ ] Where the two sides are advisory clients, has the consent regime been confirmed — including that Rule 206(3)-2's blanket consent does not cover a transaction the adviser recommended to both sides?

## Residual routing

- [ ] Does the residual inherit the most conservative limit among its contributing orders?
- [ ] Is an unpriced `MARKET` residual a conscious decision rather than a default?
- [ ] Is a residual bunching several accounts allocated back under a written, pre-disclosed policy?
- [ ] Is the residual passed through lot/minimum-size rules before dispatch?

## Cost reporting

- [ ] Are the costs that survive internalisation (Section 31, TAF, TRF/clearing) subtracted on a reportable cross — or their absence disclosed rather than papered over?
- [ ] Is the counterfactual recorded alongside the saving: both sides assumed to have removed liquidity at the touch?
- [ ] On a maker-rebate venue, has the forgone rebate been accounted for?
- [ ] Does the persisted report retain fills, exclusions, allocation, classification, warnings and the source quote — enough to reconstruct the decision post-trade?

## Controls that netting does not replace

- [ ] Is venue-level self-match prevention still configured for the resting book this engine cannot see?
- [ ] Is cross-venue and cross-batch self-matching covered by surveillance rather than assumed away?
- [ ] Is internalised flow fed into the best-execution review (order-by-order, or regular and rigorous), rather than treated as self-evidently favourable?

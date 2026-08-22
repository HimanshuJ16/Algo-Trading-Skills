# Pre-Flight Checklist — CME Globex Order Entry

## Operator ID (Tag 50 / iLink 3 SenderID) — Rule 576

- [ ] Every order carries an Operator ID of 2–18 characters, alphanumeric plus only the
      non-alphanumeric characters permitted by the advisory notice in force.
- [ ] Whitespace-padded IDs are rejected, not trimmed — the transmitted value must match
      the EFS registration exactly.
- [ ] Operator ID comparisons fold case: CME Operator IDs are not case sensitive.
- [ ] The EFS registration list is reconciled on a schedule, and each ID is recorded as
      individual or team/ATS.

## Manual Order Indicator (Tag 1028) — Rule 536.B.

- [ ] Tag 1028 is set explicitly on every order entry message, never defaulted.
- [ ] Orders produced by an execution algorithm are marked automated (`N`).
- [ ] A team/ATS Operator ID never carries Tag 1028 = `Y`.

## Contract parameters

- [ ] Tick size, Price Band Variation and protection points are loaded per symbol from
      CME's product reference files and refreshed daily.
- [ ] Loading rejects a non-positive tick size and negative band/protection values.
- [ ] No parameter is derived from another — protection points are published, not
      computed from the band.

## Price validation

- [ ] Limit prices are checked for exact tick divisibility using decimal arithmetic, not
      float modulo.
- [ ] Price banding is checked on **one side only**: buys above BRP + PBV, sells below
      BRP − PBV. Deep passive bids and offers are not rejected.
- [ ] The banding reference price is the last trade, else the best bid/offer, else the
      settlement price — and it is fresh.
- [ ] Non-finite or missing market data is rejected on its own terms, not reported as a
      price band breach.

## Market with Protection

- [ ] The protection limit is best offer + protection points for a buy, best bid −
      protection points for a sell.
- [ ] An off-tick protection limit is rounded toward the market (down for a buy, up for a
      sell).
- [ ] Market orders are transmitted without a Tag 44 price; the protection limit is not
      encoded as an order price.
- [ ] Position tracking accounts for residual quantity resting at the protection limit.

## Rejection handling

- [ ] Local rejections are classified by type, not by message string.
- [ ] Operator ID and Tag 1028 failures escalate rather than retry — they are
      configuration faults that will fail identically.
- [ ] Every local rejection is logged with enough detail to reconstruct the decision for
      audit.

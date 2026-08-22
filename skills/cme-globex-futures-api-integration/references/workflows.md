# Workflows for CME Globex Futures Order Entry

All numeric values below are **illustrative placeholders**. Real tick sizes, Price Band
Variations and protection points come from CME's product reference files — see
`references/standards.md`.

## 1. Operator ID registration and assignment (Rule 576)

1. Register every Operator ID in the Exchange Fee System (EFS) through your clearing
   member. Record, per ID, whether it is registered to an **individual** or to a
   **team/ATS**.
2. Map each algorithm instance and each human trader to its assigned ID. A team/ATS ID
   covers the person or identified team of persons on the same shift responsible for
   operating the ATS.
3. Keep the mapping in configuration, not in code, and reconcile it against EFS on a
   schedule — an ID that has been deregistered fails at the gateway, not at build time.
4. Remember that Operator IDs are not case sensitive: two IDs differing only in case are
   the same registration, so the local team/ATS lookup must fold case.

## 2. Contract parameter loading

1. Load per symbol: tick size (minimum price increment), Price Band Variation (PBV), and
   Market-with-Protection points.
2. Treat these as daily-refreshed reference data. PBV is static per product but CME
   revises it; protection points are usually about half the product's non-reviewable
   range, but that relationship is a description, not a formula to compute one from the
   other.
3. Validate on load: a non-positive tick makes every price off-tick, and a zero PBV
   rejects every order priced away from the reference. `ContractSpec` raises
   `ContractSpecError` on both.

## 3. Pre-submission validation, in order

1. **Operator ID (Tag 50)** — 2–18 characters, alphanumeric plus the permitted symbol
   set in force, no whitespace. Checked first: an unregistered ID invalidates the message
   regardless of anything else in it.
2. **Manual Order Indicator (Tag 1028)** — must be stated. If the ID is a team/ATS
   registration, Tag 1028 must be `N`.
3. **Order fields** — side in {BUY, SELL}; order type in {LIMIT, MARKET}; quantity a
   positive integer; account present. An unrecognised order type is rejected, never
   treated as a limit.
4. **Price**, for a limit order:
   - Tick conformance: the price must be an exact multiple of the tick. Check with
     decimal arithmetic — `5000.10 % 0.05` is not 0 in binary floating point.
   - Price band, **one side only**:
     - BUY: reject when `price > BRP + PBV`.
     - SELL: reject when `price < BRP − PBV`.
     - Do *not* reject a bid below the market or an offer above it. Those are ordinary
       passive orders and CME accepts them.
   - BRP is the Banding Reference Price: last transaction, else best bid/offer, else the
     settlement price.

## 4. Market with Protection

1. Compute the protection price limit:
   - BUY: `best offer + protection points`
   - SELL: `best bid − protection points`
2. If it is off-tick, round **toward the market** — down for a buy, up for a sell — so
   rounding tightens protection rather than widening it.
3. Do **not** rewrite the order type. Globex applies protection itself; the order goes
   out as a market order with no Tag 44 price. The computed limit is the client's model
   of where residual quantity will rest, not a price to transmit.
4. Register that residual with position tracking. Quantity unfilled inside the protected
   range rests as a limit order at the limit of that range — a resting order the strategy
   never explicitly placed.
5. If the protection limit falls outside the price band, treat it as a risk signal, not a
   rejection: banding applies to price-based orders and a market order carries no price.

## 5. Handling the outcome

1. Classify local rejections before reacting:
   - Operator ID / Tag 1028 faults are configuration errors. The same order will fail
     identically on retry — escalate, do not loop.
   - Banding and tick faults are price errors. Re-price against a fresh reference and
     resubmit.
   - Market-data faults (missing, stale or non-finite quotes) require a fresh quote first.
2. On the exchange side, process iLink execution reports for fills, partial fills, and the
   resting MWP balance, and reconcile them against the protection limit computed locally.
   A divergence means the local protection points are stale.

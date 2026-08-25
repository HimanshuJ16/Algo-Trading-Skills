# Workflows for Eurex Market Data and Order API

The full client-side procedure for a Eurex derivatives order on T7. Rule citations
and message layouts are in `standards.md`.

## 1. Load reference data before anything else

T7 identifies every tradable object numerically. Resolve from the T7 RDI (or the
RDF files) per instrument:

- `SecurityID` (tag 48) and `MarketSegmentID` (tag 1300).
- The instrument's standard price range table. The product snapshot carries the
  `PriceRangeRules` tables; the instrument snapshot carries the identifier of the
  one that applies, because ranges can depend on expiration.
- The product's `FastMarketPercentage`.
- The contract's minimum price change and multiplier.

A missing price range table is a hard stop for the reasonability pre-check, not a
reason to substitute a plausible number. Report that the check could not be run
and let the caller decide; a fabricated band is worse than an admitted gap.

## 2. Maintain the EMDI book, and know when it is untrustworthy

1. Subscribe to the depth incremental feed for the product and keep the snapshot
   feed available for start-up and recovery. They are separate channels; the
   snapshot's `LastMsgSeqNumProcessed` links back to the incremental feed.
2. Arbitrate the A and B services. They carry identical content on different
   multicast addresses, so a message missing from one is usually present on the
   other. Falling straight to the snapshot feed on every dropped datagram wastes
   the live-live design.
3. Track `MsgSeqNum` per product on the depth incremental feed. A skipped number
   means the local book is no longer a copy of T7's.
4. Refuse to derive a price reference from a book that has an unrecovered gap, is
   crossed, or is stale. T7's own book is never crossed; a crossed local copy means
   the decoder lost state. Answering from it produces a verdict the venue will
   never reach.
5. Derive best bid/ask, mid and depth imbalance from the book. The mid is for
   analytics and display only — it is not the reasonability reference price.

Choose the interface deliberately: EMDI for completeness and latency, MDI when
netted updates and lower bandwidth are acceptable, EOBI when order-by-order detail
is needed. Do not assume MDI's depth or update rate matches EMDI's.

## 3. Validate the order's field domains

Check these before the price, because an out-of-domain field makes every
downstream question meaningless:

- `Side` (54) is 1 or 2, not `"BUY"`/`"SELL"`.
- `OrdType` (40) is 1/2/3/4. A market order has no limit price, so neither the
  minimum price change nor the Price Reasonability Check applies to it — it is
  bounded by the Market Order Matching Range.
- `TimeInForce` (59) is 0/1/3/4/6, with GTC and GTD for standard orders only.
- `TradingCapacity` (1815) on Eurex derivatives is 1, 5 or 6. The cash market's 9
  and 10 do not exist here.
- `PriceValidityCheckType` (28710) is 0, 1 or 2.
- Quantity is a positive integer; price is positive and finite.

## 4. Check the minimum price change

Use exact decimal arithmetic, and check positivity as its own condition — float
modulo reports `-4851.0 % 1.0` as zero and would pass a negative price. FESX
requires whole index points; FGBL requires multiples of 0.01 percent of par.

These are the **on-book** figures. An off-book (TES) standardised futures strategy
uses a finer gradation and is out of this skill's scope.

## 5. Run the Price Reasonability Check locally

Order of operations:

1. If `PriceValidityCheckType` is 0, no check is requested — skip, and record that
   nothing was verified.
2. If the instrument is not in state Continuous, the check does not apply.
3. Determine the reference price:
   - **Standard procedure** when both best prices exist and the spread is within
     the price range: the reference is the opposite-side best price — best ask for
     a buy, best bid for a sell. Where there is no best bid, the instrument's
     smallest allowed limit price substitutes for it.
   - **Non-standard procedure** otherwise, combining the alternative reference
     price (last trade, theoretical, or previous day's settlement) with whichever
     best prices exist, per the table in `standards.md`.
4. Compute `PriceRange = APR + |Reference| × PPR / 100` from the row containing the
   reference price, scaled by `(1 + FastMarketPercentage / 100)` in fast or
   stressed markets. Do not round it.
5. Apply the rejection condition directionally:
   `Buy Limit > Reference + Range` or `Sell Limit < Reference − Range`.
6. If no reference price could be determined, the outcome follows
   `PriceValidityCheckType`: Optional accepts unchecked, Mandatory rejects.

Two things this pre-check cannot do, and must not pretend to: T7 evaluates against
its own book, which leads yours by at least a network hop, so a price on the
boundary can still be rejected; and where the check does not run, T7 may still
apply the Extended Price Range Validation, which has its own table.

If a limit that fails the check is genuinely intended, the venue's path is to
re-send it with the check turned off, not to widen a local band.

## 6. Encode for the wire

- Price: 8-byte signed integer, 8 implied decimals. Refuse anything needing more
  precision rather than rounding it — rounding transmits a price nobody asked for.
- Quantity: 8-byte signed integer, 4 implied decimals.
- Convert through `Decimal`, never through `float`. `int(0.29 * 1e8)` is
  `28999999`.
- Optional fields are initialised to their documented no-value; padding bytes need
  no initialisation.

## 7. Frame the header

- `BodyLen` is the whole message including the `BodyLen` field: 280 bytes for
  template 10138 on a simple instrument in Release 14.0, plus 8 per leg.
- `TemplateID` must be a request that still exists. Everything decommissioned with
  ETI 14.1 on 18 May 2026 is listed in `standards.md`.
- `SenderSubID` is the T7 User ID.
- `MsgSeqNum` increments by exactly one per request, starting from the Session
  Logon as 1 — so the first order is 2. A locally rejected order must not consume
  one, because a gap is rejected and disconnects the session.
- There is no timestamp and no session identifier in the request header.

## 8. Dispatch, and treat the response correctly

1. Attach a `ClOrdID` before dispatch and store it with the order intent. On an
   ambiguous timeout, resolve the original order's state through the venue and
   reuse that identifier. A retry under a fresh identifier is a second position.
2. Stay inside the session's transaction limit (`ThrottleNoMsgs` per
   `ThrottleTimeInterval`, both delivered in the Logon response). Repeatedly
   breaching it consumes the reject/disconnect budget and ends with a
   disconnection.
3. Treat the Execution Report as indicative. The legally binding confirmation is
   the Trade Capture Report (AE) on the trade broadcast, generated per leg for
   complex instruments. Reconcile positions against those.

## 9. Handle a disconnect deliberately

On any disconnect — network, gateway failure, or duplicate session login — T7 mass
cancels the session's quotes and non-persistent orders, with `MassActionReason` 6
or 7. There is no automatic failover and no sequence recovery. Open a new TCP
connection, log on at `MsgSeqNum` 1, reset the request counter, then reconcile
before resuming: what survived is the persistent orders, and their state is what
the venue says it is, not what your last outbound message assumed.

Keep the record of dispatched `ClOrdID` s across the reconnect. A reconnect is
exactly when a duplicate submission is most likely.

## 10. Re-verify on every T7 release

- Field offsets and widths move between releases; only the 24-byte request header
  has held stable.
- Templates get deprecated and then removed, on a published schedule.
- Price range tables and fast-market percentages are exchange parameters that
  change without any release at all — reload them from reference data, do not
  cache them into code.

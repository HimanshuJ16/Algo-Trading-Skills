# Pre-Flight Checklist

## Reference data

- [ ] Are `SecurityID` (tag 48) and `MarketSegmentID` (tag 1300) resolved from the T7 RDI, rather than sending a symbol like `FESX_202609` as the identifier?
- [ ] Is the instrument's standard price range table loaded from the RDI `PriceRangeRules` messages (product snapshot for the tables, instrument snapshot for which one applies)?
- [ ] Is the product's `FastMarketPercentage` loaded, and applied when the market is in a fast or stressed state?
- [ ] Is a missing price range table a reported gap rather than a substituted default band?
- [ ] Are the contract's on-book minimum price change and multiplier taken from the contract specifications, not from an off-book gradation?

## T7 EMDI market data

- [ ] Is the depth incremental feed's per-product `MsgSeqNum` tracked, with gaps detected rather than silently absorbed?
- [ ] Is the A/B live-live pair arbitrated before falling back to the snapshot feed?
- [ ] Is the snapshot feed wired up for start-up and recovery, using `LastMsgSeqNumProcessed` to rejoin the incremental stream?
- [ ] Are best bid/ask, mid price and depth imbalance derived from the book, with the mid treated as analytics only?
- [ ] Is a book that is crossed, stale, or carrying an unrecovered gap refused as a price reference?
- [ ] Is the chosen interface the right one — EMDI un-netted, MDI netted with fewer levels, EOBI order-by-order?

## Price Reasonability Check

- [ ] Is the check directional — buy against the best **ask**, sell against the best **bid** — rather than symmetric against the mid?
- [ ] Is the price range calculated as `APR + |Reference| × PPR / 100` from the reference price, and left unrounded?
- [ ] Is the standard procedure's applicability tested (both best prices present, spread within the range) before the non-standard table is used?
- [ ] Is an alternative reference price supplied — last trade, theoretical, or previous day's settlement — for the non-standard procedure?
- [ ] Is the check skipped outside instrument state Continuous?
- [ ] Is `PriceValidityCheckType` (tag 28710) chosen deliberately: `1` Optional accepts an unverifiable price, `2` Mandatory rejects it?
- [ ] For untriggered stop limit orders, is the order's own stop price used as the reference?
- [ ] Is it understood that a locally passing price can still be rejected, because T7 evaluates against its own book?

## Order fields and encoding

- [ ] Is `Side` (54) sent as 1/2 rather than a string?
- [ ] Is `TradingCapacity` (1815) one of 1, 5, 6 — the derivatives domain, not the cash one?
- [ ] Are `OrdType` (40), `TimeInForce` (59) and `ProductComplex` (1227) within their documented domains?
- [ ] Is the minimum price change checked in exact decimal arithmetic, with non-positive prices rejected explicitly rather than passed by a float modulo?
- [ ] Are prices encoded as 8-byte signed integers with 8 implied decimals, and quantities with 4, refusing rather than rounding excess precision?
- [ ] Do all values reach the encoder as `Decimal` or `str`, never as `float`?

## ETI session and framing

- [ ] Is `TemplateID` one that still exists — 10138/10139/10140/10141/10142, not one of the ten removed with ETI 14.1 on 18 May 2026?
- [ ] Is `BodyLen` the whole message including the `BodyLen` field (280 for 10138 on a simple instrument in Release 14.0, plus 8 per leg)?
- [ ] Are the offsets taken from the message reference for the release being certified, rather than copied from an older one?
- [ ] Does `MsgSeqNum` increment by exactly one per request, starting from the Session Logon as 1?
- [ ] Does a locally rejected order consume no sequence number?
- [ ] Is the sequence counter reset to the post-logon value after every reconnect, given that ETI has no sequence recovery?
- [ ] Is the session's transaction limit (`ThrottleNoMsgs` / `ThrottleTimeInterval`) respected, and the reject/disconnect limit monitored?
- [ ] Is TLS 1.3 in use, TLS 1.2 having been decommissioned for ETI LF and FIX LF in production on 27 April 2026?

## Order lifecycle and recovery

- [ ] Is a `ClOrdID` attached and stored with the order intent *before* dispatch?
- [ ] On an ambiguous timeout, is the original order's state resolved through the venue rather than resubmitted under a fresh identifier?
- [ ] Do dispatched `ClOrdID` records survive a reconnect?
- [ ] Is it handled that quotes and non-persistent orders are mass-cancelled on disconnect and on duplicate session login (`MassActionReason` 6 and 7)?
- [ ] Are positions reconciled against Trade Capture Reports (AE), treating Execution Reports as indicative?
- [ ] Is `ready_to_send` understood as "passed local validation", never as "the exchange has it"?

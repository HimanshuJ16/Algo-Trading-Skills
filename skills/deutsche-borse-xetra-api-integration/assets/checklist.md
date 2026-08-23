# Pre-Flight Checklist

## Reference data

- [ ] Is the RTS 11 liquidity band (1–6) loaded per instrument from venue reference data, rather than inferred from price?
- [ ] Is a missing liquidity band a hard stop rather than a default?
- [ ] Are `SecurityID` (tag 48) and `MarketSegmentID` (tag 1300) resolved, rather than sending the ISIN as the identifier?
- [ ] Are the Xetra carve-outs handled — band 11 (WM table S) for non-EU/non-Swiss home markets, and band 6 for share-only ETFs?

## Tick size

- [ ] Is the tick looked up in the full RTS 11 Annex matrix (19 price bands × 6 liquidity bands)?
- [ ] Is the multiple check done in exact decimal arithmetic, never float modulo?
- [ ] Are non-positive prices rejected explicitly, rather than relying on a modulo check that passes them?
- [ ] Is the band table refreshed before the first Monday of April, and are stored limit prices re-validated after changeover?

## ETI message fields

- [ ] Is `Side` (54) sent as 1/2 rather than a string?
- [ ] Is `TradingCapacity` (1815) one of 1, 5, 6, 9, 10 — and not a letter from the separate `Account` field?
- [ ] Is `OrderOrigination` (1724) set to 5 only for genuine direct/sponsored access flow?
- [ ] Does every short code travel with its qualifier (22 Algo / 24 Human)?
- [ ] Are the corresponding long codes uploaded to the venue with a covering valid-from date, and are TR160/TR161/TR166 clean?
- [ ] Are prices encoded as 8-byte signed integers with 8 implied decimals, refusing rather than rounding excess precision?

## Header and session

- [ ] Is `BodyLen` the whole message length including the `BodyLen` field (24 + body)?
- [ ] Is the header packed little endian at the documented offsets?
- [ ] Does `MsgSeqNum` advance by exactly one per request sent, and never on a locally rejected order?
- [ ] Is one engine instance bound to one session's sender thread?

## Release currency

- [ ] Is the order template one that still exists in the target release — 10138/10139 rather than the deprecated 10100/10125?
- [ ] Have body offsets and field widths been re-read from the Cash Message Reference for this exact release?
- [ ] Has the integration been re-certified in simulation ahead of the release's production start date?

## Response handling

- [ ] Are rejections classified into retryable vs terminal before any resubmission?
- [ ] Is `SessionRejectReason` 104 ("result of transaction unknown") reconciled against order state rather than treated as a plain failure?

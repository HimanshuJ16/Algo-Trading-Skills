# Workflows for Deutsche Börse Xetra API Integration

## 1. Reference data before anything else

1. Load, per instrument: `SecurityID` (tag 48), `MarketSegmentID` (tag 1300), and
   the **RTS 11 liquidity band (1–6)** from the venue Reference Data file. The ISIN
   is a human-facing label; the wire is numeric.
2. Treat a missing liquidity band as a hard stop, not a default. Guessing a band
   guesses the tick size, and guessing coarse accepts off-tick prices while guessing
   fine rejects valid ones.
3. Watch for the two Xetra carve-outs: liquidity band 11 (WM tick size table S) for
   instruments whose home market is outside the EU and not Switzerland, and ETFs
   whose underlyings are exclusively tick-regime shares, which sit in band 6.

## 2. Annual tick size changeover

1. ESMA publishes ADNT figures annually; venues apply the resulting bands from the
   **first Monday of April** (RTS 11 Art. 3(4) as amended by (EU) 2023/960).
2. Refresh the band table before that Monday's start phase, not after.
3. Expect Xetra to delete resting orders whose limits no longer comply, with reason
   "Invalid Limit Price" or "Invalid Stop Limit Price". Plan to re-price and
   re-submit; do not assume the book carried over.
4. Re-run any stored or scheduled limit prices through the tick check after the
   changeover — a price that was on-tick in March may not be in April.

## 3. Order construction

1. Validate field domains first: `Side` 1/2, positive integer quantity, positive
   price, `TradingCapacity` in {1, 5, 6, 9, 10}. Reject rather than coerce.
2. Set the MiFID fields that apply. `OrderOrigination` (1724) = 5 only for direct
   or sponsored access flow. If you send an `ExecutingTrader` (25123) short code,
   send its qualifier (25124: 22 Algo, 24 Human) with it; the same pairing applies
   to `PartyIdInvestmentDecisionMaker` and its qualifier (21222).
3. Confirm the short codes were uploaded to the venue's short/long code database
   with a valid-from date covering the trading day. Unresolved combinations surface
   in the TR160 / TR161 / TR166 reports and are a basis for enforcement.
4. Look up the RTS 11 tick for (price band, liquidity band) and verify the limit is
   an exact multiple, in decimal arithmetic.
5. Encode the price as an integer with 8 implied decimals. Refuse anything needing
   more precision rather than rounding it.

## 4. Header framing and sequencing

1. Build the 24-byte request header: `BodyLen`, `TemplateID`, `NetworkMsgID`,
   `Pad2`, `MsgSeqNum`, `SenderSubID`, little endian.
2. Set `BodyLen` to the **whole message length including the BodyLen field**
   (24 + body), not the body length alone.
3. Advance `MsgSeqNum` by exactly one per request actually sent. A locally rejected
   order must not consume one — a gap is a session-level fault.
4. Drive one engine instance from one session's sender thread. Sharing an instance
   across threads interleaves sequence numbers.

## 5. Template lifecycle

1. Prefer New Order Single or Multi Leg (10138 / short layout 10139). The R14.0
   change log schedules New Order Single (10100 / 10125) and Replace Order Single
   (10106 / 10126) for decommissioning with ETI 14.1 in mid-2026.
2. Re-read the Cash Message Reference on every release upgrade. Only the request
   header has held stable; body offsets and field widths have not — `OrderQty`
   widened from 4 to 8 bytes between Release 5.0 and Release 14.0.
3. Re-certify in simulation before each release's production start date.

## 6. Response handling

1. Rejections arrive on the FIX Reject (3) message: code in `SessionRejectReason`
   (373), text in `VarText` (30355).
2. Classify before reacting. Throttle rejection (100) and "service temporarily not
   available" (102) are retryable; "client order ID not unique" (10002) and
   pre-trade risk limit breaches (10004, 10005) are not, and retrying them either
   duplicates an order or hammers a limit that will not clear on its own.
3. "Result of transaction unknown" (104) is the dangerous one: the order may or may
   not have been accepted. Reconcile order state before resubmitting — do not treat
   it as a plain failure.
4. Off-tick limits appear as `ExecRestatementReason` (378) 238 "Invalid limit
   price" or 243 "Invalid stop price". Treat these as a defect in your tick table
   or liquidity band data, not as something to retry.

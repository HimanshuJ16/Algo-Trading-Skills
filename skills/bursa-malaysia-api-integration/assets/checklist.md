# BTS2 Pre-Production Checklist

Sign off before the first live order. Evidence for every item is in
`references/standards.md`.

## Session configuration

- [ ] FIX engine `BeginString` reads `FIXT.1.1` — checked in the engine's own config,
      not assumed from ours.
- [ ] `DefaultApplVerID(1137)` is `8` (FIX50SP1) on the Logon message.
- [ ] `TargetCompID(56)` is the CompID **Bursa assigned**, not `FIXTRADER` /
      `FIXNEGDEAL` (those are connection types).
- [ ] `HeartBtInt(108)` is between 10 and 60, and session timers are driven by the value
      BTS2 **acknowledged**, not the value we requested.
- [ ] `Username(553)` (≤30 chars) and session password (≤12 chars) are supplied from a
      secrets store, never from source control, and never appear in logs.
- [ ] Password expiry is tracked, with a rotation path via `NewPassword(925)` at logon
      before the current password expires.
- [ ] Logon retry is **disabled or capped below 3 attempts** — three failures lock the
      account and only Bursa operations can unlock it.
- [ ] Certification and Production credentials, endpoints and broker codes live in
      separate configuration, with no path by which a test run reaches Production.

## Connection type and entitlements

- [ ] The connection type matches the business being sent: `FIXTRADER` for
      Normal / Odd-Lot / Buy-In, `FIXNEGDEAL` for Direct Business Transactions.
- [ ] The broker code is 6 digits, and its 4th digit matches the connection type —
      `9` ordinary, `1` market maker (Normal board only), `2` DBT.
- [ ] Market-maker order flow uses a `1` branch code and only the Normal board.
- [ ] Short-selling entitlements (RSS / IDSS / PSS) are confirmed with Bursa and
      compliance, and the correct `Side(54)` value is used — never a plain `SELL`.
- [ ] Instrument-level short-sell eligibility and the daily maximum RSS traded
      percentage are sourced from reference data, not assumed.

## Order construction

- [ ] Instruments are addressed by `SecurityID(48)` with `SecurityIDSource(22)=99`, and
      the security master maps internal symbols to Bursa stock codes.
- [ ] `SecuritySubType(762)` carries the correct board for every order.
- [ ] `Account(1)` is a valid 9-digit CDS account, left-padded with zeros.
- [ ] `OrderRestrictions(529)` is populated on every order, and algorithm-generated
      orders carry `E`.
- [ ] `ClOrdID(11)` generation is ≤20 characters and unique across the trading day —
      **we** guarantee this; BTS2 does not check it.
- [ ] Price/trigger rules are enforced per OrdType: no Price on Market or Market-at-Best,
      TriggerPrice on Stop and Stop Limit, Price only on Limit and Stop Limit.
- [ ] If All-or-None (`ExecInst=G`) is used, `MinQty(110)` equals the total quantity.

## Order lifecycle

- [ ] A cancel request is treated as a **request**: the order stays in `PENDING_CANCEL`,
      keeps its risk budget, and is not reported flat until the venue confirms.
- [ ] The cancel request carries its **own** ClOrdID, unique amongst order ClOrdIDs.
- [ ] Order Cancel Reject (MsgType=9) returns the order to a working state, and
      `Text(58)` is logged (CxlRejReason is almost always just `99`).
- [ ] A fill arriving during `PENDING_CANCEL` is applied, not discarded — tested
      explicitly against our own integration, not only in the skill's unit suite.
- [ ] `ExecID(17)` deduplication is in place, and a replayed report has been shown to
      leave cumulative quantity and average price unchanged.
- [ ] Overfills are refused and alerted, not absorbed.
- [ ] Unsolicited reports are handled: supervisor cancels, native-protocol amendments
      (which arrive with **no ClOrdID** — matched on `OrderID(37)`), IOC/FoK remainders,
      and GT expiries.
- [ ] `OrderID(37)` renumbering after an amendment is handled, using
      `SecondaryOrderID(198)` to relink.

## Verification and certification

- [ ] `python -m unittest discover -s skills/bursa-malaysia-api-integration/scripts`
      passes.
- [ ] The certification-log arithmetic reconciles: 5,000 @ (2,000 × 6.10 + 3,000 × 6.20)
      gives AvgPx 6.16, matching the exchange's `AvgPx(6)`.
- [ ] Cumulative filled quantity and average price reconcile against Bursa's own
      end-of-day trade records.
- [ ] Bursa's FIX certification test cases have been executed against BTS2 FIX CERT and
      the logs retained.
- [ ] The Order Management specification version in use has been re-checked against
      Bursa's Documents and Guides page (this skill was written against v1.15, and BTS3
      is on Bursa's roadmap).

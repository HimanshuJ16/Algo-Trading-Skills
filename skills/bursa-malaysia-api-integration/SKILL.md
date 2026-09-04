---
name: bursa-malaysia-api-integration
description: >-
  Use when routing orders to Bursa Malaysia BTS2 through Bursa Direct Access over
  FIXT.1.1 and FIX 5.0 SP1; enforces the client order id uniqueness BTS2 does not check
  itself, and models the cancel request and execution report lifecycle.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: broker-integration, bursa-malaysia, fix-protocol, bts2, order-lifecycle
  brokers_frameworks: "Bursa Malaysia BTS2 FIX (Order Management); Bursa Direct Access (BDA); FIXT.1.1 / FIX 5.0 SP1; Nasdaq X-stream"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building order routing to Bursa Malaysia's BTS2 platform through
**Bursa Direct Access (BDA)** — the open interface Participating Organisations use to
connect a customised OMS to the exchange over FIX — and you need the order lifecycle
and the venue's pre-send requirements modelled correctly.

BTS2 runs on Nasdaq's X-stream. Order entry speaks **BeginString FIXT.1.1** with
**DefaultApplVerID(1137)='8' = FIX50SP1**: the transport version and the application
version are two different fields, and conflating them is a logon failure. Bursa issues
FIX sessions as one of two **connection types** — `FIXTRADER` for the Normal, Odd-Lot
and Buy-In boards, `FIXNEGDEAL` for Direct Business Transactions and off-market
business — each with broker codes in its own format.

## When NOT to Use

- **As a FIX gateway.** `scripts/bursa_malaysia_api_integration.py` is an in-memory
  order-state machine with a *simulated* session layer. It opens no sockets, encodes
  and decodes no FIX messages, assigns no sequence numbers, authenticates against
  nothing, and persists nothing across restarts. `connect()` sets a flag. Use a real
  FIX engine for transport and this module to model the lifecycle and drive state from
  the reports that engine decodes.
- **For Direct Business Transactions.** DBT / off-market business rides a FIXNEGDEAL
  connection, and BTS2 handles privately negotiated trades through Trade Capture
  Reporting (MsgType=AE) rather than the order path modelled here. The engine refuses
  order entry on a FIXNEGDEAL connection rather than guessing a board code.
- **For market data.** Order entry only. BTS2's market-data FIX interface is a separate
  specification with its own message set.
- **For order modification.** Order Cancel/Replace (MsgType=G) is not modelled. Note
  the trap if you implement it yourself: BTS2 treats Cancel/Replace as the *complete
  new order state*, so fields you omit are reset rather than retained, and a price
  change or quantity increase forfeits queue priority.
- **As a substitute for certification.** Passing these unit tests is not BTS2 FIX
  certification and grants no production access.

## Prerequisites

- Python 3.10+ and a real FIX engine for transport.
- Participating Organisation status, and a BDA/BTS2 FIX connection of the right
  **connection type** for the business you intend to send.
- Connectivity: for the BTS2 FIX Certification (UAT) environment, a site-to-site IPsec
  VPN set up by submitting the **BTS2-A1 form** to Bursa's IT Infrastructure team;
  production connectivity via Bursa's own connectivity or co-location services.
- Session credentials: `SenderCompID(49)`, the assigned `TargetCompID(56)`,
  `Username(553)` and a session password. Passwords expire and must be rotated —
  programmatically at logon time via `NewPassword(925)`, or manually through Bursa.
- The **broker code(s)** issued with your connection, and the 9-digit CDS account(s)
  you will trade for.
- BTS2 FIX certification for the message set you use, before production.

## Workflow

1. **Configure the session, and let it fail at start-up.** `BursaConfig` validates
   everything BTS2 checks at logon: `BeginString` must be `FIXT.1.1` and
   `DefaultApplVerID` must be `'8'`; CompIDs and username are capped at 30 characters
   and the password at 12; and `heartbeat_interval` must be 10–60. That heartbeat range
   matters more than it looks — out-of-range values are **not rejected**, the gateway
   silently answers with the last valid value (or 60 on the first logon of the day),
   after which your staleness timers and the venue's disagree.
2. **Match the broker code to the connection type before you send anything.** A broker
   code is 3-digit firm code + 3-digit branch code, and the branch code's first digit
   declares what the order is: `9` ordinary, `1` market maker, `2` Direct Business
   Transaction. Codes `9`/`1` are issued with FIXTRADER, `2` with FIXNEGDEAL. The
   engine enforces this on `Environment.PRODUCTION` only, because Bursa documents these
   formats as production-specific — Certification issues its own codes.
3. **Do not retry a failed logon.** BTS2 locks the account after a defined number of
   failed authentications (default 3), and unlocking requires Bursa operations to reset
   it and issue a new password. Report failures through `record_logon_failure()`; once
   the budget is spent, `connect()` raises rather than spending the attempt that costs
   you the session.
4. **Build the order in BTS2's terms, not the ticker's.** Instruments are
   `SecurityID(48)` — the marketplace-assigned stock code, e.g. `"1082"`, `"1818WA"` —
   with `SecurityIDSource(22)=99`, plus `SecuritySubType(762)` for the board (`NM`,
   `OD`, `BI`). `Account(1)` is the 9-digit CDS account, left-padded with zeros.
   `OrderRestrictions(529)` is **mandatory tagging**, and an algorithm-generated order
   carries `E`. Short sales use their own `Side(54)` values — `5` RSS, `I` IDSS, `V`
   PSS — never a plain `SELL`.
5. **Generate ClOrdIDs that fit, and never reuse one.** ClOrdID(11) is String(20); a
   bare `uuid4()` string is 36 characters and will not fit. More importantly, **BTS2
   does not check ClOrdID uniqueness** — when an action targets a duplicated ClOrdID
   only the last order is affected. `submit_order()` refuses a duplicate rather than
   overwriting a working order's fill state; use `new_client_order_id()`.
6. **To cancel, send the request — then wait.** `cancel_order()` sends an Order Cancel
   Request (MsgType=F) with **its own unique ClOrdID** and moves the order to
   `PENDING_CANCEL`. BTS2 accepts the request only if the order can be withdrawn
   without executing, so **the order is still live and can still fill.** Do not treat
   `PENDING_CANCEL` as cancelled, do not release its risk budget, and do not reuse
   its ClOrdID.
7. **Resolve the cancel on the venue's answer.** An ExecutionReport with
   ExecType=Canceled → `confirm_cancel()`. An Order Cancel Reject (MsgType=9) →
   `reject_cancel()`, which returns the order to `PARTIALLY_FILLED` or `NEW` because a
   rejected cancel means the order was never cancelled. BTS2 returns
   CxlRejReason(102)=99 (Other) for most rejections and puts the real reason in
   Text(58) — log it.
8. **Apply fills idempotently.** `simulate_execution_report(...)` takes `filled_qty` as
   **LastQty(32)** — this fill, not CumQty(14). **Always pass `exec_id`.** After a
   sequence gap BTS2 resends application messages, and ExecID(17) is the only thing
   distinguishing a resent report from a new one.
9. **Expect messages you did not ask for.** BTS2 sends unsolicited execution reports:
   supervisor cancellations, orders amended or cancelled through the native protocol
   (which arrive with no ClOrdID set), IOC/FoK remainders, and Good-Till orders expired
   by date or dynamic limit. `confirm_cancel()` accepts an unsolicited cancel and
   `expire_order()` handles expiry; a client that ignores them drifts out of sync with
   the book.
10. **Treat a refused overfill as an incident.** A report that would push cumulative
    quantity past the order quantity is rejected and logged at ERROR. A duplicate
    escaped deduplication or the venue sent something impossible — reconcile before
    trading on the position.

> Full session and order-routing sequence: see `references/workflows.md`.
> Protocol, symbology, broker-code and enumeration evidence: see `references/standards.md`.
> Pre-production readiness checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Putting `FIX.5.0SP1` in BeginString.** BTS2's BeginString(8) is `FIXT.1.1`; FIX 5.0
  SP1 is the *application* version, carried in DefaultApplVerID(1137)/ApplVerID(1128)
  as the value `8`. A FIX engine configured with `BeginString=FIX.5.0SP1` never logs on.
- **Treating `FIXTRADER` / `FIXNEGDEAL` as TargetCompID values.** They are Bursa's
  **connection types**, recorded as the session's user type. TargetCompID is the CompID
  Bursa assigns you (Bursa's own published certification log shows `56=XSTRMO`). Hard-
  coding `FIXTRADER` into tag 56 produces a session that will not establish.
- **Calling FIXNEGDEAL a dark pool.** It carries Direct Business Transactions and
  off-market business — bilaterally negotiated trades — not an anonymous dark book. Its
  orders take the `2` branch-digit broker code, and privately negotiated trades run
  through Trade Capture Reporting.
- **Assuming BTS2 will catch a duplicate ClOrdID.** It explicitly does not check
  uniqueness. A reused ClOrdID makes the venue's reports ambiguous — an action affects
  only "the last order identified by ClOrderId" — and locally overwrites the fill state
  of the order still working under that ID.
- **Generating ClOrdIDs longer than 20 characters.** ClOrdID(11) is String(20). A UUID
  string does not fit, and neither does a verbose `strategy-name-timestamp-seq` scheme.
- **Treating an Order Cancel Request as a cancellation.** MsgType=F *requests*
  cancellation of the remaining quantity, and BTS2 accepts it only if the order can be
  withdrawn without executing. Marking the order cancelled locally and then discarding
  later reports as "terminal" silently loses real fills — in the test suite that is a
  2,000-share fill vanishing from a 5,000-share order.
- **Reusing the order's ClOrdID for its cancel request.** The cancel request is a
  separate entity with its own ClOrdID, which must be unique amongst the ClOrdIDs
  assigned to orders and replacement orders.
- **Applying execution reports without ExecID deduplication.** Resend Request
  (MsgType=2) recovery is normal FIX session behaviour and it replays application
  messages. A handler with no ExecID memory double-counts every replayed fill.
- **Sending Price(44) on a Market or Market-at-Best order.** BTS2 does not use Price
  for them. Some gateways reject it; others ignore it and fill you at a price you never
  specified. The mirror image: a Stop order carries TriggerPrice(1102) and *no* Price,
  while a Stop Limit carries both.
- **Sending a short sale as a plain `SELL`.** Bursa distinguishes Regulated Short Sell,
  Intraday Short Sell, Permitted Short Sell and Proprietary Day Trading with their own
  Side(54) values. Using `2` misdeclares the trade, and the entitlement to use those
  values is a separate question this module cannot answer for you.
- **Omitting OrderRestrictions(529).** It is a required field, capped at 5 characters,
  and `E` is the value that declares an order algorithm-generated. Leaving it off gets
  the order rejected; leaving off `E` misstates the order's provenance to the exchange.
- **Retrying a failed logon in a loop.** Three failed authentications lock the account,
  and only Bursa operations can unlock it. An automated reconnect turns a wrong password
  into a lost trading day.
- **Setting a heartbeat outside 10–60 seconds.** The gateway does not reject it, it
  substitutes its own value — so your engine believes it agreed one interval while the
  venue is running another.
- **Validating quantity with `qty <= 0` alone.** NaN fails every comparison, so a NaN
  quantity passes that check and gets routed.
- **Assuming this module manages FIX sequence numbers.** It does not. Sequence
  assignment, gap fill, Resend Request handling and persistence across restarts belong
  to your FIX engine.
- **Testing against Certification and assuming Production behaves identically.** Broker
  code formats, endpoints, CompIDs and credentials all differ between the two.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/bursa-malaysia-api-integration/scripts`
- Assert the cancel-race invariant in your own integration: send a cancel request,
  deliver a fill before any venue answer, and confirm the fill is applied and the order
  remains `PENDING_CANCEL`. This is the defect that costs money; test it directly.
- Replay a captured ExecutionReport twice with the same ExecID and confirm cumulative
  quantity and average price are unchanged.
- Reconcile a full fill sequence against Bursa's own published certification log:
  5,000 shares of SecurityID 1082 filled 2,000 @ 6.10 then 3,000 @ 6.20 must produce
  AvgPx = 6.16, matching the exchange's AvgPx(6).
- Assert that a duplicate ClOrdID is refused and that the original order's fill state
  survives the refusal.
- Assert that a broker code with the wrong branch digit for the connection type is
  rejected in `Environment.PRODUCTION` and permitted in `Environment.CERTIFICATION`.
- Confirm your FIX engine's configured BeginString is `FIXT.1.1` and its
  DefaultApplVerID is `8` — read it out of the engine's own config, not the code.
- Run Bursa's FIX certification test cases against the BTS2 FIX CERT environment before
  production. Unit tests do not substitute for certification.

## Related Skills

- `borsa-istanbul-api-integration`
- `fix-protocol-session-management-across-venues`
- `broker-api-idempotent-cancel-requests`
- `order-placement-idempotency`
- `singapore-exchange-sgx-api-integration`
- `broker-failover-secondary-account-routing`

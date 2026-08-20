# Workflows — Bursa Malaysia BTS2 Integration

Field-level evidence for every claim here is in `standards.md`.

## 0. Onboarding and certification

1. Establish Participating Organisation status and request BDA/BTS2 FIX access, choosing
   the **connection type** that matches the business you will send: `FIXTRADER` for
   Normal / Odd-Lot / Buy-In board order flow, `FIXNEGDEAL` for Direct Business
   Transactions and off-market business. You may need both; they are separate sessions.
2. For the Certification (UAT) environment, submit the **BTS2-A1 form** to Bursa IT
   Infrastructure to set up the site-to-site IPsec VPN. Allow at least five working
   days. Bursa returns your private address range, a sample router configuration and a
   pre-shared key.
3. Record what Bursa issues you, and keep Certification and Production values in
   separate configuration stores: `SenderCompID`, `TargetCompID`, `Username`, session
   password, endpoint, and the **broker code(s)** for your connection type.
4. Run Bursa's FIX certification test cases against BTS2 FIX CERT. Order state
   transitions (New, Partially Filled, Filled, Cancelled, Rejected, Expired), cancel
   rejects and amendment flows are all covered there. Unit tests are not certification.

## 1. Session establishment

```
Client                                  BTS2 (X-stream)
  |  Logon (35=A)                             |
  |    8=FIXT.1.1                             |
  |    98=0  108=<10..60>                     |
  |    1137=8   553=<user>  554=<password>    |
  |------------------------------------------>|
  |                       Logon (35=A) ack    |   success
  |<------------------------------------------|
  |                       Logout (35=5)       |   failure — Text(58) says why
  |<------------------------------------------|
```

Rules that bite:

- **BeginString is `FIXT.1.1`.** FIX 5.0 SP1 travels in `DefaultApplVerID(1137)=8`.
- **HeartBtInt must be 10–60.** Outside that, BTS2 does not reject — it answers with the
  last valid value, or 60 on the first logon of the day. Your session timers must be
  driven by what the venue *acknowledged*, not what you asked for.
- **Three failed logons lock the account**, and only Bursa operations can unlock it. Do
  not put `connect()` in an automatic retry loop. Feed failures to
  `record_logon_failure()`; after the budget is spent, `connect()` raises.
- **Passwords expire.** Rotate at logon with `NewPassword(925)`, while the current
  password is still valid, or the rotation has to go through Bursa manually.
- One FIX session maps one-to-one to a native BTS2 session, and the same user cannot be
  logged on via both protocols at once.

## 2. Building a New Order Single (35=D)

```python
from bursa_malaysia_api_integration import (
    Board, BursaConfig, BursaMalaysiaFixEngine, ConnectionType, Environment,
    FIXOrder, OrderCapacity, OrderSide, OrderType, TimeInForce,
)

config = BursaConfig(
    sender_comp_id="0031",
    target_comp_id="XSTRMO",        # assigned by Bursa — NOT "FIXTRADER"
    host="10.1.117.10",
    port=9999,
    username="BMIOE031901",
    password=os.environ["BTS2_SESSION_PASSWORD"],
    connection_type=ConnectionType.FIXTRADER,
    broker_code="031901",
    environment=Environment.CERTIFICATION,
    heartbeat_interval=30,
)

engine = BursaMalaysiaFixEngine(config)
engine.connect()

order = FIXOrder(
    security_id="1082",             # SecurityID(48), SecurityIDSource(22)=99
    board=Board.NORMAL,             # SecuritySubType(762)
    side=OrderSide.BUY,             # Side(54)
    order_type=OrderType.LIMIT,     # OrdType(40)
    quantity=5000,
    account="1002",                 # padded to 000001002 on the wire
    order_restrictions="E",         # OrderRestrictions(529): E = Algorithmic
    price=6.10,
    time_in_force=TimeInForce.DAY,
    order_capacity=OrderCapacity.PRINCIPAL,
)
cl_ord_id = engine.submit_order(order)
```

The engine refuses, before anything reaches the wire:

| Refusal | Why |
|---|---|
| ClOrdID longer than 20 characters | ClOrdID(11) is String(20) |
| Duplicate ClOrdID | BTS2 does not check uniqueness; the duplicate would discard the live order's fills |
| Non-finite or non-positive quantity | NaN passes a bare `qty <= 0` check |
| Limit without Price, Market **with** Price | Price(44) is not used on Market / Market-at-Best |
| Stop without TriggerPrice, Stop **with** Price | Stop activates as a Market order; Stop Limit carries both |
| Account that is not ≤9 digits | Account(1) is the 9-digit CDS account |
| Missing or invalid OrderRestrictions | Tag 529 is mandatory, ≤5 chars, from `{9, E, I, M, R}` |
| Board not valid for the connection type | FIXTRADER covers NM/OD/BI; FIXNEGDEAL is DBT/off-market |
| Odd-Lot or Buy-In order under a market-maker broker code | Branch digit `1` is issued for Normal-board orders |
| Re-submitting an order object that already has fills | Would import stale state into a new order |

## 3. Order lifecycle

```
              New order
                 |
                 v
      +--------> NEW ---------------------------+
      |           |                             |
      |    partial execution                    | full execution
      |           v                             v
      +---- PARTIALLY_FILLED -----------------> FILLED
      |           |
      |     cancel request (35=F, own ClOrdID)
      |           v
      |    PENDING_CANCEL  <-- still live, still fills
      |        /      \
      | reject(35=9)   confirm (35=8, ExecType=4)
      |      /             \
      +-----+               v
                          CANCELED

  Also reachable, unsolicited, from any working state:
    CANCELED  — supervisor cancel, native-protocol cancel, market control
    EXPIRED   — IOC/FoK remainder, GT expiry, dynamic limit  (ExecType=C)
    REJECTED  — rejected on entry (ExecType=8)
```

`PENDING_CANCEL` is a **local** state: BTS2's OrdStatus enumeration has no Pending
Cancel value (ExecType(150)=6 does). It models the window between sending the request
and the venue answering — the window in which orders are most often mishandled.

### Cancelling

```python
engine.cancel_order(cl_ord_id)              # 35=F, generates its own unique ClOrdID
# ... the order is STILL LIVE here and can still fill ...
engine.simulate_execution_report(cl_ord_id, 2000, 6.10, exec_id="1535B")
#   -> fill applied, status stays PENDING_CANCEL

engine.confirm_cancel(cl_ord_id)            # 35=8 ExecType=4  -> CANCELED
# or
engine.reject_cancel(cl_ord_id, reason=text_58)   # 35=9 -> back to NEW / PARTIALLY_FILLED
```

Do not release risk budget, reuse the ClOrdID, or report the position as flat while the
order sits in `PENDING_CANCEL`.

### Applying execution reports

```python
engine.simulate_execution_report(
    cl_ord_id,
    filled_qty=2000,      # LastQty(32) — THIS fill, not CumQty(14)
    exec_price=6.10,      # LastPx(31)
    exec_id="1535B",      # ExecID(17) — always pass it
)
```

- **Always pass `exec_id`.** Resend Request (MsgType=2) recovery replays application
  messages; ExecID is the only thing that distinguishes a resend from a new fill.
  Without it the engine logs a warning and applies the fill non-idempotently.
- A report that would take cumulative quantity past the order quantity is **refused**
  and logged at ERROR. Alert on it — it means a duplicate escaped deduplication or the
  venue sent something impossible. Reconcile against BTS2's trade records before trading
  on the position.
- The running average price matches the exchange's `AvgPx(6)`; reconcile the two rather
  than trusting either alone.

### Handling what you did not ask for

| Event | Handler |
|---|---|
| Supervisor cancels your order | `confirm_cancel()` — accepts an unsolicited cancel from a working state |
| Order amended/cancelled via the native protocol | Arrives with **no ClOrdID**; match on OrderID(37) in your own engine, then drive state here |
| IOC/FoK remainder auto-cancelled | `expire_order(..., reason="IOC remainder")` |
| GT order expired by date or dynamic limit | `expire_order()`, with ExecRestatementReason(378) in the reason |
| Order rejected on entry | Mark `REJECTED`; do not reuse the ClOrdID |

## 4. What this module does not do

Sequence-number assignment, gap fill, Resend Request handling, message encoding,
TCP/TLS/VPN transport, persistence across restarts, order modification (35=G), Trade
Capture Reporting, cross orders (35=s), mass quotes, and market data. All of those
belong to your FIX engine or to a separate specification.

If you implement order modification yourself, remember it replaces the *entire* order
state — omitted fields are reset, not retained — it needs a new ClOrdID, and a price
change or quantity increase forfeits queue priority.

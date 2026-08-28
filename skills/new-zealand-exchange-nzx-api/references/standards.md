# Standards for New Zealand Exchange (NZX) Order Entry

## Scope of this document

Everything below that is stated as fact is sourced from NZX's own published
material or the FIX specification, and the source is named. Where NZX's
information is not public — most importantly the order-entry FIX specification —
this document says so rather than guessing.

## NZX Main Board Price Steps

NZX publishes two distinct price-step regimes for the Main Board.

| Security class | Security price | Minimum price step |
|---|---|---|
| **Listed funds** | any price | $0.001 (tenth cent) |
| All other securities | Up to $0.19 | $0.001 (tenth cent) |
| All other securities | $0.20 to $1.995 | $0.005 (half cent) |
| All other securities | Above $2.00 | $0.01 (one cent) |

Notes:

- The funds carve-out is absolute: every listed fund ticks at $0.001 regardless
  of price. Applying the band schedule to an ETF rejects valid orders.
- The gap in NZX's wording between $1.995 and $2.00 is immaterial in practice.
  No price strictly between them is a multiple of $0.005, and $2.00 itself is a
  valid multiple of both $0.005 and $0.01, so both readings of the boundary
  accept and reject exactly the same set of prices.
- **These steps are not frozen.** NZX Participant Rule 11.9.1 reads: "Minimum
  price changes for a Security Quoted on the NZSX may be specified by NZX from
  time to time." Rule 22.7.9 says the same for the FSM. Treat the table as
  configuration (`NZXTickSchedule`) and reconcile it against the current NZX
  notice.

## NZX Debt Market (NZDX)

Debt securities have a **minimum yield change of 0.005%** — a yield step, not a
price step. Hybrid securities listed on the NZDX are traded in price per $100
rather than yield and follow the standard price-step rules above.

This engine validates price steps only. It rejects
`NZXSecurityType.DEBT_YIELD_QUOTED` rather than applying the wrong rule.

## NZSX Cash-Market Session Schedule (Pacific/Auckland wall-clock)

The schedule is published in NZ local time. The wall-clock boundaries are
identical under **NZST (UTC+12)** and **NZDT (UTC+13)** — daylight saving shifts
the UTC offset, not the local session times.

| Phase | Nominal time (NZ local) | Matching | New orders |
|---|---|---|---|
| Enquiry | 17:30 – 08:30 (next day) | No | No (read-only) |
| Pre-Open | 08:30 – opening auction | No | Yes (queued) |
| Opening Auction | randomised within 09:59:30 – 10:00:30 | At auction | Restricted |
| Normal / Continuous Trading | ~10:00 – 16:45 | Continuous | Yes |
| Pre-Close | 16:45 – closing auction | No | Yes |
| Closing Auction | randomised within 16:59:30 – 17:00:30 | At auction | Restricted |
| Adjust | ~17:00 – 17:30 | No | No (amend/withdraw only) |

Corroborated by the NZX Participant Rules, which fix the session *durations*
rather than the clock times: Pre-Opening is "a period of 1 hour and 30 minutes"
(Rule 11.2.1), Pre-Close is "the period of 15 minutes" (Rule 11.5.1), and Adjust
is "a period of 30 minutes" (Rule 11.7.1) — each "or such other period as
determined from time to time by NZX". Rule 11.1.1 adds that "the different
sessions and scheduled times for each session during a Trading Day will be as
notified from time to time by NZX."

NZX's published session/action matrix:

| Action | Enquiry | Pre-Open | Normal | Pre-Close | Adjust | Suspension | Halt |
|---|---|---|---|---|---|---|---|
| Place order | N | Y | Y | Y | N | N | Y |
| Amend order | N | Y | Y | Y | Y | N | Y |
| Trade | N | N | Y | N | N | N | N |
| Withdraw order | N | Y | Y | Y | Y | Y | Y |

Two consequences for automated trading:

- **The auctions are randomised** by ±30 seconds. Code that assumes a hard
  10:00:00.000 open will intermittently race the exchange. Treat the exchange
  session-state message as authoritative.
- **Adjust is not a trading session.** Orders may be amended (but not improved:
  bids may be reduced and offers raised, never the converse) and withdrawn; new
  orders may not be entered.

## Order Types Published by NZX

| NZX name | Semantics | FIX TimeInForce (59) |
|---|---|---|
| Limit — Day | Rests until filled/cancelled, removed at end of day | `0` |
| Limit — Good til Date | Rests until the specified date | `6` (requires ExpireDate 432) |
| Limit — Good til Cancel | Rests until filled or cancelled | `1` |
| Fill-and-kill | Immediate partial match, remainder cancelled | `3` (IOC) |
| All or Nothing (fill-or-kill) | Entire quantity or nothing | `4` (FOK) |
| Market | Quantity specified, no price; walks the book | OrdType (40) `1` |

`NZXTimeInForce` implements DAY, GTC, IOC and FOK. Good-til-Date is **not**
implemented, because it requires `ExpireDate (432)` / `ExpireTime (126)`, and is
rejected explicitly rather than silently downgraded to DAY.

NZX notes that Participants may decline a market order, since Participants must
maintain an orderly market under the Participant Rules and Good Broking Practice.

## FIX Message Requirements

These are FIX specification requirements, not NZX-specific ones:

- **Standard header**: `BeginString (8)`, `BodyLength (9)`, `MsgType (35)`,
  `MsgSeqNum (34)`, `SenderCompID (49)`, `TargetCompID (56)`, `SendingTime (52)`.
- **Standard trailer**: `CheckSum (10)`, three digits, always last.
- `BodyLength (9)` counts the bytes from the field following `9` up to and
  including the delimiter preceding `10`. `CheckSum (10)` is the sum of all
  preceding bytes modulo 256.
- `SendingTime (52)` and `TransactTime (60)` are `UTCTimestamp`:
  `YYYYMMDD-HH:MM:SS` or `YYYYMMDD-HH:MM:SS.sss`, in UTC. Colons, dash and
  period are required in position.
- `Price (44)` is required for limit order types. It is not meaningful on a
  market order and is commonly rejected there.
- Fields are SOH (`0x01`) delimited on the wire. A `|` delimiter is a readability
  convention for logs and test fixtures only.
- `OrderCancelRequest (35=F)` requires a NEW `ClOrdID (11)` distinct from
  `OrigClOrdID (41)`, plus `Symbol (55)`, `Side (54)` and `TransactTime (60)`.
- `OrdStatus (39)` values used here follow FIX 4.4 Appendix A.

## NZX FIX Connectivity — what is and is not verifiable

**Not public.** NZX distributes its order-entry FIX specification to Participants
through the Participant Portal. This skill therefore asserts **no** FIX
`BeginString`, `TargetCompID`, `SenderCompID`, `TargetSubID`, port, host, rate
limit or supported-order-type list on NZX's behalf, and `NZXFixSessionConfig`
ships with no defaults. Take those values from the specification NZX issued to
your firm.

**Publicly described.** NZX runs a Nasdaq matching engine (Nasdaq Financial
Framework), with FIX used for order entry and negotiated deal reporting and ITCH
for market data. Third-party integrators describe NZX order entry as FIX 5.0.
This is secondary-source information — confirm the version against your own NZX
specification before building a session, and note that the tags this engine emits
(35, 34, 49, 56, 52, 11, 55, 54, 38, 40, 44, 59, 15, 60, 41) carry the same
meaning and enumerations in FIX 4.x and 5.x.

## Other NZX Trading Features (not implemented here)

- **NZX Dark** operates only during the Normal Trading Session, though NZX Dark
  Orders may be withdrawn at any time other than the Enquiry Session
  (Participant Rules 11.1.1). Dark Orders support a *Minimum Executable Quantity*
  and *Sweep Orders* that route to NZX Central on non-match (Rule 10.13.6).
- **Self-Match Prevention (SMP)** is configured per the NZX SMP Practice Note;
  Cancel-Passive SMP removes the passive side rather than trading with yourself
  (Rules 11.4.2, 10.13.4).
- **Non-disclosure of quantity** (iceberg) is available where the order value
  exceeds $100,000, "or any such amount as prescribed from time to time by NZX"
  (Rule 11.11.2).
- **Short-selling restrictions** are maintained and varied by NZX per security.
  Read the current list from NZX; do not hard-code it.

## Sources

- NZX — Trading Information (Price Steps, Order Types, NZDX yield step, trading
  fees): https://www.nzx.com/services/nzx-trading/trading-information
- NZX — Anatomy of a Trading Day (session states, auction randomisation, session
  action matrix): https://www.nzx.com/services/nzx-trading/anatomy-of-a-trading-day
- NZX Limited Participant Rules, January 2025 (v2.10) — Section 11 (NZSX trading
  sessions 11.1–11.8; Minimum Bids / Price Steps 11.9.1; VWAP 11.10;
  non-disclosure of quantity 11.11), Section 10.13 (trading operations, SMP,
  Sweep Orders), Rule 22.7.9 (FSM price steps).
- FIX 4.4 specification, Appendix A — UTCTimestamp format, OrdStatus (39)
  values, standard header/trailer, TransactTime (60).
- NZX — Self-Match Prevention:
  https://www.nzx.com/services/nzx-trading/Self-Match-Prevention

## Category

`global-market-integration`

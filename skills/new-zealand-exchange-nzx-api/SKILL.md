---
name: new-zealand-exchange-nzx-api
description: >-
  New Zealand Exchange (NZX) Main Board order-entry engine enforcing the NZX price-step (tick size) schedule including the listed-funds carve-out, NZSX session-phase awareness, and FIX order lifecycle serialisation (NewOrderSingle 'D', OrderCancelRequest 'F', ExecutionReport '8' decoding).
domain: Global Exchange Integrations
subdomain: Australasia Markets & FIX Protocol Connectivity
tags: ["nzx", "new-zealand-exchange", "fix-protocol", "tick-size-schedule", "nzd", "order-routing", "australasia"]
brokers_frameworks: ["NZX Participant FIX Order Entry", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing cash-equity or listed-fund orders to the NZX Main Board (NZSX) over a FIX order-entry session. It covers the NZX-specific decisions that a generic FIX engine will not make for you:

- enforcing the NZX **price-step schedule**, including the carve-out that gives every listed fund a $0.001 step regardless of price;
- gating on the **NZSX session phase** in `Pacific/Auckland` wall-clock time;
- **strict order-field validation**, so a malformed side or order type is rejected rather than silently coerced into a different, tradeable order;
- serialising correctly framed FIX `NewOrderSingle` (35=D) and `OrderCancelRequest` (35=F) messages, and decoding `ExecutionReport` (35=8).

**Sourcing caveat, read this first.** NZX distributes its order-entry FIX specification to Participants through the Participant Portal; it is not public. Public descriptions indicate NZX runs a Nasdaq matching engine with FIX order entry and ITCH market data, but this skill does **not** assert a FIX version, a `TargetCompID`, or a set of supported order types on NZX's behalf. `NZXFixSessionConfig` therefore has no defaults — take `BeginString`, `SenderCompID` and `TargetCompID` from the specification NZX issued to your firm.

## When NOT to Use

- **As a FIX engine.** This module has no sockets, no Logon/Logout, no heartbeating, no sequence-number persistence and no resend handling. It builds and parses application messages only; the caller supplies `MsgSeqNum (34)`. Session management belongs to `fix-protocol-session-management-across-venues`.
- **For NZX Debt Market securities quoted in yield.** NZDX debt trades on a minimum *yield* change (0.005%), not a price step. `NZXSecurityType.DEBT_YIELD_QUOTED` is rejected rather than mis-validated. NZDX hybrids quoted as a price per $100 do follow the standard price steps — pass those as `EQUITY`.
- **For NZX derivatives (NZCX) or SGX-NZX dairy derivatives.** Those markets publish per-contract specifications; the NZSX price-step table does not apply.
- **As the authoritative session clock.** `NZXSessionSchedule` uses published nominal boundaries and is advisory only — see the auction-randomisation pitfall below.

## Prerequisites

- Python 3.9+ (uses `zoneinfo` for NZDT/NZST conversion; production hosts must ship IANA tzdata).
- An NZX Participant FIX order-entry session, and the `BeginString` / `SenderCompID` / `TargetCompID` (and any `TargetSubID`) from NZX's specification for your firm.
- A FIX engine that owns the session layer and can supply `MsgSeqNum (34)`, via `seq_num=` or a `seq_num_provider`.
- A classification of each instrument as `EQUITY` or `FUND` for price-step purposes, and an NZ trading-holiday calendar (`NZXSessionSchedule` is time-of-day only).

## Workflow

1. **Configure the session.** Build an `NZXFixSessionConfig` from NZX's issued specification. There are no defaults: a plausible-but-wrong `TargetCompID` fails at Logon in the best case and routes to the wrong destination in the worst.
2. **Classify the instrument.** Set `NZXOrderRequest.security_type`. It defaults to `EQUITY` because that is the fail-closed direction — a fund misread as an equity gets its valid $0.001 price *rejected*, whereas an equity misread as a fund would have a sub-tick price *accepted and sent*.
3. **Price-step compliance.** `validate_price_tick` compares the price against the schedule using exact `Decimal` arithmetic:
   - listed **funds**: $0.001 at every price level;
   - all other securities: up to $0.19 → $0.001; $0.20–$1.995 → $0.005; above $2.00 → $0.01.
   Non-positive prices are never valid. NZX Participant Rule 11.9.1 lets NZX respecify these steps at any time, so `NZXTickSchedule` is configuration — reconcile it against the current NZX notice rather than treating it as a constant.
4. **Gate on the session phase.** Pass `at_time=` to gate the build on `NZXSessionSchedule`, or call `is_order_entry_window` yourself before sending. NZX accepts new orders in Pre-Open, Normal Trading and Pre-Close; Adjust permits amend/withdraw but no new orders; Enquiry permits nothing.
5. **Build the order.** `build_fix_new_order_single` validates every field and either returns a `NEW` report carrying a framed message, or a `REJECTED` report with a `rejection_reason` and an empty payload. It never coerces an unrecognised field into a working alternative. `Price (44)` is emitted for LIMIT orders only, rendered at the precision of the step it was validated against.
6. **Cancel.** `build_fix_order_cancel_request` requires a NEW `ClOrdID (11)` distinct from `OrigClOrdID (41)`. The returned status is `PENDING_CANCEL`, not `CANCELED` — see the pitfall below.
7. **Process ExecutionReports.** `parse_execution_report` verifies the CheckSum, refuses any MsgType other than `8`, requires `ClOrdID`/`ExecID`/`OrdStatus`, and surfaces `poss_dup`. Drive position state from `CumQty (14)` and `LeavesQty (151)`.

> Full procedure: see `references/workflows.md`.
> Price steps, session table, and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying the equity band schedule to a listed fund.** Every NZX listed fund ticks at $0.001 *independent of price*. Running an ETF priced at $5.001 through the equity bands rejects a perfectly valid order — and the reverse misclassification would send a sub-tick price to the exchange. Classify the instrument; do not infer the step from price alone.
- **Silently coercing an order field.** A `side` of `"SEL"` must not fall through to Sell, and an `order_type` of `"LIMT"` must not fall through to Market — a coerced field means sending a real order that differs from the one that was requested, and a limit-to-market coercion also skips price validation entirely. Reject and surface the field name.
- **Sending Price (44) on a market order.** Price is meaningful only for limit order types; venues commonly reject a market order that carries one. Omit the tag rather than passing a stale limit price through.
- **Epoch timestamps in tag 60.** `TransactTime (60)` and `SendingTime (52)` are FIX `UTCTimestamp` — `YYYYMMDD-HH:MM:SS.sss` in UTC. An epoch-milliseconds integer is rejected by any conforming FIX engine.
- **Emitting an unframed message.** Every FIX message needs `BeginString (8)`, `BodyLength (9)`, `MsgSeqNum (34)`, `SendingTime (52)` and `CheckSum (10)`, SOH-delimited. A `|`-delimited tag string is a debugging artefact, not something you can put on the wire.
- **Unvalidated identifiers in tag values.** A `ClOrdID` or `Symbol` taken from an upstream system and pasted into a tag=value string is a field-injection vector: an embedded SOH terminates the field and everything after it is parsed as new tags.
- **Vendor symbology in Symbol (55).** `FPH.NZ` is market-data vendor symbology. FIX `Symbol (55)` carries the bare NZX ticker, `FPH`. Strip the market suffix before routing.
- **Treating a cancel request as a cancellation.** An `OrderCancelRequest` is a request. The order stays live and can still fill until an ExecutionReport confirms `OrdStatus=4`. Keep applying fills until then, and never reuse the original `ClOrdID` for the cancel — that is a protocol error.
- **Double-counting a replayed fill.** ExecutionReports can be resent after a reconnect with `PossDupFlag (43)=Y`. Summing `LastQty (32)` across a duplicate double-counts the position; drive state from `CumQty (14)`.
- **Mistaking a session Reject for an order state.** A Reject (35=3) or OrderCancelReject (35=9) is not an ExecutionReport and confirms nothing about the order. It must not be decoded as one.
- **Racing the auction.** NZX fires the opening and closing auctions at a random instant within ±30 seconds of 10:00 and 17:00. Code that assumes a hard 10:00:00 open will intermittently race the exchange — wait for the exchange session-state message.
- **Wrong timezone, and holidays.** NZX publishes its schedule in NZ local time; daylight saving shifts the UTC offset (NZST UTC+12 → NZDT UTC+13), not the local session times. Convert to Auckland wall-clock once at the scheduler boundary. `NZXSessionSchedule` is also time-of-day only — it will happily report NORMAL on a NZ public holiday, so gate on a trading calendar too.
- **Hard-coding a short-selling restriction.** NZX maintains a current list of securities under short-selling restriction and can vary it; read it from NZX, do not freeze it in code.

## Verification

- Construct `NZXFixSessionConfig` and `NewZealandExchangeNZXEngine`; assert the engine cannot be constructed without a session config.
- Assert equity steps $0.001 / $0.005 / $0.01 at $0.15 / $1.50 / $25.00, and the band edges at $0.199, $0.20, $1.995, $2.00.
- Assert $5.001 validates as a `FUND` and is rejected as an `EQUITY`; assert `DEBT_YIELD_QUOTED` raises.
- Assert $30.005 and $1.9975 are rejected, and that `0.1 + 0.2` still validates as $0.300.
- Assert a valid LIMIT order emits `35=D`, `55=FPH`, `54=1`, `44=30.00`, `15=NZD`, and a `60` matching `YYYYMMDD-HH:MM:SS.sss`; independently recompute `BodyLength (9)` and `CheckSum (10)` from the payload bytes.
- Assert `side="SEL"`, `order_type="LIMT"`, `time_in_force="GTD"`, `quantity=0` and `symbol="FPH.NZ"` are all refused.
- Assert a MARKET order omits tag 44; assert a missing `seq_num` raises.
- Assert `phase_at` returns PRE_OPEN (08:30), OPENING_AUCTION (09:59:30), NORMAL (12:00), PRE_CLOSE (16:45), CLOSING_AUCTION (16:59:30), ADJUST (17:10) and ENQUIRY (17:30) in Auckland wall-clock.
- Assert `parse_execution_report` decodes a partial fill, flags `PossDupFlag=Y`, and raises on a 35=3 message, a missing mandatory tag, and a tampered CheckSum.
- Run `python scripts/test_new_zealand_exchange_nzx_api.py`.

## Related Skills

- `australian-securities-exchange-asx-api`
- `fix-protocol-session-management-across-venues`
- `global-exchange-holiday-calendar-handling`
- `order-placement-idempotency`
- `daylight-saving-time-transition-handling`

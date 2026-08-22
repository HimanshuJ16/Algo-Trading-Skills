# Workflows for CME Group tag=value FIX

These procedures apply to tag=value FIX 4.2/4.4 sessions — CME STP, replay and
conformance harnesses, and other venues that still accept tag=value order entry. CME
Globex order entry is iLink 3 (SBE/FIXP) and none of this applies to it; see
`references/standards.md`.

Messages below are shown with `|` for readability. The wire delimiter is SOH (`0x01`).

## 1. Session logon

- Outbound: `8=FIX.4.2|9=<len>|35=A|49=SENDER|56=TARGET|34=<seq>|52=<UTC>|98=0|108=30|10=<sum>|`
- Wait for the peer's `35=A` before sending business messages.
- Restore both sequence counters from durable storage first. Starting at 1 mid-session
  desynchronises the session; whether and when to send `ResetSeqNumFlag(141)=Y` is
  specified by the venue, not chosen locally.
- Heartbeat on `108`, and treat a missed heartbeat as a `TestRequest(35=1)` trigger before
  concluding the session is dead. This module does not own timers — a session engine must.

## 2. Order construction (`35=D`)

Header, in order: `8`, `9`, `35`, then `49`, `56`, `34`, `52`. Body follows. `10` last.

| Tag | Value | Note |
|---|---|---|
| `1` | Account | Required — no placeholder default |
| `11` | ClOrdID | Unique per order; reuse on a retry only under a deliberate idempotency scheme |
| `50` | Operator ID | CME Rule 576 |
| `54` | Side | `1` Buy, `2` Sell |
| `38` | OrderQty | Positive integer |
| `40` | OrdType | `2` = Limit |
| `44` | Price | Decimal string, unrounded, no scientific notation |
| `55` | Symbol | |
| `1028` | ManualOrderIndicator | `N` automated, `Y` manual |
| `7928` | SelfMatchPreventionID | Registered in FADB; numeric if the code will ever move to iLink 3 |
| `8000` | SelfMatchPreventionInstruction | `O` cancel oldest/resting, `N` cancel newest/aggressing. Omit to take CME's default (cancel resting) |

`BodyLength` and `CheckSum` are computed over the final byte sequence. Transmit that
sequence verbatim — any re-encoding downstream invalidates both.

## 3. Inbound message processing

For each inbound message, in this order:

1. **`SequenceReset(35=4)` with `GapFillFlag(123)≠Y`** — apply `NewSeqNo(36)` immediately,
   whatever the message's own `34` is. Reject a `NewSeqNo` that moves the sequence
   backwards. This is the only message exempt from the too-low rule.
2. **`34` > expected** — if no resend is outstanding, emit
   `ResendRequest(35=2)` for `[expected, 34-1]` and record the range. If one *is*
   outstanding, process nothing and emit nothing: the peer is replaying the range you
   already asked for, and re-requesting per message is how one gap becomes a resend storm
   and then a disconnect.
3. **`34` == expected** — process the message, then advance. For a gap fill
   (`35=4`, `123=Y`) advance to `NewSeqNo(36)` instead of by one. Clear the recovery state
   once the expectation passes the requested range.
4. **`34` < expected, `PossDupFlag(43)=Y`** — discard as a duplicate.
5. **`34` < expected, no `PossDupFlag`** — session-fatal. Emit `Logout(35=5)` with an
   explanatory `Text(58)`, then close the transport connection. Do not continue trading on
   the session: silently ignoring this loses every subsequent `ExecutionReport`, and the
   in-process position stops matching the exchange's.

## 4. Execution report handling

Parse `ExecutionReport(35=8)` into order state via `OrdStatus(39)` and `ExecType(150)`,
not via `Text(58)`. Note that a missing or invalid `ManualOrderIndicator(1028)` comes back
as a **Business Reject** (`35=j`, `380=100`), not as an `ExecutionReport` and not as a
session reject — code that watches only `35=3` and `35=8` will believe the order is live.

## 5. Porting to iLink 3

Run `to_ilink3_order_fields()` over the order parameters before rewriting anything. It
fails on exactly the fields a naive port breaks:

- `50` → `5392` `SenderID`, String(20).
- `7928` → `2362`, a `uInt64`. An alphanumeric SMP ID must be re-registered as numeric.
- `1028` → boolean `0`/`1`, not the characters `N`/`Y`.
- Session recovery has no equivalent: FIXP uses `Retransmit Request` / `NotApplied`.

# Borsa Istanbul (BISTECH) Integration Standards

> **Sourcing note.** This file separates two kinds of claim, because they carry very
> different risk. Sections 1-4 are **FIX protocol semantics**, normative in the FIX
> specification itself and checkable in FIXimate; they are what the module in `scripts/`
> actually implements. Section 5 holds **venue-specific** claims about Borsa Istanbul,
> each tagged with its corroboration level. BIST publishes its message specifications,
> session schedules and symbology to members rather than openly, so anything tagged
> **Confirm** must be checked against the BISTECH specification for your market before
> it drives production behaviour. A wrong tag number is a rejected order; a wrong
> assumption about cancel semantics is a lost fill.

## 1. Protocol baseline

| Item | Standard | Basis |
| :--- | :--- | :--- |
| Wire protocol | FIX 5.0 SP2 semantics for order entry | Venue-stated (section 5) |
| Session layer | Standard FIX session: Logon `A`, Heartbeat `0`, Test Request `1`, Resend Request `2`, Sequence Reset `4`, Logout `5`, session-level Reject `3` | FIX specification |
| Application layer | NewOrderSingle `D`, Order Cancel Request `F`, Order Cancel/Replace Request `G`, ExecutionReport `8`, Order Cancel Reject `9` | FIX specification |
| Sequence numbers | `MsgSeqNum` (34) is per-session and gap-filled by the counterparty on request; persist it across restarts or the session cannot resume | FIX specification |

The module in `scripts/` implements **none** of this transport. It models the order
state that these messages drive, so that a real engine (QuickFIX or a BIST-certified
application) can own encoding, sequencing and reconnection while the lifecycle logic
stays testable. Keep that split when adapting it.

## 2. ExecType and OrdStatus are different fields

This is the distinction integrations most often collapse, and the one the module exists
to enforce.

- **`OrdStatus` (39)** is the order's *current state*.
- **`ExecType` (150)** describes *the report you are holding* — why it was sent.

A single ExecutionReport carries both, and they routinely differ: a partial fill arrives
as `ExecType=F` with `OrdStatus=1`.

| `OrdStatus` (39) | Meaning | Modelled as |
| :--- | :--- | :--- |
| `0` | New | `OrderStatus.NEW` |
| `1` | Partially filled | `OrderStatus.PARTIALLY_FILLED` |
| `2` | Filled | `OrderStatus.FILLED` |
| `4` | Canceled | `OrderStatus.CANCELED` |
| `6` | Pending Cancel | `OrderStatus.PENDING_CANCEL` |
| `8` | Rejected | `OrderStatus.REJECTED` |

**`ExecType=F` (Trade) is the fill.** In FIX 4.2 a fill arrived as `ExecType=1`
(Partial fill) or `2` (Fill); FIX 4.4 replaced both with the single value `F` and
deprecated them, and FIX 5.0 SP2 follows FIX 4.4 here. Code ported from a FIX 4.2
integration that branches on `ExecType in (1, 2)` will silently apply **no fills at
all** against a 5.0 SP2 session — the reports arrive, match no branch, and the order sits
at `OrdStatus=0` while the position builds at the venue. Branch on `F`, and read
partial-versus-complete from `OrdStatus`, not from `ExecType`.

The other application-level values the lifecycle needs: `0` (New — the acknowledgement,
not a fill), `4` (Canceled), `6` (Pending Cancel), `8` (Rejected).

## 3. A cancel request is a request

`MsgType=F` asks the venue to cancel the remaining quantity. It does not cancel
anything. The order stays live and fillable until the venue answers, in one of three
ways:

| Answer | Meaning | Correct handling |
| :--- | :--- | :--- |
| ExecutionReport, `ExecType=6` | Request acknowledged, still working | Stay in `PENDING_CANCEL`, keep applying fills |
| ExecutionReport, `ExecType=4` | Cancel completed | Terminal; release risk budget now, not before |
| Order Cancel Reject, `MsgType=9` | Refused | Return to `OrdStatus=1` or `0` per fill state |

The usual cause of `MsgType=9` is that the order filled or went inactive before the
request arrived. Read `CxlRejResponseTo` (434) to know which request was refused and
`CxlRejReason` (102) with `Text` (58) for why.

Two invariants follow, both enforced in `scripts/`:

- **Do not release risk budget or reuse the `ClOrdID` while an order is Pending Cancel.**
  It is still working.
- **Only `ExecType=4` is terminal.** Marking the order canceled locally when the request
  is *sent* discards any fill that lands in the race window — and that fill is a real
  position you now do not know you hold.

## 4. Idempotency and arithmetic

| Requirement | Standard | Why |
| :--- | :--- | :--- |
| `ClOrdID` (11) uniqueness | Unique per session per the FIX specification; in practice never reuse an id that has an order still working under it | A reused id makes cancel/replace ambiguous |
| Cancel chaining | `OrigClOrdID` (41) identifies the order being cancelled; `ClOrdID` (11) identifies the cancel request itself | Two different ids on one message |
| Fill deduplication | Deduplicate on `ExecID` (17) before applying a report | Resend Request recovery replays application messages, and a replayed report is otherwise indistinguishable from a new one. `PossDupFlag` (43) and `PossResend` (97) are hints, not a substitute — a replay can arrive without them |
| Overfill | Reject any report taking `CumQty` (14) beyond `OrderQty` (38) | It means a duplicate escaped deduplication, or the venue erred. Absorbing it corrupts the average price and hides the error |
| Average price | Recompute from `LastQty` (32) and `LastPx` (31) as a quantity-weighted mean over applied fills | Averaging the `AvgPx` (6) field across reports weights every report equally regardless of size |
| Working-order identity | `CumQty` + `LeavesQty` (151) = `OrderQty` while working; `LeavesQty` is 0 in a terminal state | A cheap reconciliation assertion on every report |
| Float accumulation | Compare cumulative quantity to order quantity with a tolerance | Summing many partial fills as floats can miss exact equality by an ulp and strand a filled order in `PARTIALLY_FILLED` |

## 5. Venue-specific claims

| Claim | Corroboration | Notes |
| :--- | :--- | :--- |
| BISTECH runs on Nasdaq's Genium INET technology | **Corroborated** — the BIST-Nasdaq technology partnership and the BISTECH rollout are both publicly reported | Explains why the protocol family below is the Nasdaq set rather than a bespoke one |
| Order entry over FIX, with OUCH available as the low-latency binary alternative | **Corroborated** at the level of the platform's protocol family | Which protocols *your* membership is entitled to use is account-specific |
| Market data over ITCH, with a separate feed product for depth/reference data | **Corroborated** at the platform level | Out of scope for this skill, which covers order entry only |
| FIX version is **5.0 SP2** | **Venue-stated** — repeated from Borsa Istanbul's own description | Confirm against the BISTECH FIX specification for your market. If your session is 4.2 or 4.4, section 2's `ExecType=F` point changes |
| Members must certify their software, or use a certified application, before production access | **Corroborated** as standard practice for this venue and platform | Passing this skill's unit tests is **not** certification and grants no access |
| Equity instrument codes carry a `.E` suffix (e.g. `GARAN.E`) | **Confirm** | Widely seen in BIST data, but suffix sets differ by instrument class and change over time. Read the symbology from the venue's instrument reference file rather than hard-coding a suffix |
| Accepted `TimeInForce` (59) values, required party/account tags, session phase schedule, tick and lot tables | **Confirm** — deliberately not asserted here | These are market-specific and change. `scripts/` models the generic FIX subset (`0` Day, `1` GTC, `3` IOC, `4` FOK, `6` GTD) and does not claim BIST accepts all of them |

The last row is the important one. This skill does not hard-code BIST's parameter
tables, and it should not: a stale tick table or an unsupported `TimeInForce` produces
a rejected order at best, and a mispriced one at worst. Source them from the venue.

## 6. Operational conventions

- **Never blindly retry a rejected order.** Log `OrdRejReason` (103) and `Text` (58) and
  alert trading operations. A reject is information about your order, your entitlements
  or the market state; resending the same message reproduces it.
- **Persist order state and sequence numbers across restarts.** An intraday crash that
  loses either leaves working orders at the venue that the process no longer knows about
  — see `order-placement-idempotency` and `websocket-reconnection-with-state-recovery`
  for the general pattern.
- **Reconcile against the venue's end-of-day trade file.** A local state machine is only
  ever as good as the reports fed to it.

## Sources

- FIX Trading Community — protocol specifications (message types, `ExecType`/`OrdStatus`
  semantics, the FIX 4.4 replacement of `ExecType` 1/2 with `F`, and all tag numbers
  cited above). <https://www.fixtrading.org/standards/>
- FIXimate — searchable reference for FIX messages, fields and enumerated values, used
  to check every tag number and value in this file.
  <https://fiximate.fixtrading.org/>
- Borsa Istanbul — venue documentation, membership and technical specifications; the
  authority for everything in section 5 tagged **Venue-stated** or **Confirm**.
  <https://www.borsaistanbul.com/>

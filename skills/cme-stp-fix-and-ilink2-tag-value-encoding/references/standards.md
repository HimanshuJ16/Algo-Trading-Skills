# CME Group FIX Reference

Verified 2026-08-21 against the sources listed at the end. CME changes protocol surfaces
on published timelines; re-verify before relying on a value here.

## Protocol status — what CME actually accepts today

| Surface | Protocol | Status |
|---|---|---|
| Order entry (MSGW) | iLink 3 — FIX SBE over FIXP | Current. iLink 2 decommissioned **2021-03-28** |
| Order entry (CGW) | iLink 3 — FIX SBE over FIXP | Current. iLink 2 decommissioned **2025-04-06** |
| Post-trade / trade capture | **CME STP — standard FIX 4.4** tag=value | Current |
| Market data | MDP 3.0 — SBE multicast | Current; not FIX tag=value |

iLink 3 is a rewrite, not an upgrade: SBE binary encoding plus the FIXP session layer.
FIXP has no `MsgSeqNum(34)`, `ResendRequest(35=2)` or `SequenceReset(35=4)`; recovery is
`Retransmit Request` / `NotApplied`. Tag=value FIX order entry to CME Globex no longer
exists.

## Order tags — tag=value form vs. iLink 3

| Concept | tag=value FIX (iLink 2) | iLink 3 (SBE) | Notes |
|---|---|---|---|
| Manual order indicator | `1028`, char `N`/`Y` | `1028`, boolean `0`=automated / `1`=manual | Required on CME order messages since June 2011 |
| Operator ID (Rule 576) | `50` (`SenderSubID`) | `5392` `SenderID`, String(20) | Issued by the clearing member; unique at clearing-member level; case-insensitive |
| Self-Match Prevention ID | `7928`, alphanumeric, no spaces, documented as ≤12 bytes | `2362`, **uInt64** | A non-numeric ID has no iLink 3 representation |
| SMP instruction | `8000`, char | `8000`, char | Only `O` and `N` are defined |
| Location | — | `9537` String(5) | iLink 3 audit-trail element |

`SelfMatchPreventionInstruction(8000)` enumeration, from CME's published iLink 3 SBE
schema (v8.4):

| Value | Meaning |
|---|---|
| `O` | CancelOldest — cancel the **resting** order |
| `N` | CancelNewest — cancel the **aggressing** order |
| *absent* | CME cancels the resting order by default |

There is no `B` (cancel both), no `R`, and no long-form string such as `CANCEL_RESTING`.
On an `OrderCancelReplaceRequest(35=G)`, omitting Tag 8000 **removes** the SMP instruction
from the resting order rather than leaving it unchanged.

SMP IDs must be registered in the CME Firm Administrator Dashboard (FADB) before use.

## Rejects

| Condition | CME response |
|---|---|
| Missing or invalid `ManualOrderIndicator(1028)` | **Business Reject** `35=j` with `BusinessRejectReason(380)=100` and explanatory `Text(58)` — *not* a session-level `35=3` |

## FIX session layer — the rules this module implements

Applies to tag=value FIX 4.2/4.4 sessions (CME STP, replay harnesses, other venues).

| Rule | Behaviour |
|---|---|
| Standard header | `BeginString(8)`, `BodyLength(9)`, `MsgType(35)` are the first three fields; `CheckSum(10)` is last |
| `BodyLength(9)` | Byte count from the field after `9=` up to and including the delimiter before `10=` |
| `CheckSum(10)` | Sum of all preceding bytes modulo 256, rendered as three digits |
| Delimiter | SOH (`0x01`). A `\|` rendering is for logs only |
| `MsgSeqNum(34)` higher than expected | Send `ResendRequest(35=2)` with `BeginSeqNo(7)`/`EndSeqNo(16)`. `EndSeqNo=0` means infinity (all subsequent messages) |
| Resend already outstanding | Do not issue a second `ResendRequest`; the replayed messages are legitimately ahead of the expected number |
| `MsgSeqNum(34)` lower than expected, `PossDupFlag(43)≠Y` | Session-fatal: send `Logout(35=5)` with `Text(58)` explaining the too-low sequence number, then terminate the transport connection |
| `MsgSeqNum(34)` lower than expected, `PossDupFlag(43)=Y` | Discard as a duplicate |
| `SequenceReset(35=4)`, `GapFillFlag(123)≠Y` | Reset mode: apply `NewSeqNo(36)` regardless of the message's own sequence number — the one exemption from the too-low rule |
| `SequenceReset(35=4)`, `GapFillFlag(123)=Y` | Gap fill: sequence-checked normally, then advance the expectation to `NewSeqNo(36)` |

Sequence-number reset policy across sessions and trading weeks is venue-specific and is
not asserted here; take it from the venue's session specification.

## Sources

- CME Globex Self-Match Prevention: <https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457640025>
- CME iLink Binary Order Entry — Business Layer (SelfMatchPreventionID as Tag 2362): <https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/714211378>
- CME iLink 3 SBE schema v8.4 field and enum definitions (`SelfMatchPreventionInstruction` = `N`/`O`; `SenderId` 20 bytes; `SelfMatchPreventionId` 8-byte integer; `ManualOrderIndicator` 0/1), machine-generated mirror: <https://github.com/Open-Markets-Initiative/CSharp.Hft.Structs/blob/master/Cme/Cme.Futures.iLink3.Sbe.v8.4.cs>
- CME Group Rule 576 — Identification of Globex Terminal Operators: <https://www.cmegroup.com/rulebook/files/cme-group-Rule-576.pdf>
- CME Market Regulation Advisory Notice on Rule 536.B / Tag 1028: <https://www.cmegroup.com/rulebook/files/cme-group-Rule-536-B-Tag1028.pdf>
- CME Straight Through Processing (FIX 4.4): <https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457412452/CME+Straight+Through+Processing>
- iLink 2 CGW decommission date (2025-04-06) and iLink 2 SDK end-of-life, OnixS: <https://www.onixs.biz/cme-ilink-fix-order-entry.html>
- iLink 2 MSGW decommission (2021-03-28) and iLink 2 vs. iLink 3 protocol differences, OnixS: <https://www.onixs.biz/insights/difference-between-cme-ilink-2-and-ilink-3>
- FIX 4.2 `EndSeqNo(16)` — `0` means infinity: <https://www.onixs.biz/fix-dictionary/4.2/tagNum_16.html>
- FIX session layer (too-low `MsgSeqNum` without `PossDupFlag` terminates the session): <https://www.fixtrading.org/standards/>

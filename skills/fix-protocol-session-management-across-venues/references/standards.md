# Standards — fix-protocol-session-management-across-venues

Every rule the engine enforces is listed here with the clause it comes from.
Where the specification is silent, this file says so rather than inventing a
requirement.

## Session-layer rules (FIX 4.2 / 4.4 specification narrative)

| Rule enforced | Specification wording | Source |
|---|---|---|
| A `SequenceReset` may only ever raise the expected sequence number. | "The Sequence Reset can only increase the sequence number. If a sequence reset is received attempting to decrease the next expected sequence number the message should be rejected and treated as a serious error." | [FIX 4.4 dictionary, SequenceReset(35=4)](https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html) |
| `SequenceReset`-Reset mode is applied regardless of its own `MsgSeqNum` and never triggers a `ResendRequest`. | "Reset mode is indicated by the GapFillFlag field = 'N' or if the field is omitted"; "the receipt of a Sequence Reset - Reset mode message with an out of sequence MsgSeqNum should not generate resend requests." | [FIX 4.4 dictionary, SequenceReset(35=4)](https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html) |
| `SequenceReset`-GapFill is subject to normal sequencing. | "Gap Fill mode is indicated by GapFillFlag field = 'Y'"; "the MsgSeqNum of the Sequence Reset GapFill mode message should represent the beginning MsgSeqNum in the GapFill range." | [FIX 4.4 dictionary, SequenceReset(35=4)](https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html) |
| `EndSeqNo(16)=0` means infinity, and is the preferred form for gap recovery. | "EndSeqNo = 0 (represents infinity)"; "This latter approach is strongly recommended to recover from out of sequence conditions as it allows for faster recovery in the presence of certain race conditions." | [FIX 4.4](https://www.onixs.biz/fix-dictionary/4.4/msgType_2_2.html) and [FIX 4.2](https://www.onixs.biz/fix-dictionary/4.2/msgType_2_2.html) dictionaries, ResendRequest(35=2) |
| An inbound `TestRequest` is answered with a `Heartbeat` echoing `TestReqID(112)`. | "The opposite application responds to the Test Request with a Heartbeat containing the TestReqID"; "The opposite application includes the TestReqID in the resulting Heartbeat." | [FIX 4.4 dictionary, TestRequest(35=1)](https://www.onixs.biz/fix-dictionary/4.4/msgType_1_1.html) |
| `HeartBtInt(108)` is a single negotiated value used by both sides. | "The HeartBtInt field is used to declare the timeout interval for generating heartbeats (same value used by both sides)"; it "should be agreed upon by the two firms and specified by the Logon initiator and echoed back by the Logon acceptor." | [FIX 4.4 dictionary, Logon(35=A)](https://www.onixs.biz/fix-dictionary/4.4/msgType_A_65.html) |
| `HeartBtInt = 0` disables heartbeat generation. | Setting HeartBtInt to zero disables regular heartbeat generation; TestRequests may still be sent independently. | [FIX 4.4 dictionary, Heartbeat(35=0)](https://www.onixs.biz/fix-dictionary/4.4/msgType_0_0.html) |
| `SendingTime(52)` / `OrigSendingTime(122)` are UTC `UTCTimestamp`. | "Time of message transmission (always expressed in UTC ...)"; UTCTimestamp is "YYYYMMDD-HH:MM:SS (whole seconds) or YYYYMMDD-HH:MM:SS.sss (milliseconds) format, with colons, dash, and period required." | [FIX 4.4 tag 52](https://www.onixs.biz/fix-dictionary/4.4/tagNum_52.html); [InfoReach FIX 4.4 SendingTime](https://www.inforeachinc.com/fix-dictionary/fix_4_4_fields_sendingtime) |
| `OrigSendingTime(122)` carries the original transmission time on retransmission. | "Original time of message transmission ... when transmitting orders as the result of a resend request." | [FIX 4.4 tag 122](https://www.onixs.biz/fix-dictionary/4.4/tagNum_122.html) |

## Sequence-error handling

The FIX session layer distinguishes three below-expected cases. The engine
implements all three separately; collapsing them is the usual source of
duplicate-fill bugs.

| Condition | Required action | Source |
|---|---|---|
| `MsgSeqNum` < expected **without** `PossDupFlag=Y` | "a serious error has occurred ... The recipient of such a message should terminate the FIX session immediately via a Logout message." Recommended `Text(58)`: "MsgSeqNum too low, expecting X but received Y". | [OneChronos FIX Primer](https://www.onechronos.com/docs/fix/primer/) |
| `MsgSeqNum` < expected **with** `PossDupFlag=Y` | "If PossDupFlag=Y is set and the message sequence number is less than the receiver's inbound sequence number, the message is dropped." The expected sequence number is not changed. | [OneChronos FIX Primer](https://www.onechronos.com/docs/fix/primer/) |
| `SequenceReset` with `GapFillFlag=N` | Exempt from the termination rule above — its `MsgSeqNum` is ignored and the counterparty is told to "reset its expected incoming sequence number explicitly". | [OneChronos FIX Primer](https://www.onechronos.com/docs/fix/primer/); [FIX 4.4 dictionary](https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html) |
| Inbound `Logon` with `MsgSeqNum` > expected | "The recipient of a Logon should always process it immediately, even if their sequence number is too high. After sending a Logon confirmation back, a ResendRequest is sent if a message gap was detected." Wait for recovery (or a TestRequest round trip) before sending queued messages, or the counterparty issues a ResendRequest per message. | [OneChronos FIX Primer](https://www.onechronos.com/docs/fix/primer/) |
| `SenderCompID(49)`/`TargetCompID(56)` mismatch | Validate every message against the values established at Logon; "a discrepancy in the SenderCompID + TargetCompID pair should result in the termination of the FIX connection by sending a Logout message using the Text field to indicate the reason." | [FIX Session Layer, FIX Trading Community](https://www.fixtrading.org/standards/fix-session-layer-online/) |

## Heartbeat timing — what the specification does *not* say

**The FIX specification defines no numeric heartbeat timeout.** It states that
when no data arrives for `HeartBtInt` plus "some reasonable transmission time"
a `TestRequest` should be sent, and that if no response arrives after the same
interval again "the connection should be considered lost and corrective action
be initiated" — and it leaves "reasonable transmission time" deliberately
undefined ([FIX 4.4 Heartbeat(35=0)](https://www.onixs.biz/fix-dictionary/4.4/msgType_0_0.html)).

Any specific multiplier is therefore an implementation or venue choice, never a
FIX requirement. The engine's defaults reproduce QuickFIX/J:

| Constant | Value | Effect |
|---|---|---|
| `DEFAULT_TEST_REQUEST_DELAY_MULTIPLIER` | `0.5` | `isTestRequestNeeded()` fires at `(1 + 0.5) × HeartBtInt` — the first TestRequest at **1.5 ×**. |
| `DEFAULT_HEARTBEAT_TIMEOUT_MULTIPLIER` | `1.4` | `isTimedOut()` fires at `(1 + 1.4) × HeartBtInt` — disconnect at **2.4 ×**. |

Source: [QuickFIX/J `Session.java`](https://github.com/quickfix-j/quickfixj/blob/master/quickfixj-core/src/main/java/quickfix/Session.java)
and [`SessionState.java`](https://github.com/quickfix-j/quickfixj/blob/master/quickfixj-core/src/main/java/quickfix/SessionState.java).

Venues frequently mandate their own figure. CME iLink 3 declares a session
failed when nothing is received within **2 × `KeepAliveInterval`**
([CME Client Systems Wiki, Fault Tolerance](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457671413/Fault+Tolerance)).
Configure `test_request_multiplier` / `disconnect_multiplier` from the venue
specification; the defaults are a starting point, not evidence of compliance.

## Scope limits of this engine

- Binary session layers (CME iLink 3 FIXP/SBE, Eurex T7 ETI, Nasdaq OUCH) do not
  follow these rules. Eurex T7 ETI in particular expects `MsgSeqNum = 1` on every
  connection including reconnects and provides **no** sequence recovery mechanism
  ([T7 ETI Manual §6.6](https://www.eurex.com/resource/blob/4305946/dcadfeef8842b1a84b0e9afa439802e1/data/T7_R.13.1_Enhanced_Trading_Interface_-_Manual_Version_1.pdf)).
- Sequence numbers are held in memory only. Durable storage across process
  restarts is the caller's responsibility.
- Session recovery is not order recovery. A resynchronised message stream says
  nothing about which orders the venue still holds.

## Regulatory touchpoints

**US — broker-dealers with market access.** SEC Rule 17 CFR 240.15c3-5(c)(1)(ii)
requires controls reasonably designed to prevent erroneous orders "by rejecting
orders that exceed appropriate price or size parameters, on an order-by-order
basis or over a short period of time, **or that indicate duplicative orders**".
A session layer that rewinds its inbound sequence on a `PossDup` retransmission,
or accepts a `SequenceReset` that lowers `NewSeqNo`, re-applies execution reports
and can drive duplicate order submission. Note the scope: this obligation
attaches to the broker-dealer providing market access, not to every trading firm.

**EU / UK — investment firms engaged in algorithmic trading.** Commission
Delegated Regulation (EU) 2017/589 (**RTS 6**), Article 14 "Business continuity
arrangements" ([FCA Handbook, assimilated version](https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1566)),
requires at Art. 14(2)(g) "alternative arrangements for the investment firm to
manage outstanding orders and positions" and at Art. 14(4) an **annual** test of
those arrangements. A session-recovery procedure is part of that evidence, and
the `FixSessionAuditReport` stream is the artefact that makes it auditable.

**Not applicable by default:** Regulation SCI (17 CFR 242.1000 et seq.) binds
*SCI entities* — exchanges, clearing agencies, plan processors and ATSs above the
volume thresholds — not ordinary members or buy-side firms. RTS 7 (Regulation
(EU) 2017/584) addresses **trading venues**, not participants. Neither is
authority for a member firm's FIX session design.

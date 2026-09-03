# Pre-Flight Checklist — FIX Session Management

## Configuration

- [ ] `SenderCompID(49)` / `TargetCompID(56)` match the venue's onboarding pack, and **one engine instance per venue session** — no shared sequence state.
- [ ] `HeartBtInt(108)` agreed with the venue; the acceptor echoes the initiator's value.
- [ ] `test_request_multiplier` / `disconnect_multiplier` set from the **venue specification**, not left at the QuickFIX/J-derived defaults (1.5 / 2.4), unless the venue is silent.
- [ ] `resend_buffer_size` sized against peak message rate and the largest resend the venue may request.
- [ ] `out_seq_num` and `expected_in_seq_num` persisted durably and restored on start-up — this engine keeps them in memory only.
- [ ] `validate_comp_ids` left enabled.

## Sequence-number discipline (the duplicate-fill gates)

- [ ] `PossDupFlag(43)=Y` below the expected sequence is **discarded**, and the expected sequence is left unchanged.
- [ ] `MsgSeqNum` below expected **without** `PossDupFlag` sends `Logout(35=5)` with both numbers in `Text(58)` and terminates — the engine's own state changes, not just the report's.
- [ ] `SequenceReset(35=4)` with `NewSeqNo(36)` at or below the expected sequence is **rejected** in both Gap Fill and Reset mode.
- [ ] `SequenceReset`-Reset (`GapFillFlag(123)` absent or `N`) is applied without gap-checking its own `MsgSeqNum`, and issues no `ResendRequest`.
- [ ] A gap issues exactly **one** `ResendRequest(BeginSeqNo=expected, EndSeqNo=0)`; later out-of-sequence messages do not trigger more.
- [ ] The gap-triggering message is held, not applied — the expected sequence does not advance across a gap.
- [ ] An inbound `Logon` carrying a gap is accepted **first**, then recovered; the expected sequence is not advanced to the Logon's own number.

## Liveness

- [ ] `Heartbeat(35=0)` emitted after `HeartBtInt` of outbound idleness.
- [ ] One outstanding `TestRequest(35=1)` at a time, with a unique `TestReqID(112)`.
- [ ] Inbound `TestRequest` answered with a `Heartbeat` **echoing Tag 112**.
- [ ] Timeout threshold triggers teardown — and order reconciliation, not an assumption that the venue cancelled anything.
- [ ] Heartbeat timing driven by a timer or event loop, not a spin loop.

## Wire correctness

- [ ] `SendingTime(52)` formatted `YYYYMMDD-HH:MM:SS.sss` — hyphen, no `T`, no `Z`.
- [ ] Retransmitted messages carry their **original** `MsgSeqNum`, `PossDupFlag(43)=Y` and `OrigSendingTime(122)`, and consume no new outbound sequence numbers.
- [ ] Administrative messages are gap-filled on resend, never replayed.

## Shutdown and recovery

- [ ] `Logout(35=5)` handshake completes both ways before the socket closes.
- [ ] Final sequence numbers persisted at logout.
- [ ] A resend range that has aged out of the buffer is escalated as a reconciliation event, not logged and ignored.
- [ ] Working orders reconciled against the venue after any session loss, before flow resumes.

## Sign-off

- [ ] `python -m unittest discover -s skills/fix-protocol-session-management-across-venues/scripts` — 48 tests, 100% pass.
- [ ] Venue conformance/certification run passed against the venue's own session test cases.
- [ ] Business-continuity test of the recovery procedure recorded (RTS 6 Art. 14(4) requires this annually for in-scope EU/UK firms).

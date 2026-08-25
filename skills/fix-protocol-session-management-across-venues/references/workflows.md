# Workflows — fix-protocol-session-management-across-venues

Deep procedure reference. Clause citations for every rule are in
`references/standards.md`.

## 1. Logon handshake

1. Restore `out_seq_num` and `expected_in_seq_num` from durable storage. This
   engine holds them in memory only — a process restart without restoration
   fails on the first message.
2. `initiate_logon()` sends `Logon(35=A)` with `HeartBtInt(108)` and
   `EncryptMethod(98)=0`. State ⟶ `LOGON_SENT`.
3. On the venue's `Logon`:
   - `MsgSeqNum` == expected ⟶ `LOGGED_IN`, advance expected.
   - `MsgSeqNum` > expected ⟶ **accept the Logon first**, then issue
     `ResendRequest(BeginSeqNo=expected, EndSeqNo=0)` and hold the expected
     sequence where it was. State ⟶ `RESEND_REQUEST_SENT`.
   - `MsgSeqNum` < expected without `PossDupFlag` ⟶ fatal; Logout and terminate.
4. `ResetSeqNumFlag(141)=Y` resets both directions to 1. Agree it with the venue
   in advance; an unagreed reset is itself a session failure.
5. Do not release queued application messages until recovery has completed or a
   `TestRequest` round trip has confirmed both sides are synchronised.

**Failure mode:** advancing the expected sequence to the Logon's own number.
The session comes up looking healthy while every message the venue sent in the
interim — execution reports included — is gone with no recovery path.

## 2. Heartbeat and liveness monitoring

Call `check_liveness(now)` from a timer or event loop and transmit what it
returns, in order. Poll a monotonic clock; never spin.

| Condition | Action |
|---|---|
| Outbound idle ≥ `HeartBtInt` | Send `Heartbeat(35=0)`. |
| Inbound idle ≥ `test_request_multiplier × HeartBtInt` | Send **one** `TestRequest(35=1)` with a unique `TestReqID(112)`. |
| Inbound `TestRequest` | Reply `Heartbeat` echoing its `TestReqID(112)`. |
| Inbound `Heartbeat` echoing the outstanding `TestReqID` | Liveness confirmed; clear the probe. |
| `is_timed_out()` | Session presumed dead: tear down, then **reconcile working orders** before reconnecting. |
| `HeartBtInt == 0` | Heartbeat generation disabled entirely. |

The multipliers are QuickFIX/J defaults, not FIX requirements. Set them from the
venue specification where it states one.

**Failure modes:** re-issuing the `TestRequest` on every poll (it forces nothing
extra and burns message quota); answering an inbound `TestRequest` with a bare
`Heartbeat` that omits Tag 112; and reading a heartbeat timeout as proof the
venue cancelled your orders — cancel-on-disconnect is a per-session
configuration, not a default.

## 3. Inbound message processing — check order

The ordering below is normative. Reordering steps 2 and 4 reintroduces the
reset/resend loop; reordering 3 and 6 reintroduces duplicate-fill processing.

1. **Pre-logon guard.** Anything other than `Logon` while `DISCONNECTED` ⟹ drop
   the connection. Do not touch sequence numbers.
2. **CompID guard.** `SenderCompID`/`TargetCompID` must mirror this session's
   pair. A mismatch ⟹ `Logout` and terminate; the message counts as neither
   session activity nor sequence progress.
3. **Unexpected `Logon`.** A `Logon` on an already-established session is a
   session-level error, not application traffic.
4. **`SequenceReset`-Reset (`GapFillFlag` absent or `N`).** Evaluated *before*
   gap detection: its `MsgSeqNum` is ignored and it must never provoke a
   `ResendRequest`. `NewSeqNo` ≤ expected ⟹ `Reject(35=3)`, sequence unchanged.
5. **`PossDupFlag=Y` below expected.** Discard. Change nothing.
6. **`MsgSeqNum` > expected.** `ResendRequest(BeginSeqNo=expected, EndSeqNo=0)`,
   state ⟶ `RESEND_REQUEST_SENT`, hold the message, leave expected alone. If a
   request is already outstanding, issue no further one.
7. **`MsgSeqNum` < expected without `PossDupFlag`.** `Logout` naming both
   numbers in `Text(58)`; terminate.
8. **In sequence.** Dispatch by `MsgType`:
   - `SequenceReset`-GapFill ⟶ `NewSeqNo` must exceed expected, else `Reject`.
   - `TestRequest` ⟶ `Heartbeat` echoing Tag 112.
   - `Heartbeat` ⟶ clear the outstanding probe if the `TestReqID` matches.
   - `ResendRequest` ⟶ serve it (§4).
   - `Logout` ⟶ acknowledge unless we initiated; state ⟶ `DISCONNECTED`.
   - Anything else ⟶ hand to the application; advance expected.

## 4. Serving an inbound ResendRequest

`build_resend_response(begin, end)`; `end` of `0` or beyond the highest sent
means "everything to date".

- **Application messages** are replayed under their **original** `MsgSeqNum`
  with `PossDupFlag(43)=Y` and `OrigSendingTime(122)` set to the original
  `SendingTime`. Retransmission must not consume new outbound sequence numbers.
- **Administrative messages** are not replayed. Contiguous runs collapse into one
  `SequenceReset`-GapFill whose `MsgSeqNum` is the start of the run and whose
  `NewSeqNo(36)` is the sequence immediately after it.
- **Ranges aged out of the resend buffer** can only be gap-filled. This is
  spec-legal but means real application messages were skipped: the engine logs it
  at `ERROR` and it must be treated as a reconciliation trigger, not a clean
  recovery. Size `resend_buffer_size` against your message rate and the largest
  resend a venue may plausibly request.

## 5. Sequence resynchronisation and graceful logout

1. A gap closes when the missing messages arrive or a `SequenceReset` advances
   past them; state returns to `LOGGED_IN`.
2. `initiate_logout(reason)` sends `Logout(35=5)` and moves to `LOGOUT_SENT`.
3. The counterparty's `Logout` completes the handshake ⟶ `DISCONNECTED`. If the
   venue initiated, the engine emits the acknowledging `Logout` for you.
4. Persist the final `out_seq_num` and `expected_in_seq_num`. They are what the
   next logon depends on.

**Failure mode:** closing the socket immediately after sending `Logout`. You
lose the confirmation that both sides agree on the final sequence numbers, which
is exactly what determines whether the next logon succeeds.

## 6. Session recovery is not order recovery

Resynchronising the message stream tells you nothing about which orders the
venue still holds. After any session loss, reconcile working orders against the
venue's own view — `OrderMassStatusRequest(35=AF)`, an order-book restatement, or
a drop copy — before resuming flow. See
`exchange-gateway-redundancy-and-failover-testing` for the in-flight order
problem and `order-placement-idempotency` for the duplicate-submission guard.

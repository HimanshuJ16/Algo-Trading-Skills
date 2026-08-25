# Workflows — exchange-gateway-redundancy-and-failover-testing

## 0. Establish the venue profile first

Before any failover logic is written, answer these four questions from the venue's
own specification and encode the answers in a `VenueRecoveryProfile`:

1. **Sequence policy** — does the standby session continue its own outbound
   `MsgSeqNum`, negotiate `ResetSeqNumFlag(141)=Y` on logon, or restart at 1?
   (Eurex T7 ETI: always 1, no recovery, gaps disconnect the session.)
2. **Order-state recovery** — `OrderStatusRequest(35=H)` /
   `OrderMassStatusRequest(35=AF)`, an order book restatement broadcast, or a drop
   copy? (ETI supports no status inquiry at all.)
3. **Non-persistent order survival** — does the venue delete lean/non-persistent
   orders and quotes when the session drops? (ETI does, and does not restate them.)
4. **Resend marking** — `PossResend(97)=Y`, or nothing at all?

A profile you have not checked against the venue manual is a guess, and the
generic FIX default is wrong for at least one venue in this list.

## 1. Liveness monitoring

- Sample `heartbeat_delay_ms`, `latency_rtt_ms` and `tcp_connected` on both the
  active and standby sessions at a cadence well inside the heartbeat window.
- Derive the heartbeat-loss threshold from the negotiated interval:
  `heartbeat_timeout_from_interval(heart_bt_int_ms, multiplier)`. CME designates a
  fault-tolerant session failed at 2 × `KeepAliveInterval`; 1.5 × `HeartBtInt` is
  also common. A raw millisecond constant tells you nothing.
- Keep the standby's health sampled too. An unmonitored standby is an assumption,
  not a redundancy.

## 2. Failure confirmation — three distinct signals, three distinct responses

| Signal | What it proves | Correct response |
|---|---|---|
| Socket closed | The session cannot transmit | Trigger confirmed; no fence needed |
| Heartbeat delay > threshold, socket up | Nothing yet — the peer may be idle | Send `TestRequest(35=1)`; declare failure only if it goes unanswered |
| RTT > SLA for N consecutive samples | The session is slow, **not** dead | Trigger only if latency failover is deliberately enabled — and fence first |

Never collapse these into one boolean. A slow-but-ordered session is often
preferable to a switch, and a single missed heartbeat is routine.

## 3. Fencing — the split-brain gate

A session whose socket is alive can still put orders on the wire. If the standby
is promoted beside it, both sessions send, both sets of orders execute, and the
position is unreconstructable.

Before promotion, one of the following must be true:

- the failing session's socket is closed (it physically cannot send), **or**
- the operator has fenced it: FIX `Logout(35=5)` and socket close, credential
  revocation, venue-side session kill, or a cancel-on-disconnect that the venue
  has confirmed.

Only then re-audit with `fence_confirmed=True`. Treat a fence you have not
verified as no fence.

## 4. Standby pre-flight and role switch

1. Verify the standby is `STANDBY`, connected, and inside every threshold.
   No promotable standby $\implies$ `FAILOVER_FAILED`: stop order flow and escalate
   to the manual procedure (venue-side purge, phone-to-desk). Promoting a dead
   standby is worse than not failing over.
2. Demote the failing node first: `QUIESCED` if its socket was alive, otherwise
   `DISCONNECTED`.
3. Promote the standby to `ACTIVE`.
4. A repaired node is **not** automatically eligible again. An operator restores it
   to `STANDBY` explicitly, after verifying its state — otherwise a second failure
   silently hands flow back to a node nobody checked.

## 5. Sequence resolution

- `CONTINUE_SESSION`: next outbound = the standby's **own** `last_sent_seq_num + 1`.
- `RESET_ON_LOGON`: logon with `ResetSeqNumFlag(141)=Y`; both directions reset to 1.
  Bilateral — an unagreed reset is itself a session failure.
- `RESTART_AT_ONE`: log on at 1 (Eurex T7 ETI; every connection including reconnects).

Under no policy is the failed session's sequence number copied into the standby.
Sequence numbers are a property of a session, not of a firm.

## 6. In-flight order reconciliation

For every order whose last known state is `PENDING_NEW`, `PENDING_CANCEL` or
`PENDING_REPLACE`, three outcomes are indistinguishable from the client side: the
venue never received it, the venue holds it, or the venue already filled it.

1. **Classify, don't act.** Completed orders are left alone. Non-persistent orders
   at a venue that deletes them are gone with certainty — including acknowledged,
   resting ones — and must be re-entered as **new** orders under **new**
   `ClOrdID`s (residual quantity only, where they were partially filled).
   Acknowledged working orders the venue keeps carry no resend risk but must still
   be confirmed against the venue's own view. Everything else is
   `RECONCILE_REQUIRED`.
2. **Query the venue.** `OrderMassStatusRequest(35=AF)` returns an
   `ExecutionReport` per matching order with `ExecType(150)=I`; on ETI, consume the
   order book restatement (and request retransmission by `ApplMsgID` if the session
   was not logged on when it was published). A drop copy is an acceptable
   independent source.
3. **Reconcile executions against the binding record.** On ETI all Execution
   Reports are preliminary; an execution with no matching `TradeCaptureReport(AE)`
   must be discarded rather than booked.
4. **Resend only what came back absent.** Mark those messages `PossResend(97)=Y` —
   *not* `PossDupFlag(43)=Y`, which asserts retransmission under the same sequence
   number and belongs to session-layer gap fill with `OrigSendingTime(122)`.
   Because PossResend is handled by the counterparty's business logic (usually
   ClOrdID dedup) rather than its session layer, it is a hint, never a guarantee.
5. **An unresolved order is not a resend candidate.** If the venue's answer is
   still ambiguous, escalate. Guessing here is the duplicate execution.

## 7. Post-failover controls

- Confirm the promoted session enforces the same pre-trade risk controls as the
  primary. A failover path that bypasses them is an uncontrolled market-access path.
- Reconcile positions against the venue's own record before resuming strategy flow,
  not after.
- Record the trigger, the fence evidence, the sequence decision, every
  reconciliation verdict, and the measured recovery time. This is the audit trail
  the annual business-continuity test is judged on.

## 8. Failover drill (what to actually test)

Run each of these against the harness and against staging, at least annually:

1. Kill the primary socket $\implies$ clean promotion, correct standby sequence.
2. Stall heartbeats with the socket alive $\implies$ `TEST_REQUEST_REQUIRED`, then
   `FAILOVER_BLOCKED`, then success only after a confirmed fence.
3. Degrade latency for fewer than N samples $\implies$ no failover.
4. Fail the standby too $\implies$ `FAILOVER_FAILED` and a manual escalation path
   that someone has actually rehearsed.
5. Fail over with orders in every state, including one the venue silently filled
   during the outage $\implies$ verify no duplicate reaches the wire.
6. Fail over twice in a row $\implies$ verify flow is not handed back to the
   unrepaired node.

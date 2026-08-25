# Pre-Flight Checklist — Gateway Redundancy & Failover

## Venue profile
- [ ] Sequence policy taken from the venue's own manual (continue / `ResetSeqNumFlag(141)=Y` / restart at 1), not from a generic default?
- [ ] Order-state recovery method confirmed (`OrderMassStatusRequest(35=AF)`, restatement broadcast, or drop copy) — and confirmed the venue supports it at all?
- [ ] Known whether the venue deletes non-persistent orders and quotes on session loss, and whether it restates the survivors?
- [ ] Confirmed whether the **venue** owns failover (e.g. an iLink fault-tolerant group), in which case the client does not promote independently?

## Detection
- [ ] Heartbeat-loss threshold derived from the negotiated `HeartBtInt`/`KeepAliveInterval`, not a hard-coded millisecond constant?
- [ ] `TestRequest(35=1)` issued and left unanswered before a live-socket session is declared dead?
- [ ] Latency-triggered failover either disabled, or gated on N consecutive breaching samples?
- [ ] Standby health monitored continuously, not assumed?

## Safety gates
- [ ] Failing session provably fenced (socket closed, logged out, credentials revoked, or venue-confirmed) **before** the standby is promoted?
- [ ] Standby pre-flighted, with a rehearsed manual escalation path when no standby is promotable?
- [ ] A repaired node requires explicit operator restoration to `STANDBY` before it is eligible again?

## Sequence & orders
- [ ] Standby logs on with **its own** sequence number — the failed session's `MsgSeqNum` is never copied across?
- [ ] Every unacknowledged in-flight order reconciled against the venue before any resend?
- [ ] Resent orders marked `PossResend(97)=Y` — never `PossDupFlag(43)=Y`, which asserts retransmission under the same sequence number?
- [ ] Orders the venue deleted re-entered as **new** orders with **new** `ClOrdID`s, not resent?
- [ ] Orders still `UNKNOWN` after reconciliation escalated rather than resent?

## After the switch
- [ ] Promoted session enforcing the same pre-trade risk controls as the primary?
- [ ] Positions reconciled against the venue record before strategy flow resumes?
- [ ] Recovery time measured end to end (connect + logon + restatement + reconciliation) rather than quoted from an in-process timer?
- [ ] Trigger, fence evidence, sequence decision and reconciliation verdicts recorded for the audit trail?
- [ ] Full failover drill run and documented within the last 12 months (RTS 6 Art. 14(4) for EU/UK investment firms)?

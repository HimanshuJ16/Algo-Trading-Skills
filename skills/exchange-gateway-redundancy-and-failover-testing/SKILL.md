---
name: exchange-gateway-redundancy-and-failover-testing
description: >-
  Active-Standby order-entry gateway failover decision engine and test harness: confirms liveness before failing over, fences the failing session against split-brain, resolves the standby's own MsgSeqNum under the venue's sequence policy, and forces reconciliation of in-flight orders before any PossResend.
domain: Venue Integration & Protocols
subdomain: Exchange Connectivity & High Availability
tags: ["gateway-redundancy", "fix-failover", "active-standby", "split-brain-prevention", "poss-resend", "order-reconciliation", "high-availability"]
brokers_frameworks: ["FIX 4.4 / 5.0 SP2", "Eurex T7 ETI", "CME iLink 3", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading system holds redundant order-entry sessions to a venue (Active-Standby FIX sessions, an ETI session pair, an iLink fault-tolerant group) and needs a defensible answer to three questions at the moment the active session degrades: *should we fail over*, *what sequence number does the standby log on with*, and *what happens to the orders we sent but never got acknowledged*. It is a decision engine and a test harness for failover drills — it performs no I/O, opens no sockets, and sends no orders.

The dangerous part of a gateway failover is not the switch. It is the set of orders in flight when the session died: an unacknowledged `PENDING_NEW` may be resting at the venue, may have filled, or may never have arrived, and the three are indistinguishable from the client side. This engine refuses to resend any of them until the venue says which.

## When NOT to Use

- **As a FIX/ETI session implementation.** It decides; your session layer connects, logs on, cancels and resends. It cannot detect a dead socket — you sample health and pass it in.
- **As proof of an RTO.** `decision_latency_ms` measures this in-process decision only. TCP connect, logon, order book restatement and reconciliation dominate real recovery and happen outside this module. No RTO is reported unless you supply a measured `standby_activation_ms`.
- **For N-way gateway pools or cross-venue rerouting.** Two nodes, one venue. For routing around a venue outage entirely see `smart-order-router-failover-on-venue-outage`; for network/region-level failover see `multi-region-failover-for-broker-connectivity`.
- **Where the venue itself owns failover.** CME iLink 3 fault tolerance has the client Establish on *both* the primary and backup market segment gateway and the exchange disconnects both on failure — the promotion decision is not the client's to make. Model the venue's documented mechanism, don't override it.
- **With a cold standby.** The engine promotes only a session that is already established and inside every health threshold — a session that has never logged on cannot be pre-flighted from health samples. Keep the standby warm (this is also what avoids a connect-and-logon spike in the middle of an outage), or establish it before auditing.
- **Without a venue profile you have checked.** `GENERIC_FIX_PROFILE` is a generic default, not a venue specification. Eurex T7 ETI differs from it on every field that matters.

## Prerequisites

- Two `GatewayNodeConfig` sessions (exactly one `ACTIVE`, one `STANDBY`) with sampled health: `heartbeat_delay_ms`, `latency_rtt_ms`, `tcp_connected`, `test_request_unanswered`, `consecutive_latency_breaches`.
- A heartbeat-loss threshold **derived from the negotiated interval** via `heartbeat_timeout_from_interval()` — a bare millisecond constant is meaningless without the `HeartBtInt`/`KeepAliveInterval` it is measured against.
- A `VenueRecoveryProfile` for the venue: sequence policy, order-state recovery method, whether non-persistent orders survive a disconnect, and the resend marking (if any).
- In-flight orders keyed by unique `ClOrdID`, each flagged `persistent` or not.
- A way to fence the failing session (session logout, socket close, credential revocation, venue-side cancel-on-disconnect).

## Workflow

1. **Confirm liveness before declaring death.**
   - Socket down $\implies$ trigger confirmed; the session cannot transmit.
   - Heartbeat delay over threshold but socket up $\implies$ **not yet a trigger**. The FIX liveness procedure is to send `TestRequest(35=1)` and only declare the session dead when it goes unanswered. The engine returns `TEST_REQUEST_REQUIRED` and changes nothing.
   - Latency SLA breach $\implies$ requires `min_consecutive_latency_breaches` consecutive samples. One RTT spike is noise, and failing over on noise abandons a working session. Latency-triggered failover is **off by default**.
2. **Fence before promoting — the split-brain gate.**
   - If the failing session still holds a live socket it can still put orders on the wire. Promoting the standby beside it doubles the order flow. The engine returns `FAILOVER_BLOCKED` with `FENCE_ACTIVE_GATEWAY` until the caller re-audits with `fence_confirmed=True`.
   - **Decision point:** a heartbeat timeout is *not* a disconnect. It is exactly the case where fencing is mandatory.
3. **Pre-flight the standby, then switch.**
   - The standby must be `STANDBY`, connected, and inside every threshold. A repaired node stays ineligible until an operator explicitly restores it to `STANDBY`, so a second failure never hands flow back to an unverified node.
   - No promotable standby $\implies$ `FAILOVER_FAILED`, escalate to the manual procedure. Promoting a dead standby is worse than not failing over.
   - Demote first (`QUIESCED` if the socket was alive, `DISCONNECTED` if not), then promote.
4. **Resolve the standby's own sequence number.**
   - `CONTINUE_SESSION`: standby continues *its own* outbound `MsgSeqNum`. **Never copy the failed session's sequence across** — sequence numbers belong to a session (a CompID pair), not to a firm.
   - `RESET_ON_LOGON`: `ResetSeqNumFlag(141)=Y`, bilaterally agreed. An unagreed reset is itself a session failure.
   - `RESTART_AT_ONE`: Eurex T7 ETI — every connection, reconnects included, logs on at `MsgSeqNum=1`; there is no sequence recovery.
5. **Plan order recovery — reconcile, never resend blind.**
   - Completed states (`FILLED`, `CANCELED`, `REJECTED`, …) $\implies$ `NO_ACTION`.
   - Non-persistent orders at a venue that deletes them on disconnect $\implies$ `REENTER_AS_NEW_ORDER` under a **new** `ClOrdID`, and for a partial fill only the residual quantity. They are gone with certainty; resending the original ClOrdID is a duplicate submission, not a recovery. **An acknowledgement does not protect them** — a resting `NEW` lean order is deleted exactly like an unacknowledged one, so this test precedes the "it's confirmed, leave it alone" test.
   - Acknowledged, still-working orders (`NEW`, `PARTIALLY_FILLED`) that the venue keeps $\implies$ `NO_ACTION`: no resend risk, but still confirm them against the venue's own view before resuming flow.
   - Everything else $\implies$ `RECONCILE_REQUIRED` via the venue's method: `OrderMassStatusRequest(35=AF)` / `OrderStatusRequest(35=H)` answered by `ExecutionReport` with `ExecType(150)=I`, an order book restatement, or a drop copy.
6. **Resend only what reconciliation cleared.**
   - `build_resend_plan()` returns **copies** of the orders the venue confirmed absent, marked `PossResend(97)=Y`. A still-`UNKNOWN` verdict raises rather than resends.
   - **Decision point:** `PossResend(97)`, not `PossDupFlag(43)`. Tag 43 means "possible retransmission of message *with this sequence number*" and belongs to session-layer gap fill with `OrigSendingTime(122)`; a new order over a new session under a new sequence number is a `PossResend`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Resending unacknowledged orders because the session dropped.** A lost acknowledgement is not a lost order. The venue may be holding it, or may already have filled it. Blind retransmission of every `PENDING_NEW` is the duplicate-execution mechanism, not the guard against it — and duplicative orders are exactly what SEC Rule 15c3-5(c)(1)(ii) requires a US broker-dealer's controls to reject.
- **Marking a failover resend `PossDupFlag(43)=Y`.** Wrong field. Tag 43 asserts a retransmission under the *same* sequence number and is validated against the expected `MsgSeqNum` with `OrigSendingTime(122)`; sending it on a fresh sequence invites a session-level reject. The failover case is `PossResend(97)=Y`, and because PossResend is handled by *business* logic (typically ClOrdID dedup) rather than the session layer, it is a hint to the counterparty — never a substitute for reconciling first.
- **Copying the failed session's `MsgSeqNum` into the standby.** Taking `max(primary, secondary)` desynchronises the standby's own session; the counterparty rejects and logs out. On Eurex T7 ETI it is doubly wrong: a reconnect must log on at 1, and any gap or duplicate is rejected with a sequence error and disconnection.
- **Promoting the standby while the primary's socket is alive.** A heartbeat timeout leaves a fully transmit-capable session in place. Two live sessions means duplicated flow and a position no one can reconstruct. Fence first.
- **Failing over on a single latency sample.** RTT is noisy; one spike is not a degraded gateway. Require consecutive breaches, and remember that a slow session is still an *ordered* session — dropping it may be worse than the latency.
- **Treating "our socket died" as "the venue cancelled our orders".** Cancel-on-disconnect is a per-venue, per-session *configuration*, not a default. Eurex deletes non-persistent orders and quotes on session loss but restates persistent ones; verify which regime applies before assuming an empty book.
- **Reporting an in-process timing as an RTO.** Timing a few attribute assignments and calling it "recovery under 50 ms" is a fabricated SLA. Measure connect + logon + restatement + reconciliation, and note that reconciliation is usually the long pole.
- **Silently dropping risk controls on the standby path.** Pre-trade controls must apply to the promoted session exactly as they did to the primary; a failover path that bypasses them is an uncontrolled market-access path.

## Verification

- Instantiate `ExchangeGatewayRedundancyEngine` with a primary (`ACTIVE`, `last_sent_seq_num=150`) and secondary (`STANDBY`, `last_sent_seq_num=145`) and `max_heartbeat_delay_ms=heartbeat_timeout_from_interval(30_000)`.
- **Split-brain gate:** breach the latency SLA for 5 consecutive samples with `tcp_connected=True` $\implies$ `FAILOVER_BLOCKED`, primary still `ACTIVE`, secondary still `STANDBY`. Re-audit with `fence_confirmed=True` $\implies$ `FAILOVER_SUCCESS`, primary `QUIESCED`.
- **Liveness gate:** heartbeat delay above threshold with `test_request_unanswered=False` $\implies$ `TEST_REQUEST_REQUIRED` and no role change.
- **Sequence:** after failover verify `standby_next_out_seq_num == 146` (the standby's own 145 + 1) and **not** 150 or 151. With `EUREX_T7_ETI_PROFILE` verify it is `1`.
- **Order recovery:** pass one `PENDING_NEW`, one `FILLED`, one `PENDING_CANCEL` $\implies$ two `RECONCILE_REQUIRED`, one `NO_ACTION`, and confirm the caller's order objects are unmutated. A non-persistent order under `EUREX_T7_ETI_PROFILE` $\implies$ `REENTER_AS_NEW_ORDER`.
- **Resend gate:** `build_resend_plan` with an `UNKNOWN` verdict raises; with `ABSENT_AT_VENUE` it returns a copy carrying `poss_resend=True` while the original stays untouched; under `EUREX_T7_ETI_PROFILE` it raises `NotImplementedError`.
- **No standby available:** both sockets down $\implies$ `FAILOVER_FAILED` with `NO_HEALTHY_STANDBY`, and no promotion.
- Run `python scripts/test_exchange_gateway_redundancy_and_failover_testing.py` and confirm 100% pass rate.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `smart-order-router-failover-on-venue-outage`
- `multi-region-failover-for-broker-connectivity`
- `order-placement-idempotency`
- `disaster-recovery-runbook-for-full-region-outage`

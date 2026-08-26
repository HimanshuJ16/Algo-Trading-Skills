---
name: matching-engine-throttle-and-message-gapping-detection
description: >-
  Use when a bot submits orders over an exchange order-entry session and must not be
  rejected or disconnected for exceeding the venue's message-rate limit, and must detect
  inbound sequence gaps so lost execution reports are retransmit-requested rather than
  silently missed. Peak-window rate counting, per-session sequence state that holds a gap
  open until it is actually filled, and explicit handling of the low-sequence-number
  regression the FIX session layer requires a logout for.
domain: Market Microstructure Latency
subdomain: Exchange Protocol Reliability & Session Governance
tags: ["matching-engine", "throttle-detection", "sequence-gapping", "cme-ilink3", "fixp", "nasdaq-ouch", "moldudp64", "retransmit-request", "rate-limiting", "session-recovery", "possdup"]
brokers_frameworks: ["CME iLink 3 (FIXP)", "FIX 4.2/4.4 Session Layer", "Nasdaq OUCH / SoupBinTCP", "Nasdaq MoldUDP64 / ITCH", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on any order-entry session that (a) is counted against a venue message-rate
limit and (b) carries a session-layer sequence number. Both halves are failure modes that
end trading rather than degrade it:

- **Throttle.** Venues do not queue your overflow — they **reject**, then **disconnect**.
  CME Globex counts iLink messages over a pre-defined interval that begins with the first
  message processed; past the reject threshold subsequent messages are rejected, and past
  the larger terminate threshold the session ends. Nasdaq applies a per-order-entry-port
  limit above which messages may be rejected. Losing the session mid-position is a
  materially worse outcome than slowing down.
- **Gapping.** FIX `MsgSeqNum(34)`, the FIXP/iLink 3 sequence number and MoldUDP64 packet
  sequence numbers all let the receiver see that messages were lost. A missed execution
  report means your local position is wrong while you keep trading on it.

It also serves the **EU/UK** pre-trade control that RTS 6 Article 15(1)(d) makes mandatory
— a hard counter against a known maximum message limit. See `references/standards.md` for
jurisdiction and exact wording.

## When NOT to Use

- **On a plain SoupBinTCP/OUCH session, for gap detection.** SoupBinTCP numbers messages
  implicitly — only the Login Accepted packet carries an explicit sequence number, and TCP
  guarantees ordered delivery within the session, so an in-session gap cannot be observed.
  Recovery there is *reconnect and set Requested Sequence Number*, not a retransmit
  request. The throttle half still applies.
- **As a substitute for handling the venue's own reject.** This module is a client-side
  predictor. When the venue sends a Business Level Reject or a FIXP `NotApplied`, obey the
  venue — it is authoritative and this module is not.
- **As a queueing/latency model.** If the question is "how much delay does engine
  congestion add", that is `exchange-matching-engine-behavior-under-load`. This skill
  covers the regime *above* the session throttle, where no queueing model applies.
- **To infer a sequence reset.** A low sequence number is a regression, not a reset. Resets
  are signalled by the session layer (FIX `Logon` with `ResetSeqNumFlag(141)=Y`, a new FIXP
  UUID, a new SoupBinTCP session) and must be passed in via `reset_session_sequence`.
- **Across sessions.** Rate limits and sequence numbers are per session. Aggregating a
  firm-wide message log into one audit produces both phantom gaps and phantom breaches.

## Prerequisites

- Outbound message log per session: `session_id`, `message_type`, `timestamp_epoch`
  (epoch **seconds**, fractional part carrying sub-second resolution), `sequence_id`.
- Inbound message log per session **in arrival order**: `session_id`, `message_type`,
  `sequence_id`, `timestamp_epoch`, and `poss_dup` where the venue marks possible
  duplicates (FIX `PossDupFlag(43)=Y`).
- The session's **contracted** message limit and the venue's **counting interval**.
  `max_allowed_mps=500.0` is a placeholder default, not a venue-published figure.
- The venue's cap on the message Count of one retransmit request (2500 on CME iLink 3).

## Units and conventions

Rates are **messages per second**; timestamps and `window_seconds` are **seconds**.
Sequence numbers are positive integers, first message of a session numbered 1. Missing
ranges are **inclusive** at both ends. Threshold comparisons are **non-strict lower
bounds** — a rate landing exactly on the limit takes the more conservative branch.

## Workflow

1. **Split the log by session and by message class before auditing.**
   - **Decision point.** Venue counters are per session *and* per class — CME's
     administrative counter (100 admin MPS over a three-second window → reject; 200 admin
     MPS, or 5 invalid Negotiate/Establish in 60 s → automatic port closure) is separate
     from application messaging. Call the audit once per (session, class) with the matching
     `window_seconds`. Records for another session are excluded and reported in
     `records_excluded_other_session`; a non-zero count there means the caller mixed logs.

2. **Set `window_seconds` to the venue's counting interval, not to 1.0 by habit.**
   - **Decision point.** 480 messages inside 10 ms sit comfortably under a 1-second average
     and blow through a 100 ms counter. A window longer than the venue's dilutes exactly the
     burst you are trying to catch.

3. **Rate the outbound log on its worst window.**
   - The verdict uses `peak_window_rate_per_sec` — the maximum count in *any* window of
     `window_seconds` in the log supplied — not the trailing window. It is therefore a pure
     function of the data: a replayed capture reports the burst it contains, and a clock
     step cannot turn a breach into `MATCHING_ENGINE_NORMAL`.
   - `outbound_rate_per_sec` is the trailing window ending at `as_of_epoch`, reported for
     dashboards only. **Pass only the messages you consider in scope** — the module reports
     the worst window in what it is given.
   - Peak $\ge$ `max_allowed_mps` $\implies$ `EXCHANGE_RATE_LIMIT_THROTTLED`, directive
     `BLOCK_OUTBOUND_ORDER_SUBMISSION`. Peak $\ge$ 80% of it $\implies$
     `THROTTLE_WARNING_SLOW_DOWN`, directive `REDUCE_OUTBOUND_MESSAGE_RATE`.

4. **Walk the inbound log in arrival order — never sorted by sequence number.**
   - **Decision point.** Sorting the batch is look-ahead: it lets a message that arrived
     later fill a hole that was open when the earlier one landed, so a genuine loss reads
     as contiguous and no retransmit request is ever issued. `out_of_order_ahead_count`
     tells you whether a resolved discontinuity was reordering (normal on MoldUDP64 over
     UDP, impossible in-session on TCP-based FIX) or loss.
   - $Seq_i > Seq_{\text{expected}}$: buffer $Seq_i$, record the missing run, and **do not
     advance the expected counter**. The venue still owes those messages and will deliver
     them under their original numbers.
   - $Seq_i = Seq_{\text{expected}}$: advance, then drain any buffered contiguous run.
   - $Seq_i < Seq_{\text{expected}}$: see step 6.

5. **Size the retransmit requests before issuing any.**
   - **Decision point.** CME caps one Retransmit/Resend Request at 2500 messages; a larger
     request is rejected. `retransmit_requests_required` is the ceiling division, and
     `exceeds_single_retransmit_limit` says whether the run needs splitting. Re-issuing one
     oversized request instead is the infinite-resend-loop that CME's AutoCert+ suite exists
     to catch.
   - If `buffered_ahead_overflow` is set, retransmission is not converging. The directive is
     `SESSION_RESYNC_REQUIRED` — stop requesting and resynchronise.

6. **Treat a low sequence number as a session-ending event, not a curiosity.**
   - With `poss_dup` set it is an expected duplicate from a retransmission: discard it and
     count it. Without it, the FIX session layer requires `Logout(35=5)` with
     `SessionStatus(1409)=9` and termination of the transport connection — the only
     exception being `SequenceReset(35=4)` with `GapFillFlag(123)=N`. The module reports
     `SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE` and directive
     `TERMINATE_SESSION_AND_RESYNC`, and does **not** rewind its counter.

7. **Act on every flag, not on `status`.**
   - **Decision point.** `status` is a single most-severe label for logging and is lossy by
     construction. `is_throttled`, `has_sequence_gap` and `has_sequence_regression` are
     independent and can all be true at once. Honour `directives` — branching on `status`
     alone is how a throttle block gets masked by a coincident gap.

8. **Retain the `MatchingEngineAuditReport`.** It is frozen, so it is safe to keep as the
   audit record of why order submission stopped. RTS 6 Article 16(5) requires real-time
   alerts within five seconds of the event, which bounds how long a detection may sit
   unreported.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Anchoring the counting window to `time.time()` instead of to the data.** A burst
  replayed from a capture, or a log delivered a few seconds late, then counts zero messages
  and reports the session healthy — a silent false negative on the one event the module
  exists to catch. Worse, `time.time()` is not monotonic: an NTP step can erase a breach.
- **Averaging the burst away.** Counting over a window longer than the venue's own dilutes
  a sub-second burst below the limit while the venue's counter trips.
- **Advancing the expected sequence past a gap on detection.** It reports the gap once and
  then declares the stream healthy while the missing execution reports never arrive.
  Hold the counter at the gap until the retransmission actually fills it.
- **Sorting the inbound batch by sequence number.** This is look-ahead — a later arrival
  conceals the gap that was open when the earlier one landed, and the retransmit request
  that was owed is never issued.
- **Silently dropping a sequence number below the expected one.** That is the FIX session
  layer's definition of a protocol violation requiring logout; treating it as noise is how
  a stale execution report gets applied to position twice. Note the layer distinction: on a
  *market data* feed a stale multicast packet genuinely is a duplicate to discard (see
  `sequence-number-gap-detection-for-feeds`), but on an *order-entry* session the same
  condition ends the session. Do not carry the feed-side rule across.
- **Inferring a sequence reset from a low sequence number.** A daily reset and a corrupted
  stream look identical from the number alone. Only the session layer knows; take the
  signal from it.
- **Re-requesting a gap larger than the venue's per-request cap.** The request is rejected,
  the gap stays open, and a naive retry loop re-issues the same oversized request forever.
- **Pooling sessions.** One firm-wide log audited as a single session invents both phantom
  gaps (two independent sequence streams interleaved) and phantom breaches (another
  session's traffic counted against yours).
- **Counting one MoldUDP64 packet as one message.** The header numbers the *first* message
  in the packet and `MessageCount` gives the rest; advancing one per packet invents gaps
  on every multi-message packet.
- **Branching on `status` alone.** A throttle block and a sequence gap are independent
  conditions; a single label can only carry one of them.
- **Treating a reconnect loop as free.** CME closes the ports of a session exceeding 5
  invalid Negotiate/Establish messages in 60 seconds, so an aggressive re-establish loop
  after a disconnect converts a recoverable outage into a closed port.

## Verification

- Instantiate `MatchingEngineMonitorEngine(max_allowed_mps=500.0, warning_threshold_pct=80.0)`
  and pass an explicit `as_of_epoch` so results are reproducible.
  - 420 messages in a 1 s window $\implies$ `THROTTLE_WARNING_SLOW_DOWN`,
    `peak_window_rate_per_sec` $= 420.0$, `is_throttled` false.
  - 550 messages $\implies$ `EXCHANGE_RATE_LIMIT_THROTTLED` with directive
    `BLOCK_OUTBOUND_ORDER_SUBMISSION`.
  - Boundary: exactly 500 blocks, 499 warns, exactly 400 warns, 399 is normal.
- Determinism: 900 messages timestamped one hour before `as_of_epoch` still report
  `EXCHANGE_RATE_LIMIT_THROTTLED` with `peak_window_rate_per_sec` $= 900.0$,
  `outbound_rate_per_sec` $= 0.0$ and `newest_outbound_age_sec` $= 3600.0$.
- Window sensitivity: 480 messages spread over 10 ms give $480.0$ at
  `window_seconds=1.0` (no block) and $4800.0$ at `window_seconds=0.1` (block).
- Session isolation: 600 outbound and one inbound record tagged `"OTHER"`, audited as
  `"MINE"`, give a peak rate of $0.0$, no gap, and
  `records_excluded_other_session` $= 601$.
- Gap detection: inbound sequence 105 when expecting 1 $\implies$
  `MESSAGE_SEQUENCE_GAP_DETECTED`, missing range $[1, 104]$, `gap_size` $= 104$. A second
  audit with no new messages still reports the same open gap — the counter did not advance.
- Multiple gaps: sequences 1, 2, 5, 6, 9, 10 report **two** runs, $[3,4]$ and $[7,8]$, with
  `next_expected_seq_id` $= 3$.
- Recovery: after sequence 4 arrives first, delivering 1, 2, 3 closes the gap and drains
  the buffer to `next_expected_seq_id` $= 5$.
- Retransmit sizing: a 2500-message gap needs 1 request; 2501 needs 2; 9000 needs 4
  (ceiling division, not 3).
- Regression: after 1, 2, 3, receiving 2 without `poss_dup` $\implies$
  `SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE` and `TERMINATE_SESSION_AND_RESYNC`, with
  `next_expected_seq_id` unchanged at 4. The same message with `poss_dup=True` is a counted
  duplicate and nothing else.
- Precedence: 700 msgs/sec *and* an open gap report `status`
  `EXCHANGE_RATE_LIMIT_THROTTLED` while `has_sequence_gap` stays true and both directives
  are emitted.
- Negative checks — each must raise `ValueError`: `max_allowed_mps` $\le 0$ or non-finite;
  `warning_threshold_pct` outside $(0, 100]$; `window_seconds` $\le 0$ or non-finite; a
  non-finite `as_of_epoch` or record timestamp; an empty `session_id` or `message_type`; an
  inbound `sequence_id` $< 1$. A non-int sequence number or count raises `TypeError`,
  which is what catches an accidentally swapped sequence/timestamp pair.
- Run `python -m unittest discover -s scripts` from the skill directory, or
  `python test_matching_engine_throttle_and_message_gapping_detection.py` from `scripts/`,
  and confirm a 100% pass rate.

## Related Skills

- `exchange-matching-engine-behavior-under-load`
- `exchange-gateway-redundancy-and-failover-testing`
- `message-rate-limit-vs-latency-tradeoff-tuning`
- `broker-side-order-throttle-detection`
- `sequence-number-gap-detection-for-feeds`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `cme-group-fix-api-for-futures`
- `fix-protocol-session-management-across-venues`
- `websocket-reconnection-with-state-recovery`

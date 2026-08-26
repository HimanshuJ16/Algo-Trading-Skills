# Workflows for Matching Engine Throttle and Message Gapping Detection

Full procedure behind the summary in `SKILL.md`. Venue behaviour and citations are in
`references/standards.md`; sign-off gates are in `assets/checklist.md`.

---

## 0. Establish the session's parameters before any code runs

The module's defaults are placeholders, not venue figures. Three numbers must come from
the venue or the contract:

| Number | Where it comes from | Failure if guessed |
|---|---|---|
| Message limit | The session's contracted limit, per message class | Too high: the venue rejects and then terminates before the detector fires. Too low: order flow is blocked for no reason. |
| Counting interval | The venue's own interval (CME's administrative counter uses three seconds) | A window longer than the venue's dilutes the burst below the limit while the venue's counter trips. |
| Retransmit request cap | The venue's per-request `Count` maximum (2500 on CME iLink 3) | An oversized request is rejected, the gap stays open, and a naive retry loop re-issues it forever. |

Record all three as configuration with their source, so an audit can trace why a number
was chosen. Where a figure could not be obtained, say so explicitly rather than adopting
the default silently.

---

## 1. Partition the message log

**Sessions.** Rate limits and sequence numbers are both defined at session scope. Group the
log by `session_id` and call the audit once per session. The module excludes foreign
records and reports the count in `records_excluded_other_session` — treat any non-zero
value there as a caller bug, not as a handled case.

**Message classes.** Where the venue maintains separate counters (CME's administrative
counter is independent of application messaging), split the outbound log per class and
call the audit once per class with that class's own limit and interval. Auditing a mixed
log against one limit both under-counts the class that is actually breaching and
over-counts the one that is not.

**Arrival order.** The inbound list must be in the order messages were *received*, not
sorted. This is the single most important input contract in this skill — see §3.

---

## 2. Outbound rate audit

### 2.1 Peak window, not trailing window

The verdict is taken from `peak_window_rate_per_sec`: the largest number of messages
falling in any window of `window_seconds` anywhere in the supplied log, divided by the
window. Computed by a two-pointer sweep over the sorted timestamps, O(n) after the sort.

Only windows ending on an actual message need testing — sliding a window further right can
only drop messages, never add them.

Two consequences:

1. **The verdict is a pure function of the data.** Replay a captured burst an hour later
   and it still reports the burst. A wall-clock-anchored counter reports zero and calls the
   session healthy — a silent false negative on exactly the event being monitored, and one
   an NTP step can produce in live operation too.
2. **The caller controls scope.** The module reports the worst window in what it is given,
   so pass a bounded recent buffer in live monitoring. Passing an entire session's log will
   latch onto the day's worst burst, which is right for post-trade review and wrong for a
   live gate.

`outbound_rate_per_sec` is the trailing window ending at `as_of_epoch` and is reported for
dashboards only. `newest_outbound_age_sec` exposes staleness; a value larger than
`window_seconds` means the trailing figure is describing an empty window.

### 2.2 Bands

| Condition on the peak rate | Status | Directive |
|---|---|---|
| $< 80\%$ of `max_allowed_mps` | `MATCHING_ENGINE_NORMAL` | — |
| $\ge 80\%$ and $<$ limit | `THROTTLE_WARNING_SLOW_DOWN` | `REDUCE_OUTBOUND_MESSAGE_RATE` |
| $\ge$ `max_allowed_mps` | `EXCHANGE_RATE_LIMIT_THROTTLED` | `BLOCK_OUTBOUND_ORDER_SUBMISSION` |

Comparisons are non-strict lower bounds on the exact rate. A rate landing on a threshold
takes the more conservative branch, deliberately: the cost of one over-cautious slowdown is
far below the cost of a terminated session mid-position.

### 2.3 Data hygiene signals

- `future_dated_outbound_count` — messages timestamped after `as_of_epoch`. They still
  count toward the peak (their spacing is what matters), but a non-zero count means the
  clock source is wrong and every latency figure derived from the same log is suspect.
- Non-finite timestamps are rejected at record construction. This is deliberate: a dropped
  NaN reports a 900-message burst as 0 msgs/sec and a healthy session.

---

## 3. Inbound sequence audit

### 3.1 Arrival order, and why sorting is look-ahead

Processing the batch sorted by sequence number lets a message that arrived *later* fill a
hole that was open when an earlier one landed. Sequences arriving 105, 101, 102, 103, 104
then read as contiguous 101–105 and no gap is reported — but at the instant 105 arrived the
receiver had a gap and owed a retransmit request. The module therefore walks arrival order
and exposes `out_of_order_ahead_count` so a resolved discontinuity is distinguishable from
a loss:

| Transport | Out-of-order arrival | Interpretation |
|---|---|---|
| MoldUDP64 / UDP multicast | Expected | Reordering; the gap closed on its own. |
| FIX / FIXP over TCP | Impossible in-session | Either the batch was assembled out of order by the caller, or the session was re-established underneath. Investigate. |

### 3.2 The state machine

Per session the module holds `expected` (next sequence number wanted) and `buffered` (the
numbers received ahead of it). For each inbound message:

| Case | Action |
|---|---|
| $Seq = expected$ | Advance `expected`, then drain any contiguous run already in `buffered`. |
| $Seq > expected$ | Add to `buffered`, log the gap, and **do not advance `expected`**. If $Seq$ was already buffered, count it as a duplicate. |
| $Seq < expected$, `poss_dup` set | Expected duplicate from a retransmission. Count and discard. |
| $Seq < expected$, no `poss_dup` | Sequence regression — see §5. |

**Holding `expected` at the gap is the point.** Advancing past it on detection reports the
gap once and then declares the stream healthy while the missing execution reports never
arrive. Because retransmission delivers them under their *original* numbers, holding is
also what lets the gap close naturally when they land.

At the end of the batch the still-missing runs are derived from `expected` and `buffered`
by walking the buffered numbers in order — so a gap of ten million costs one step, not ten
million. Each run is reported as an inclusive `[missing_seq_start, missing_seq_end]`, which
is exactly the range to put in a request.

### 3.3 MoldUDP64 normalisation

A MoldUDP64 packet header carries the sequence number of the **first** message in the
packet; the rest follow at sn+1, sn+2. Expand each packet into `MessageCount` records
before handing them to this module. Advancing one number per packet invents a gap on every
multi-message packet — a false alarm on essentially every packet in a busy feed. Treat
`MessageCount = 0` (heartbeat) and `0xFFFF` (End of Session) as session-layer events, not
as data.

---

## 4. Retransmit request sizing

Before issuing anything, size the request against the venue's cap:

- `gap_size` — messages missing in the run.
- `retransmit_requests_required` — ceiling division of `gap_size` by the cap. A 9000-message
  gap at a 2500 cap needs **4** requests, not 3.
- `exceeds_single_retransmit_limit` — whether the run must be split at all.

Issue the requests **sequentially**, confirming each range lands before requesting the next.
Re-issuing one oversized request in a loop is the infinite-resend-loop failure CME's
AutoCert+ suite exists to catch: the venue rejects the oversized request, the gap stays
open, and the naive retry re-sends it forever — while the retry traffic itself counts
against the session's message limit and can escalate into a disconnect.

Escalation: if `buffered_ahead_overflow` is set, retransmission is not converging and the
directive is `SESSION_RESYNC_REQUIRED`. Stop requesting; resynchronise at the session layer
and reconcile open orders before quoting again.

---

## 5. Sequence regression and session reset

A sequence number below the expected one, arriving without a possible-duplicate marker, is
a protocol violation. FIX requires `Logout(35=5)` with `SessionStatus(1409)=9` and
termination of the transport connection, the only exception being `SequenceReset(35=4)`
with `GapFillFlag(123)=N`. The module reports
`SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE`, emits `TERMINATE_SESSION_AND_RESYNC`, and
leaves its counter untouched.

**Do not infer a reset from it.** A daily sequence reset and a corrupted stream produce the
same low number. Only the session layer knows which it is — FIX `Logon(35=A)` with
`ResetSeqNumFlag(141)=Y`, a new FIXP UUID, a new SoupBinTCP session id. On that signal, and
only then, call `reset_session_sequence(session_id, next_expected_seq_id)`. It re-anchors
the counter and discards any open gap, because a gap in the previous sequence stream can no
longer be retransmit-requested.

After any reset or reconnect, **reconcile open orders and positions against the venue before
resuming submission**. The sequence counter being healthy says nothing about whether the
orders you think are resting still are.

---

## 6. Acting on the report

`status` is one most-severe label for logging and is lossy. The flags below it are
independent and can all be true at once:

- `is_throttled` → stop submitting.
- `has_sequence_gap` → issue sized retransmit requests.
- `has_sequence_regression` → log out, terminate, resynchronise.
- `buffered_ahead_overflow` → resynchronise rather than request more.

`directives` carries every required action as a tuple; wire that into the order gate rather
than branching on `status`. A throttle block coinciding with a gap is the concrete case
where a single label loses the action that matters.

Timing: RTS 6 Article 16(5) requires real-time alerts within five seconds of the relevant
event, so an audit sweep interval longer than five seconds cannot meet it in the EU/UK.

---

## 7. Retention

`MatchingEngineAuditReport` and its detail records are frozen, so they are safe to retain
verbatim as the audit trail for why order submission stopped, which gap was requested, and
why a session was torn down. Keep the configured limit, window and cap alongside the
report — a rate figure is uninterpretable without the window it was counted over.

# Pre-Flight Checklist — Matching Engine Throttle and Message Gapping Detection

## Configuration

- [ ] Is `max_allowed_mps` taken from the session's **contracted** limit, with its source
      recorded — rather than left at the 500.0 placeholder default?
- [ ] Does `window_seconds` match the **venue's own counting interval**, rather than
      defaulting to 1.0? (CME's administrative counter uses a three-second window.)
- [ ] Is `max_retransmit_request_count` the venue's documented per-request cap (2500 on
      CME iLink 3), confirmed rather than assumed?
- [ ] Is `warning_threshold_pct` inside (0, 100], so the warning band is reachable before
      the hard block?
- [ ] Is every parameter that could not be sourced from the venue flagged as an assumption
      rather than adopted silently?

## Input contract

- [ ] Is the log **split by session** before auditing, and is
      `records_excluded_other_session` asserted to be zero rather than merely reported?
- [ ] Is the log **split by message class** where the venue counts classes separately, with
      one audit per class against that class's limit?
- [ ] Is the inbound list passed in **arrival order**, never sorted by sequence number?
- [ ] Are MoldUDP64 packets expanded into `MessageCount` per-message records starting at
      the header's sequence number, rather than counted one per packet?
- [ ] Are timestamps epoch **seconds** with sub-second resolution in the fractional part —
      not milliseconds, and not nanoseconds?
- [ ] Is `poss_dup` populated from the venue's duplicate marker (FIX `PossDupFlag(43)=Y`),
      so an expected retransmission is not misread as a protocol violation?

## Outbound throttle

- [ ] Is the verdict read from `peak_window_rate_per_sec` (the worst window in the log) and
      not from `outbound_rate_per_sec` (the trailing window, for display)?
- [ ] Is the buffer passed in live monitoring **bounded and recent**, so the peak does not
      latch onto a burst from hours ago?
- [ ] Is `as_of_epoch` passed explicitly wherever the result must be reproducible — replays,
      backtests, post-trade review?
- [ ] Is `newest_outbound_age_sec` checked, so a stale or stalled feed is not read as a
      quiet session?
- [ ] Is `future_dated_outbound_count` zero — and if not, has the clock source been
      investigated before any latency figure from the same log is trusted?
- [ ] Does `BLOCK_OUTBOUND_ORDER_SUBMISSION` actually gate the order path, rather than only
      raising an alert?
- [ ] Is there hysteresis (dwell time or a lower re-entry threshold) so a rate oscillating
      around the threshold cannot flap the gate?

## Inbound sequence

- [ ] Is it understood that the expected counter **holds at the gap** and does not advance
      until the retransmission actually fills it?
- [ ] Are **all** open runs in `sequence_gaps` acted on, not just `sequence_gap_details`
      (which is the first run only)?
- [ ] Is `retransmit_requests_required` used to split a gap above the venue cap into
      sequential requests, with each range confirmed before the next is sent?
- [ ] Is retry bounded, so an oversized or failing request cannot loop — and is it
      understood that the retry traffic itself counts against the message limit?
- [ ] Does `buffered_ahead_overflow` escalate to a session resync rather than to more
      retransmit requests?
- [ ] Is `out_of_order_ahead_count` monitored, and treated as an anomaly on a TCP-based
      session where in-session reordering is impossible?

## Sequence regression and reset

- [ ] Does a `SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE` verdict trigger a real logout and
      transport termination, not just a log line?
- [ ] Is it understood that the module **never** infers a reset from a low sequence number,
      and that `reset_session_sequence` must be called on the session-layer signal
      (`ResetSeqNumFlag=Y`, new FIXP UUID, new SoupBinTCP session)?
- [ ] After any reset or reconnect, are **open orders and positions reconciled against the
      venue** before submission resumes?
- [ ] Is the re-establish loop rate-limited, given that CME closes the ports of a session
      exceeding 5 invalid Negotiate/Establish messages in 60 seconds?

## Acting on the report

- [ ] Are `is_throttled`, `has_sequence_gap` and `has_sequence_regression` all honoured
      **independently**, rather than branching on `status` alone?
- [ ] Is `directives` wired into the order gate and the recovery path, rather than
      re-derived by the caller?
- [ ] Does the venue's own signal (Business Level Reject, FIXP `NotApplied`, a session-level
      reject) take precedence over this module's client-side prediction?

## Compliance and audit (EU / UK)

- [ ] Does the outbound counter see the **whole** order stream, as RTS 6 Article 15(1)(d)
      and Article 15's immediate-inclusion requirement demand — not a sample?
- [ ] Is the audit sweep interval inside the **five seconds** RTS 6 Article 16(5) allows
      between the event and the real-time alert?
- [ ] Are `MatchingEngineAuditReport` records retained as the audit trail for why
      submission stopped, which range was requested, and why a session was torn down?
- [ ] Is the configured limit, window and retransmit cap stored **alongside** each report,
      given that a rate figure is uninterpretable without the window it was counted over?

# Pre-Flight / Sign-off Checklist — exchange-multicast-feed-handling

Use this before considering the skill's implementation complete.

## Socket layer (caller-owned)

- [ ] **Dual line join:** IGMP membership taken on both the A and B multicast groups
      (`IP_ADD_MEMBERSHIP`, or source-specific where the venue publishes a source).
- [ ] **Line attribution:** every datagram reaches the handler tagged with the line it
      arrived on.
- [ ] **One instance per channel:** no handler is shared across channels or SenderCompIDs.

## Arbitration and sequencing

- [ ] **Arbitration window measured, not guessed:** `arbitration_window_s` comes from the
      venue (Eurex `MDRecoveryTimeInterval`, tag 2565) or from measured A-versus-B skew on
      your own cross-connects, and is recorded with the measurement date.
- [ ] **Duplicate of a processed packet** is discarded.
- [ ] **Duplicate of a *buffered* packet** is discarded without re-buffering or opening a
      second gap.
- [ ] **Out-of-order packets are buffered**, not applied, and not treated as loss.
- [ ] **No recovery request fires inside the window**, and a gap filled by the other line
      within the window produces none at all.
- [ ] **One gap yields one request:** later out-of-order packets extend the range without
      restarting the timer or emitting a second request.
- [ ] **Monotonic clock** used for the window; `time.time()` is not.

## Recovery escalation

- [ ] **Timer-driven polling:** `poll_recovery()` runs on a timer, so a gap still escalates
      when the feed goes silent after the loss.
- [ ] **Correct transport per venue:** CME TCP replay, MoldUDP64 UDP-unicast Re-request
      Server, Eurex T7 none — snapshot only.
- [ ] **Venue limits respected:** request sized under CME's 2000-packet / 24-hour /
      one-request-per-session caps, or under one datagram for MoldUDP64; oversized ranges
      routed to snapshot recovery instead.
- [ ] **Recovered data applied before queued data.**
- [ ] **Partial responses do not close the gap:** the remainder stays outstanding and
      re-armed.

## Failure containment

- [ ] **Buffer is capped** and overflow latches `requires_resynchronization`.
- [ ] **Quoting is gated** on `requires_resynchronization` being clear.
- [ ] **Sequence restart is surfaced, not swallowed:** a large backward jump reports
      `RESET_SUSPECTED`, and `reset_sequence()` is called only after the venue's in-band
      restart signal is confirmed (CME Channel Reset 35=X/269=J, weekly reset, or a new
      MoldUDP64 Session).
- [ ] **MoldUDP64 message counts honoured:** the expected sequence advances by
      `message_count`, not by one.
- [ ] **Automated Testing:** run `python scripts/test_multicast_handler.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

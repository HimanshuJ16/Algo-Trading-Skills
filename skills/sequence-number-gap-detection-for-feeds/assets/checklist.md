# Pre-Flight / Sign-off Checklist — sequence-number-gap-detection-for-feeds

Use this before a gap detector goes in front of live capital.

## Sequence space

- [ ] **One `stream` key per sequence space, not per instrument.** On MoldUDP64 and CME
      MDP 3.0 that is the session or channel; a per-symbol key on those feeds makes every
      message a gap.
- [ ] **Stream keys normalized at the boundary.** The engine compares them exactly.
- [ ] **Sequencing model matched to the feed.** Range-sequenced streams (Binance `U`/`u`)
      pass `last_sequence_id`; point-sequenced streams do not.
- [ ] **Baseline seeded where the start is known** (`resynchronize` with a snapshot's
      `lastUpdateId + 1` or a stored session position), and the consequence of an adopted
      baseline understood and accepted where it is not.

## Detection

- [ ] **Every frame is ingested**, including ones arriving out of order, and no frame is
      applied to downstream state except from `processed_frames`.
- [ ] **Recovery requests use `missing_ranges`**, not the full span — already-buffered
      sequences are not re-requested.
- [ ] **Heartbeats are fed in** where the publisher sends them (MoldUDP64 Message Count 0),
      so loss at the tail of a stream is visible.
- [ ] **End of Session is treated as a deadline.** Any outstanding range when MoldUDP64
      `0xFFFF` packets stop is a data-integrity incident, not a recoverable gap.
- [ ] **Liveness is measured separately.** A feed that stops entirely produces no gap.

## Authorization gate

- [ ] **`is_trading_authorized(stream)` gates the strategy**, and the gate is checked
      before acting, not only when a gap is first reported.
- [ ] **Unknown streams are treated as unauthorized** (the engine already does; confirm no
      caller-side default overrides it).
- [ ] **`RECOVERING` is not treated as recovered.** It means a backfill round happened and
      the gap is still open.

## Recovery

- [ ] **`is_synced` is checked after every backfill round.** Partial responses are the
      normal case: MoldUDP64 returns only what fits one UDP packet, CME caps a replay
      request.
- [ ] **The recovery loop is bounded**, and escalates to a snapshot rather than retrying
      indefinitely against a venue's capped recovery service.
- [ ] **Snapshot resynchronization is implemented**, not just retransmission — it is the
      only recovery path on venues without a replay service, and the only exit from
      `RESET_REQUIRED`.
- [ ] **Book state is rebuilt from the snapshot before `resynchronize` is called**;
      resetting the sequence without reloading the book leaves a silently wrong book that
      reports `SYNCED`.

## Failure containment

- [ ] **`max_buffer_size` sized from measured message rate × longest survivable recovery**,
      and recorded with the measurement date.
- [ ] **Buffer depth is alerted on** before it reaches the bound, not only when
      `RESET_REQUIRED` latches.
- [ ] **`RESET_SUSPECTED` is surfaced to an operator**, and `resynchronize` is called only
      after the venue's in-band restart signal is confirmed (CME Channel Reset or weekly
      reset, a new MoldUDP64 Session).
- [ ] **No caller catches `FeedResetRequiredError` and continues.** It means the local book
      cannot be repaired by backfill.
- [ ] **One stream is ingested from one thread**, or the caller serializes it — the engine
      is not thread-safe.

## Monitoring

- [ ] `outstanding_missing_count`, `state`, `gaps_detected`, `buffered_frames` and
      `frames_dropped_buffer_full` exported per stream.
- [ ] Gap warnings and buffer-overflow errors reach an on-call path, not just a log file.

## Testing

- [ ] **Automated Testing:** run
      `python -m unittest discover -s skills/sequence-number-gap-detection-for-feeds/scripts`
      — 62/62 pass.
- [ ] A restart scenario (sequence jumping backwards by more than the threshold) has been
      exercised against the real consumer, not only the unit tests.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

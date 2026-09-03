# Pre-Flight / Sign-off Checklist — broker-side-order-throttle-detection

Use this before considering the skill's implementation complete.

## Scope

- [ ] **Venue actually throttles silently:** Confirm the venue/transport queues or paces
      excess messages rather than returning 429/418/FIX reject. If it signals explicitly,
      that signal is authoritative — use `multi-broker-rate-limit-handling` instead.
- [ ] **`Retry-After` is never overridden:** Confirm no code path lets a latency-derived
      backoff shorten an explicit venue-instructed wait.
- [ ] **Pre-trade message limit exists separately:** Confirm a hard maximum-message-limit
      control is in place (MiFID II RTS 6 Art. 15(1)(d) where applicable). This skill is
      advisory and does not satisfy it.

## Measurement

- [ ] **Monotonic timestamps:** Both ends timestamped with `time.monotonic()` from one
      process. No wall-clock, no cross-host subtraction.
- [ ] **Bad input rejected, not absorbed:** NaN/Inf timestamps and `t_ack < t_sub` raise
      `ThrottleDataError`. Confirm nothing clamps a negative or NaN RTT to 0 ms — under
      `max(0.0, x)` both become a fabricated "perfect" sample that drags the baseline down.
- [ ] **Submissions registered:** `register_order_submission` is called at dispatch for
      every order, not only for those later acknowledged.

## Baseline

- [ ] **Throttled samples excluded:** Confirm a sample classified `SILENT_THROTTLE` or
      `ACK_TIMEOUT` is *not* folded into the EWMA/EWMVar. Test with a sustained
      sub-ceiling throttle and confirm the state does not lapse to `NORMAL`.
- [ ] **Warmup honoured:** Confirm `WARMUP` is reported (and not treated as healthy)
      until `min_samples_for_detection` admitted samples exist, while the absolute
      ceiling and ACK timeout stay live throughout.
- [ ] **Variance floor units:** Confirm sigma = `sqrt(max(EWMVar, min_variance_clamp))`,
      with the clamp expressed in ms², applied before the square root.
- [ ] **Report is self-consistent:** Confirm
      `z_score == (latest_rtt_ms - ewma_rtt_ms) / ewmsd_rtt_ms` for every report, so the
      decision is auditable after the fact.

## Detection & response

- [ ] **Thresholds calibrated, not inherited:** `max_absolute_rtt_ms` and `ack_timeout_ms`
      set from your own measured ACK RTT distribution. The 500 ms / 5000 ms defaults are
      placeholders, not standards.
- [ ] **Missing ACKs detected:** `sweep_pending_acks` runs on a timer at an interval no
      longer than `ack_timeout_ms` (and inside five seconds where RTS 6 Art. 16(5)
      applies). Confirm one report per stalled order, not one per sweep.
- [ ] **AIMD behaviour:** Confirm multiplicative delay increase on a congestion signal,
      additive decay to exactly zero when healthy, and clamping at `max_backoff_ms`.
- [ ] **Backoff never delays risk actions:** Confirm cancels and kill-switch actions
      bypass the recommended delay entirely.
- [ ] **Recovery policy chosen deliberately:** `rebaseline_after_consecutive` left at 0
      (keep alarming) or set with a documented reason, and its WARNING log is alerted on.

## Operations

- [ ] **Thread safety:** Confirm the detector is shared safely across broker callback
      threads, or that one instance is used per thread with no shared state.
- [ ] **Alerting wired:** `SILENT_THROTTLE` and `ACK_TIMEOUT` reach a human, not just a
      log file.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/broker-side-order-throttle-detection/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

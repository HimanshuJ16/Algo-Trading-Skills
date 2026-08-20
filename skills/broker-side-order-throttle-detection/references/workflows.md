# Deep Workflow Reference — broker-side-order-throttle-detection

This file holds the full technical procedure referenced by `SKILL.md`.

## Scope check before anything else

Determine whether the venue signals congestion explicitly. If it returns HTTP 429/418,
a FIX Business Message Reject, or any documented throttle response, that response is
authoritative — handle it with `multi-broker-rate-limit-handling` and honour any
`Retry-After`. This procedure applies only where excess messages are queued or paced
with no signal returned to the client. See `references/standards.md` for the per-venue
table.

## Full Procedure

1. **Submission registration and ACK timestamping**

   - At dispatch, call `register_order_submission(order_id, t_sub)`.
   - Record $t_{\text{sub}}$ and $t_{\text{ack}}$ from the **same monotonic clock**
     (`time.monotonic()`), in one process. Wall-clock timestamps can step backwards
     under NTP correction.
   - Compute $\text{RTT} = (t_{\text{ack}} - t_{\text{sub}}) \times 1000$ ms.
   - Reject non-finite and time-reversed inputs (`ThrottleDataError`). Do **not** clamp
     to zero: `max(0.0, t_ack - t_sub)` fabricates a 0 ms sample from a backwards clock,
     and because `max(0.0, nan)` returns `0.0` it does the same for a NaN timestamp.
     Either way the baseline is pulled down with nothing logged.

2. **EWMA / EWMVar baseline**

   - Use the exponentially weighted recurrence of Finch (2009), eq. 143:

     ```
     diff     := x - mean
     incr     := alpha * diff
     mean     := mean + incr
     variance := (1 - alpha) * (variance + diff * incr)
     ```

     This is the exponentially weighted estimator, not Welford's algorithm; Welford
     (Knuth, TAOCP vol. 2 §4.2.2) is the equal-weight version and has no forgetting factor.
   - Compute the deviation with the floor applied **before** the square root:
     $\sigma = \sqrt{\max(\text{EWMVar}, \text{min\_variance\_clamp})}$. The clamp is a
     variance in ms², so a clamp of 1.0 floors sigma at 1 ms and a clamp of 100.0 floors
     it at 10 ms. Applying `max()` to the deviation instead silently changes the units.
   - **Admit only non-throttled samples.** This is the single most important rule here:
     folding a throttled sample into the baseline lets the anomaly train the detector
     onto itself. Against a 15 ms baseline, a sustained 300 ms throttle reads `NORMAL`
     from roughly the fourth sample onward and the backoff decays to zero while every
     order is still being queued.
   - Track the count of admitted samples. Until it reaches `min_samples_for_detection`,
     report `WARMUP` rather than `NORMAL` — a z-score against a one- or two-sample
     baseline is noise, and a first sample taken inside a throttle poisons the session.

3. **Classification (all thresholds against the pre-update baseline)**

   Evaluate in this order, using the mean and sigma *as they stood before this sample*:

   | Condition | State |
   |---|---|
   | order overdue in the pending sweep | `ACK_TIMEOUT` |
   | $\text{RTT} \ge \text{max\_absolute\_rtt\_ms}$ | `SILENT_THROTTLE` |
   | warm **and** $z \ge \text{z\_score\_threshold}$ | `SILENT_THROTTLE` |
   | not warm | `WARMUP` |
   | $z \ge \text{elevated\_z\_threshold}$ | `ELEVATED_LATENCY` |
   | otherwise | `NORMAL` |

   where $z = (\text{RTT} - \text{EWMA}) / \sigma$.

   Using one baseline for one threshold and a differently-timed baseline for another
   makes the reported statistics irreconcilable with the decision and shifts the
   effective elevated band to $1/(1-\alpha)$ sigma. Report the same mean and sigma that
   the decision used, so `z_score == (latest_rtt_ms - ewma_rtt_ms) / ewmsd_rtt_ms` holds.

4. **Pending-ACK sweep**

   - Call `sweep_pending_acks(now)` on a timer or from the dispatch loop, at an interval
     no longer than `ack_timeout_ms` — and short enough to satisfy the five-second
     alerting bound of RTS 6 Article 16(5) where that applies.
   - Any registered order older than `ack_timeout_ms` is reported once as `ACK_TIMEOUT`
     and removed from the pending table, so repeated sweeps do not re-escalate the
     backoff for the same stalled order.
   - A late acknowledgment arriving afterwards is still classified normally and will
     read as a large RTT.
   - Without this step the detector observes only orders that *were* acknowledged. That
     population is, by construction, the one that was not throttled into silence.

5. **AIMD adaptive backoff**

   Chiu & Jain (1989). The controlled variable is the dispatch *rate*; the delay moves
   inversely.

   - `SILENT_THROTTLE` / `ACK_TIMEOUT` → multiplicative decrease of rate: seed the delay
     at $\max(\text{min\_backoff\_ms}, \text{RTT} \times 0.5)$ if currently zero,
     otherwise multiply by `backoff_multiplier`. Clamp to `max_backoff_ms`.
   - `ELEVATED_LATENCY` → add `elevated_increment_ms`, floored at `min_backoff_ms`.
   - `NORMAL` / `WARMUP` → additive increase of rate: subtract
     `backoff_additive_decrease_ms`, floored at **exactly 0**. `min_backoff_ms` bounds an
     active penalty; it is not a resting delay.
   - The delay applies to new order flow only. Never delay a risk-critical cancel or a
     kill-switch action behind it.

6. **Recovery policy**

   - Default (`rebaseline_after_consecutive = 0`): the baseline stays frozen for as long
     as throttling persists. A sustained throttle keeps alarming with the backoff pinned
     at its ceiling until latency returns to baseline or a human intervenes. This is the
     safe direction for a risk control.
   - Genuine permanent shifts (a re-route, a venue migration, a colocation move) will
     also keep alarming under that default. Set `rebaseline_after_consecutive` to N to
     re-anchor onto the new level after N consecutive throttled samples, accepting that
     the detector then goes quiet at that level. Every re-anchor is logged at WARNING —
     treat one as an event to review, not as automatic recovery.
   - At a session boundary, call `reset()` rather than carrying a baseline across a gap
     in which network conditions may have changed entirely.

7. **Concurrency**

   Broker SDKs deliver acknowledgments on their own callback threads. All detector state
   is mutated under a single re-entrant lock; an unlocked read-modify-write of the
   backoff loses updates and silently discards congestion responses.

## Production Implementation Reference

- Reference code: `scripts/throttle_detector.py`
  (`OrderThrottleDetector`, `ThrottleState`, `ThrottleStatusReport`, `ThrottleDataError`).
- Automated unit tests: `scripts/test_throttle_detector.py`.

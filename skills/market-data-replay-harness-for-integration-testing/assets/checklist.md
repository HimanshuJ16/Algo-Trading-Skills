# Pre-Flight Checklist — Market Data Replay Harness

Use this before trusting a replay result, and before citing one as testing evidence.

## The capture
- [ ] Timestamps converted to **seconds** at load (ns/ms captures divided), and
      `max_projected_wall_time_sec` set so a unit mistake raises instead of sleeping.
- [ ] Non-finite timestamps rejected, not sorted around.
- [ ] Multi-venue captures merged into one monotonic sequence space before replay.
- [ ] `out_of_order_input_pairs` reviewed; `strict_ordering=True` on any capture that
      is supposed to be clean.
- [ ] Read from a copy, not from the production capture path (RTS 6 Art. 7(1)).
- [ ] Capture identity recorded with the result: file hash, session date, venue.

## The replay
- [ ] Speed mode chosen from the question being asked — ASAP for logic and regression,
      timed only when elapsed behaviour is under test.
- [ ] Dispatch scheduled against absolute deadlines; no per-tick `sleep(gap / S)`.
- [ ] `retain_replayed_ticks=False` for sessions above a few hundred thousand ticks.
- [ ] Strategy reads time from the tick or `simulated_now()`, never `time.time()`.
- [ ] Callback returns `dict` or `None` only.

## The result
- [ ] `ticks_dispatched_late == 0` before any latency, timer or throttle assertion is
      believed — otherwise the strategy never saw the recorded spacing.
- [ ] `max_scheduling_lag_sec` recorded next to the verdict, not discarded.
- [ ] `achieved_speed_multiplier` compared against the requested multiplier.
- [ ] Counts read from the returned summary (per session), not from accumulated state.
- [ ] Baseline diffs taken on emitted orders and risk events, never on timings.
- [ ] Any `ReplayCallbackError` investigated by the tick it names, not retried blindly.

## Claims this run does NOT support
- [ ] No claim of market impact, queue position, fill probability or contribution to
      disorderly trading — the recorded book does not react to your orders
      (FCA, Feb 2018, §6.12).
- [ ] No claim of venue conformance — RTS 6 Art. 6 requires the venue's or DEA
      provider's own system.
- [ ] No claim of RTS 6 Art. 10 stress testing — that is sized by six-month message and
      trade **volume** doubled, not by replay rate.
- [ ] No claim of sub-millisecond arrival fidelity from a `time.sleep()`-based harness.

## Automated testing
- [ ] `python -m unittest discover -s skills/market-data-replay-harness-for-integration-testing/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

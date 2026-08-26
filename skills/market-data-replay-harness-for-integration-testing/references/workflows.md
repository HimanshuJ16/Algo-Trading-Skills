# Deep Workflow Reference — Market Data Replay Harness for Integration Testing

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Decide what the replay is for

The mode follows the question, not the other way round:

| Question | Mode | What you assert on |
|---|---|---|
| Does every tick reach the strategy, in the same order, every run? | ASAP | Callback sequence, emitted orders |
| Did this build change what the strategy emits? | ASAP | Diff of emitted orders against the stored baseline |
| Do timers, throttles, staleness checks and rate limiters behave? | Timed, $S = 1$ | Behaviour **plus** `ticks_dispatched_late == 0` |
| Can the pipeline keep up at $N\times$? | Timed, $S = N$ | `ticks_dispatched_late`, `max_scheduling_lag_sec`, `achieved_speed_multiplier` |

A timed run whose consumer fell behind is a valid logic test and an invalid timing test. Decide which one you are running before you write the assertion.

## 1. Load the recorded session

Ingest events carrying symbol, timestamp, sequence id, bid, ask, volume into `ReplayTick`.

- **Timestamps must be in seconds.** Convert at load: `ts_sec = ts_ns / 1e9`. Set `max_projected_wall_time_sec` to a value your test could plausibly take (e.g. 300) so a unit mistake raises immediately instead of sleeping for years.
- Non-finite timestamps are rejected; a NaN from a bad parse would otherwise scatter the sort order in silence.
- Multi-venue captures: normalise into a single monotonic sequence space *before* replay (`multi-exchange-feed-normalization`). The harness's tie-break is deterministic, not venue-aware.
- Read from a copy of the capture, not from the production capture path — RTS 6 Art. 7(1) requires separation from the production environment, whose definition includes market data and data capture.

## 2. Fix the ordering policy

The harness orders by `(timestamp, sequence_id)` and reports `out_of_order_input_pairs` — the number of adjacent input pairs that go backwards.

- On a capture that should already be ordered, run `strict_ordering=True`: a `ReplayOrderingError` is the correct outcome, because the interesting fact is that the recorder or the merge is broken.
- On a knowingly interleaved capture, leave the default (sort, warn) and treat a non-zero count as a data-quality metric to track, not noise to ignore.
- The sequence tie-break is what makes ties reproducible. Without it, two ticks sharing a timestamp replay in file order, and the same data read differently gives a different run.

## 3. Configure the harness

```python
harness = MarketDataReplayHarness(
    speed_multiplier=10.0,           # 1.0 real-time, N fast-forward, inf == ASAP
    strict_ordering=True,            # raise on an out-of-order capture
    retain_replayed_ticks=False,     # do not pin millions of ticks in memory
    min_sleep_sec=0.0005,            # below this the OS cannot deliver; dispatch now
    late_tolerance_sec=0.001,        # lag above this counts as late
    max_projected_wall_time_sec=300, # unit-mismatch guard
)
```

`clock` and `sleeper` are injectable for deterministic testing of the scheduler itself; production callers leave them at `time.perf_counter` / `time.sleep`.

## 4. Replay against absolute deadlines

For tick $i$ the dispatch deadline is

$$t^{\text{deadline}}_i = t_{\text{wall},0} + \frac{t_i - t_0}{S}$$

anchored to the session start — **not** a sleep of $(t_i - t_{i-1})/S$ after each callback. The difference is the whole point:

| Scheme | 10 ticks, 10 ms spacing, 5 ms callback, $S=1$ |
|---|---|
| Per-tick sleep of the gap | 150 ms — every callback and every scheduler overshoot accumulates |
| Absolute deadlines (this harness) | ~95 ms — 90 ms recorded plus the final callback |

Per tick: compute the remaining time to the deadline; sleep it only if it is positive and at least `min_sleep_sec`; then measure lag as `clock() - deadline` **after** any sleep. Positive lag feeds `max_scheduling_lag_sec`, the mean, and the late count; negative lag (dispatched early, which cannot happen once the sleep is taken) is not counted as lateness.

## 5. Dispatch and attribute failures

- The callback receives the tick and returns a `dict` (one order) or `None` (none). Any other type raises `TypeError` — a callback returning a list of orders would otherwise be counted as one.
- A callback that raises aborts the session with `ReplayCallbackError`, carrying `tick_index` and `tick`, chaining the original exception as `__cause__`, and logging the symbol, sequence id and timestamp at ERROR. "Which tick broke the strategy" is the answer a replay exists to give.
- Strategy code must read time from the tick or from `harness.simulated_now()`. This is advisory — nothing can stop a callback calling `time.time()`, which is exactly why it is the first pitfall in `SKILL.md`.

## 6. Read the summary

`ReplaySessionSummary` is **per session**; the accumulators reset on every `replay_session` call.

| Field | Read it for |
|---|---|
| `total_ticks_replayed`, `emitted_orders_count` | Coverage and emission counts for this session |
| `simulated_duration_sec`, `actual_wall_time_sec` | Recorded span vs. what the replay actually took (unrounded, unclamped) |
| `achieved_speed_multiplier` | Realised speed; well below `speed_multiplier` means the consumer is the bottleneck |
| `wall_clock_replay` | Whether deadlines existed at all — the lag fields are 0.0 in ASAP mode |
| `ticks_dispatched_late`, `max_scheduling_lag_sec`, `mean_scheduling_lag_sec` | Timing fidelity; the only honest basis for a latency claim |
| `out_of_order_input_pairs` | Capture quality |

## 7. Diff against the baseline

Compare emitted orders and risk events field-by-field against the stored baseline; store the baseline from an **ASAP** run. Never put a wall-clock number in a regression baseline — timed replays are reproducible in content, not in timing.

For the comparison layer itself — thresholds, scenario coverage, pass/fail gating on execution metrics — see `execution-algorithm-regression-testing-suite`. This skill's job ends at delivering the ticks and telling you how faithfully it did so.

## 8. Wire it into CI

- ASAP replays in every pipeline run; timed replays on a dedicated, unshared runner, since a loaded CI box produces lag that has nothing to do with the code under test.
- Fail the build on: any `ReplayCallbackError`, any diff against the baseline, any `out_of_order_input_pairs` on a capture that should be clean, and — for timed runs only — `ticks_dispatched_late > 0`.
- Record the harness configuration and the capture's identity (file hash, session date, venue) alongside the result. RTS 6 Art. 5(7) requires records of material changes to algorithmic trading software: when, by whom, approved by whom, and of what nature.

## Production Implementation Reference

- Reference code: `scripts/replay_harness.py` (`MarketDataReplayHarness`, `ReplayTick`, `ReplaySessionSummary`, `ReplayError`, `ReplayOrderingError`, `ReplayCallbackError`).
- Automated unit tests: `scripts/test_replay_harness.py` — scheduler behaviour is asserted against an injected fake clock, so the drift, speed-scaling and lag properties are exact rather than dependent on the host's scheduler.

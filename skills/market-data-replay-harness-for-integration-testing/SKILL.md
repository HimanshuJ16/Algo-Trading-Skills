---
name: market-data-replay-harness-for-integration-testing
description: Use when integration-testing a trading engine, risk layer or execution
  algorithm against a recorded tick session — replaying captured ticks through the
  real pipeline in a reproducible order, at 1x, at a speed multiplier or as fast as
  the consumer can take them, and measuring the scheduling lag so the replay's timing
  fidelity is proven rather than assumed
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- tick-replay
- integration-testing
- deterministic-replay
- mifid-ii-rts-6
- event-driven
brokers_frameworks:
- Tick Replay Harness
- MiFID II RTS 6 (EU 2017/589)
- FCA Algorithmic Trading Compliance (Feb 2018)
- Python standard library (time, dataclasses)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when the code under test is the **real pipeline** — feed handler, strategy, risk layer, OMS — and the input needs to be a recorded session rather than a mock: a CPI print, the 6 May 2010 flash crash, the open after a halt. Synthetic ticks reproduce neither the micro-burst arrival spacing nor the sequence of book states that break event-driven code, so the questions a replay answers are the ones a unit test cannot: does every tick reach the strategy, in the same order, every run; does the pipeline still keep up at 10x; does the risk layer fire on the same tick it fired on last release.

For EU/EEA investment firms this is also the environment obligation. **RTS 6 Art. 7(1)** requires that testing against the Art. 5(4)(a), (b) and (d) criteria happen "in an environment that is separated from its production environment" — and the regulation's definition of production environment explicitly includes *market data* and *data capture*. Replaying a file is how that separation is achieved without pointing the system under test at the live feed. See `references/standards.md` for who is bound and by what.

## When NOT to Use

- **As evidence the algorithm does not contribute to disorderly trading.** RTS 6 Art. 5(4)(d) requires exactly that, and a recorded book cannot answer it: your orders do not move a file. The FCA's Feb 2018 review names the shortcut as poor practice and asks instead for "dynamic testing environments, that not only consider how their algorithmic trading strategies perform in a period of market disruption, but also assess whether their strategy further contributes (in combination with other trading activity) to market disruption."
- **As conformance testing.** RTS 6 Art. 6 requires testing against the *trading venue's* or DEA provider's own system, verifying the algorithm "interacts with the trading venue's matching logic as intended". A recorded file has no matching logic. Book venue conformance sessions separately.
- **As a fill model or a backtest.** Nothing here matches, queues, prices or fills an order. Fill realism belongs to `execution-realistic-simulation`; queue position to `queue-position-modeling-for-passive-orders`.
- **As the RTS 6 Art. 10 stress test.** That test is sized by *volume* — the highest message and trade counts of the previous six months, doubled — not by replay *rate*. Replaying a normal session at 10x raises messages per second while leaving the six-month volume untouched; it is a throughput probe, not the Art. 10 test.
- **For sub-millisecond arrival fidelity.** `time.sleep()` "may be longer than requested by an arbitrary amount, because of the scheduling of other activity in the system" (CPython `time` docs). Measured on CPython 3.11 / Windows 11, sleeps from 100 µs to 5 ms overshot by ~300–900 µs regardless of the requested duration. A user-space Python harness cannot reproduce microsecond tick spacing; if that is what you are testing, drive the NIC, not the scheduler — see `tick-to-trade-latency-measurement`.
- **When you have no capture.** Generating plausible ticks is a different skill: `market-data-simulator-for-offline-development`.

## Prerequisites

- A recorded session (CSV, Parquet, PCAP-derived) carrying, per event: symbol, timestamp, sequence id, bid, ask, volume.
- **Timestamps in seconds.** `ReplayTick.timestamp` is consumed directly as seconds; a millisecond or nanosecond capture must be converted before replay. Set `max_projected_wall_time_sec` so a unit mistake raises instead of sleeping for a decade.
- A single monotonic sequence space. Merge multi-venue captures into one ordering before replaying — the harness's tie-break is deterministic, not venue-aware.
- The strategy/pipeline entry point as a callback `f(tick) -> dict | None`, and strategy code that reads time from the tick (or `harness.simulated_now()`), never from `time.time()`.
- A recorded baseline of expected callback outputs to diff against, if this is a regression gate.

## Workflow

1. **Load the capture and fix the ordering policy before anything else.**
   - **Decision point — decide whether out-of-order input is a bug or a fact.** The harness sorts by `(timestamp, sequence_id)` and reports `out_of_order_input_pairs`, because a capture whose ticks go backwards usually means a broken recorder or two interleaved feeds — a finding about your data, not a detail to sort away in silence. On a capture that is supposed to be clean, run with `strict_ordering=True` so it raises.
   - The sequence tie-break exists for **reproducibility**: without it, two ticks sharing a timestamp replay in whatever order the file was read in, and the "deterministic" harness returns a different order for the same data.

2. **Choose the speed mode against what you are actually testing.**
   - `asap_mode=True` (or `speed_multiplier=inf`) for logic and order-sequence regression: no delays, fully deterministic, and the only mode whose results are comparable run-to-run on a loaded CI box.
   - `speed_multiplier=1.0…100.0` when the test depends on *elapsed* behaviour — timers, rate limiters, staleness detectors, throttles.
   - **Decision point — a timed replay whose consumer cannot keep up is not a slower replay, it is a different test.** Ticks arrive late, timers fire in the wrong relative order, and the assertion you wrote about latency is meaningless. Read `ticks_dispatched_late` and `max_scheduling_lag_sec`; the harness warns, but the test must fail on them itself.

3. **Replay against absolute deadlines, not per-tick sleeps.**
   - Each tick's deadline is $t_{\text{wall},0} + (t_i - t_0)/S$, computed from the session start. Callback cost and sleep overshoot are absorbed rather than accumulated.
   - **Decision point — never sleep the gap.** Sleeping $\Delta t_{i} / S$ after each callback adds every callback's cost and every scheduler overshoot to the session: with 10 ms spacing and a 5 ms callback, the recorded 90 ms takes 150 ms and every downstream timing assertion is wrong by 58%. This is the defect fixed in v2.0.0.
   - Gaps shorter than `min_sleep_sec` (default 500 µs) are dispatched immediately and the shortfall is booked as lag, because the OS cannot deliver them anyway.

4. **Dispatch, and let failures name their tick.** A callback that raises aborts the session with a `ReplayCallbackError` carrying the tick index, symbol, sequence id and timestamp, chaining the original exception. A callback returns a `dict` to emit an order or `None` for none; anything else raises rather than being miscounted.

5. **Diff the audit trail, not the clock.** Compare emitted orders and risk events against the stored baseline. Timings are reproducible only in ASAP mode; never put a wall-clock number in a regression baseline.
   - **Decision point — read the per-session summary, and know it is per-session.** Counts reset on every `replay_session` call (they did not before v2.0.0, so a suite reusing one harness read inflated order counts).

> Full procedure: see `references/workflows.md`.
> Standards, citations and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sleeping the inter-tick gap.** The naive loop — `sleep((t[i+1]-t[i])/S)` after each callback — drifts by the total cost of every callback plus every scheduler overshoot. It is slowest exactly where it matters most, on the burst the test was built around.
- **Calling `time.time()` inside the strategy.** The tick carries the recorded time; the wall clock carries the test runner's. Mixing them makes results unreproducible and, in a strategy that ages quotes, silently wrong. Use the tick's timestamp or `harness.simulated_now()`.
- **Trusting a timed replay that ran late.** `ticks_dispatched_late > 0` means the strategy never saw the recorded spacing. The run is still a valid logic test and is *not* a valid latency test.
- **Reusing one harness across sessions and reading the counts.** Fixed in v2.0.0 — but any harness of your own that accumulates into instance state has the same bug, and it inflates rather than errors, so it looks like a passing test.
- **Letting ties resolve by file order.** Two ticks with the same timestamp and no sequence tie-break replay differently depending on how the file was read. Determinism claims fail here first.
- **Feeding millisecond or nanosecond timestamps.** A 3-second capture in milliseconds is a 50-minute replay; in nanoseconds, decades. Nothing in the arithmetic complains — set `max_projected_wall_time_sec`.
- **NaN timestamps from a bad parse.** They compare false against everything and scatter the sort order silently. The harness rejects non-finite timestamps for this reason.
- **Retaining every tick.** A harness that appends millions of ticks to an instance list for a summary count exhausts memory long before the session ends. Use `retain_replayed_ticks=False`.
- **Treating a green replay as market-impact evidence.** The recording does not respond to your orders. See *When NOT to Use*.
- **Replaying from the production capture path.** RTS 6 Art. 7(1) requires the testing environment to be *separated* from production, and production is defined to include market data and data capture. Read from a copy.

## Verification

- **No drift (regression).** 10 ticks spaced 10 ms, a callback that consumes 5 ms, `speed_multiplier=1.0`: against an injected fake clock, every sleep is exactly 5 ms and the session spans 95 ms (90 ms recorded + the final callback). The pre-2.0 per-tick scheme takes 150 ms — the test fails against it and passes against the fix.
- **Speed scaling.** 100 ms gaps at 10x produce exactly 10 ms sleeps and a 40 ms session over 5 ticks; `achieved_speed_multiplier ≈ 10.0`.
- **Lag is reported, not absorbed.** A 20 ms callback on 10 ms spacing: no sleeps, 4 of 5 ticks late, `max_scheduling_lag_sec = 0.040`, mean lag 0.020, and a WARNING naming the consumer.
- **Tie determinism (regression).** Ticks `(1000.0, seq 9)`, `(1000.0, seq 2)`, `(999.5, seq 5)` replay as 5, 2, 9 — and the reversed input replays identically. The pre-2.0 stable-sort-on-timestamp gives 5, 9, 2.
- **Per-session isolation (regression).** Two sessions on one harness each report 3 orders, not 3 then 6.
- **Honest measurement.** A session that takes no measurable time reports `actual_wall_time_sec == 0.0`, not a fabricated 0.0001 floor.
- **Input rejection.** `speed_multiplier` of 0, negative, NaN, bool or string; a non-callable callback; a non-sequence tick log; a non-finite timestamp; a non-integer sequence id; a negative `min_sleep_sec` — each raises `ValueError`. `strict_ordering=True` on an out-of-order capture raises `ReplayOrderingError`.
- **Unit guard.** A capture in milliseconds against `max_projected_wall_time_sec` raises with the unit named; with no guard set, an implausible projection logs a WARNING.
- **Callback failure attribution.** A callback raising on the second tick produces `ReplayCallbackError` with `tick_index == 1`, `tick.sequence_id == 2`, and the original exception as `__cause__`.
- **Emission counting.** A callback returning `{}` counts as an order (it was silently dropped pre-2.0); returning a list raises `TypeError`.
- Run `python -m unittest discover -s skills/market-data-replay-harness-for-integration-testing/scripts` and confirm a 100% pass rate.

## Related Skills

- `market-data-simulator-for-offline-development`
- `execution-algorithm-regression-testing-suite`
- `execution-realistic-simulation`
- `backtest-determinism-and-reproducibility`
- `historical-order-book-reconstruction-from-message-logs`
- `sequence-number-gap-detection-for-feeds`
- `producer-consumer-tick-pipeline`
- `tick-to-trade-latency-measurement`
- `paper-to-live-promotion-checklist`
- `structured-logging-for-post-incident-forensics`

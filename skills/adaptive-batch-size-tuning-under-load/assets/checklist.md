# Pre-Flight / Sign-off Checklist — adaptive-batch-size-tuning-under-load

Use this before promoting a tuner integration to production.

---

## 1. Pre-Integration

- [ ] **Sink latency instrumented** — every write call records `(t1 - t0) ms` and
      passes the value to `tuner.record_write_latency(ms)`.
- [ ] **Latency recorded on the failure path too** — the call sits in a
      `finally`, not only after a successful write.
- [ ] **Capacity constants picked from vendor defaults** (TimescaleDB /
      ClickHouse / Redis Streams table in `references/workflows.md` §2).
- [ ] **`tuner.add_item()` exception handling** — `QueueFullError` is caught
      and routed into the project's back-pressure policy
      (`backpressure-drop-degrade-policy`); the caller still owns the rejected
      item.
- [ ] **One engine per sink** — verify producer code does not instantiate
      a new `AdaptiveBatchTunerEngine` per `add_item` call.
- [ ] **Timer tick wired** — something calls `tuner.flush_if_due()` on a
      schedule, or the flush timeout can never fire while the producer is quiet.

## 2. Production Configuration

- [ ] **`max_queue_size` is bounded** — not the default `5000` if your sink's
      RAM allows much less; tune conservatively.
- [ ] **`queue_capacity <= max_queue_size`** — enforced at construction; confirm
      the gauge denominator is the number your dashboards actually alert on.
- [ ] **`target_write_latency_ms` aligned with measured sink latency** —
      this is what decides where the batch size settles, because expansion is
      barred above it. Typical starting points:
      - ClickHouse / TimescaleDB: 50–100 ms
      - Redis Streams: 20–30 ms
- [ ] **EWMA alphas non-zero** — both `fill_ewma_alpha` and `latency_ewma_alpha`
      in `(0, 1]`; engine rejects 0 at construction.
- [ ] **Tuning multipliers left at defaults** unless you have re-derived the
      stability argument in `references/standards.md`.

## 3. Functional Verification

- [ ] **High-load regime expands batch**: drive a saturating burst and observe
      `current_batch_size` rising toward `B_max`. If it collapses toward
      `B_min` instead, the control signal is mis-wired — stop and investigate.
- [ ] **Low-load regime shrinks batch**: trickle one record per flush window
      and observe `current_batch_size` trending toward `B_min` and
      `current_flush_timeout_sec` toward `T_max`.
- [ ] **DB latency throttling**: drive `record_write_latency(150)` repeatedly
      and observe `current_batch_size` shrink by 0.8× each time, then stop at
      `B_min`.
- [ ] **Throttle outranks expansion**: with a slow sink, confirm the batch size
      does **not** climb to `B_max`.
- [ ] **Deadband sanity**: with batches cut ~50% full, no tuning transitions
      should occur.
- [ ] **Automated tests**: `python -m unittest discover -s scripts -v` — 43/43 pass.

## 4. Observability

- [ ] **`BatchTunerStatus.as_dict()` is exported** to your metrics pipeline
      (Prometheus exporter / StatsD / Vector / etc.), serialised with
      `allow_nan=False`.
- [ ] **Alert configured on `QueueFullError > 0`**.
- [ ] **Alert configured on `total_tuning_transitions` rate > 60/hour**.
- [ ] **Alert configured on `current_batch_size == min_batch_size` while the
      feed is busy** — the signature of a stuck throttle or a mis-wired signal.
- [ ] **P99 sink latency tracked** against `target_write_latency_ms`.

## 5. Failure Injection (staging)

- [ ] **Kill sink DB** — observe latency throttle shrinking batch and queue
      eventually hitting `max_queue_size`. Verify `QueueFullError` rate is
      non-zero and the upstream back-pressure policy engages.
- [ ] **Slow sink latency** (artificial 200 ms sleep) — observe throttle
      events in logs and gradual shrink to `B_min`, with no expansion in
      between.
- [ ] **Burst load** — inject a 10× burst, observe engine expands batch within
      ~10 flushes and recovers to baseline within 60 seconds of burst end.
- [ ] **Producer stall** — stop the feed with records buffered; confirm the
      scheduler's `flush_if_due()` releases them within `T_flush`.
- [ ] **Empty queue shutdown** — `tuner.close()` returns `[]` cleanly.
- [ ] **Loaded shutdown** — buffer more records than `current_batch_size`, call
      `close()`, and confirm **every** record comes back and is written. This is
      the shutdown data-loss case; do not skip it.
- [ ] **Callback safety** — if using `on_flush`, confirm a raising callback is
      logged without losing the batch.

## 6. Rollout

- [ ] **Canary 5% of traffic** for at least 1 trading day.
- [ ] **Compare P99 latency** vs. the previous statically-tuned baseline.
- [ ] **Reconcile expected vs. actual `total_tuning_transitions`**: should be
      O(tens/day) for steady traffic, <120/hour.

## 7. Rollback

- [ ] **Static-batch fallback path documented and exercised** — the consumer
      keeps a branch that bypasses the engine entirely and writes fixed-size
      batches on a fixed timer, selected by config without a code change.
      Rolling back means flipping that flag, not editing the producer loop.
- [ ] **Drain on rollback** — the switch calls `tuner.close()` and writes the
      remainder before the static path takes over.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Tuner `TuningConfig` snapshot (paste JSON): ___________________________
- Staging `BatchTunerStatus.as_dict()` after 1h traffic: ___________________________

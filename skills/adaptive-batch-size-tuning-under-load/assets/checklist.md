# Pre-Flight / Sign-off Checklist — adaptive-batch-size-tuning-under-load

Use this before promoting a tuner integration to production.

---

## 1. Pre-Integration

- [ ] **Sink latency instrumented** — every write call records `(t1 - t0) ms` and
      passes the value to `tuner.record_write_latency(ms)`.
- [ ] **Capacity constants picked from vendor defaults** (TimescaleDB / ClickHouse /
      Kafka / Redis Streams table in `references/workflows.md` §2).
- [ ] **`tuner.add_item()` exception handling** — `QueueFullError` is caught
      and routed into the project's back-pressure policy (`backpressure-drop-degrade-policy`).
- [ ] **One engine per sink** — verify producer code does not instantiate
      a new `AdaptiveBatchTunerEngine` per `add_item` call.

## 2. Production Configuration

- [ ] **`max_queue_size` is bounded** — not the default `5000` if your sink's
      RAM allows much less; tune conservatively.
- [ ] **`target_write_latency_ms` aligned with sink SLA** — typically:
      - ClickHouse / TimescaleDB: 50–100 ms
      - Kafka producer flush: 5–25 ms
      - Redis Streams: 20–30 ms
- [ ] **EWMA alphas non-zero** — both `fill_ewma_alpha` and `latency_ewma_alpha`
      in `(0, 1]`; engine rejects 0 at construction.

## 3. Functional Verification

- [ ] **Low-load regime shrinks batch**: fill only ~5% of capacity, observe
      `current_batch_size` trending toward `B_min`.
- [ ] **High-load regime expands batch**: fill 80%+ of capacity, observe
      `current_batch_size` trending toward `B_max`.
- [ ] **DB latency throttling**: drive `record_write_latency(150)` repeatedly
      and observe `current_batch_size` shrink by 0.8× each time.
- [ ] **Automated tests**: `python scripts/test_batch_tuner.py` — 17/17 pass.
- [ ] **Deadband sanity**: at fill = 30–50%, no tuning transitions should occur.

## 4. Observability

- [ ] **`BatchTunerStatus.as_dict()` is exported** to your metrics pipeline
      (Prometheus exporter / StatsD / Vector / etc.).
- [ ] **Alert configured on `QueueFullError > 0`**.
- [ ] **Alert configured on `total_tuning_transitions` rate > 60/hour**.
- [ ] **P99 sink latency tracked** against `target_write_latency_ms`.

## 5. Failure Injection (staging)

- [ ] **Kill sink DB** — observe latency throttle shrinking batch and queue
      eventually hitting `max_queue_size`. Verify `QueueFullError` rate is
      non-zero and the upstream back-pressure policy engages.
- [ ] **Slow sink latency** (artificial 200 ms sleep) — observe throttle
      events in logs and gradual shrink to `B_min`.
- [ ] **Burst load** — inject a 10× burst, observe engine expands batch within
      ~10 flushes and recovers to baseline within 60 seconds of burst end.
- [ ] **Empty queue shutdown** — `tuner.close()` returns `[]` cleanly.

## 6. Rollout

- [ ] **Canary 5% of traffic** for at least 1 trading day.
- [ ] **Compare P99 latency** vs. the previous statically-tuned baseline.
- [ ] **Reconcile expected vs. actual `total_tuning_transitions`**: should be
      O(tens/day) for steady traffic, <120/hour.

## 7. Rollback

- [ ] **Static-batch fallback path documented** — if the engine catastrophically
      mis-tunes, switch the producer loop to `BatchTunerStatus.current_batch_size`
      ... actually no. Static fallback means removing the engine and routing
      writes through a fixed-batch path. Document that branch in the consumer.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Tuner `TuningConfig` snapshot (paste JSON): ___________________________
- Staging `BatchTunerStatus.as_dict()` after 1h traffic: ___________________________

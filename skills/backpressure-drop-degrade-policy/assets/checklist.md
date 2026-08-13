# Pre-Flight / Sign-off Checklist — backpressure-drop-degrade-policy

Use this before considering the skill's implementation complete.

## Policy declaration
- [ ] **Every** stream has an explicitly declared policy — no stream relies on a default
- [ ] Undeclared / mistyped stream names raise `UnknownStreamError` (`strict_unknown_stream=True`)
- [ ] Risk-relevant streams (position, margin, kill-switch feeds) are classified `NEVER_DROP`
- [ ] Policy classification reviewed against current strategy set, not inherited from an older design

## Caller contract
- [ ] Every `handle_full()` call site checks `decision.accepted`
- [ ] `NEVER_DROP` rejections route `decision.rejected_item` to an escalation path
- [ ] `on_never_drop_overflow` wired to the emergency handler (e.g. kill switch), or `strict_never_drop=True`
- [ ] No call site treats a return value of "nothing to do" as "item was handled"

## Queue and threading
- [ ] `NEVER_DROP` streams have dedicated queues **and** dedicated worker pools — no sharing with droppable tiers
- [ ] No code path appends directly to a full `deque(maxlen=N)` (that is an implicit, unchosen drop-oldest)
- [ ] No code path reads `len(queue)` and then pops repeatedly without guarding each pop
- [ ] Overflow path verified not to raise while a consumer drains the queue concurrently

## Early warning and alerting
- [ ] `observe()` called on **every** push, not only on overflow
- [ ] `high_watermark_pct` set deliberately (default 0.8) and alerts confirmed to fire below capacity
- [ ] `NEVER_DROP` watermark alerts confirmed to fire at `CRITICAL` severity **before** capacity
- [ ] A real out-of-band `alert_fn` is wired — not left at the default log-only sink
- [ ] Alert cooldown verified to prevent flooding during a sustained volatility spike
- [ ] Alert-sink failure confirmed not to propagate into the pipeline

## Policy behaviour
- [ ] **`DROP_OLDEST`:** evicts exactly one oldest item and admits the newest
- [ ] **`SAMPLE`:** admits 1 of every N and leaves the existing backlog intact (does **not** flush the queue)
- [ ] **`DEGRADE`:** produces correct OHLC bars; ticks missing a price are rejected, not zero-filled
- [ ] **`DEGRADE`:** `interval_sec` is positive and finite; out-of-order ticks do not rewrite a closed bar

## Telemetry
- [ ] `get_metrics_summary()` reviewed at session close
- [ ] `never_drop_overflows` is **zero** — any non-zero value is an incident, not a statistic
- [ ] `drop_rate_pct` understood as discards over observed pushes, and `None` means "nothing observed", not "healthy"
- [ ] Watermark breach counts reviewed against expected peak load

## Automated tests
- [ ] Execute `python scripts/test_backpressure_policy.py` — all 38 tests pass
- [ ] Load-test at multiples of peak historical tick rate before promoting to live

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

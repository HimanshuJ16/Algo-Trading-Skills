# Deep Workflow Reference — websocket-reconnection-with-state-recovery

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Decide which recovery path you actually have

Before writing any code, answer this — it determines the whole configuration:

| Venue capability | Configuration | Recovery on a gap |
|---|---|---|
| Id-addressable history endpoint (e.g. Binance `aggTrades?fromId=`) | pass `rest_gap_fill_fn`, set `max_gap_fill_size` to the endpoint's page size | fill the range, validated |
| Snapshot endpoint only (order book deltas — Binance `/api/v3/depth`) | leave `rest_gap_fill_fn=None` | latch, re-snapshot, `resynchronize()` |
| Neither (Coinbase Advanced Trade `sequence_num`) | leave `rest_gap_fill_fn=None` | latch, rebuild state, `resynchronize()` |

Getting this wrong is not a performance problem. Configuring a fill callback that cannot
actually return the missing range produces a permanent latch on the first gap; assuming
one exists when it does not produces a book that is silently wrong.

## 1. Connection lifecycle

1. Register the desired subscription set with `register_symbol_subscription()`. Symbols are
   trimmed and upper-cased, so the set is the *desired state* and not a log of calls.
2. On drop, call `on_connection_lost(reason)` and sleep the returned delay. Pass
   `scheduled=True` when the eviction was expected (the 24-hour connection lifetime, a
   published maintenance window) so it does not escalate the failure counter.
3. Reconnect, then call `on_connection_established()`. It transitions
   `CONNECTING -> [AUTHENTICATED] -> SUBSCRIBED` (`AUTHENTICATED` only when
   `requires_auth=True`) and returns the sorted symbol list to send subscribe frames for.
4. `state_history` records every transition, bounded at 64 entries, so the state machine is
   auditable after an incident rather than asserted in a diagram.
5. The failure counter clears only when a message is processed end-to-end. A socket that
   opens and dies two seconds later keeps escalating, which is the intended behaviour.

## 2. Backoff arithmetic

```
capped = min(max_backoff_sec, base_backoff_sec * 2 ** min(attempt, 30))
fixed  = capped * (1 - jitter_factor)
delay  = fixed + uniform(0, capped - fixed)
```

- `jitter_factor = 1.0` → `uniform(0, capped)` — AWS Full Jitter.
- `jitter_factor = 0.5` → `capped/2 + uniform(0, capped/2)` — AWS Equal Jitter.
- `jitter_factor = 0.0` → exactly `capped`.

`delay <= max_backoff_sec` holds for every attempt index and every jitter factor. The
exponent clamp at 30 is what keeps `base * 2**attempt` off the `OverflowError: int too
large to convert to float` that an unbounded attempt counter reaches past ~1024 retries —
about nine hours at a 30 s cadence, which is well inside a real venue outage.

## 3. Message classification

For each incoming message, against the per-symbol watermark:

| Condition | Action |
|---|---|
| symbol latched unsynchronised | return `[]`, increment `withheld_message_count` |
| no watermark yet | adopt as baseline, emit, log that nothing before it is observable |
| `seq <= watermark` | return `[]`, increment `duplicate_message_count`, **watermark unchanged** |
| `seq == watermark + 1` | emit, advance watermark |
| `seq > watermark + 1` | gap — go to §4 |

The `seq <= watermark` rule is load-bearing. Moving the watermark backwards on a late frame
re-emits messages the consumer has already applied *and* fabricates a gap on the next
message, triggering a refetch of data already held. A *large* backward jump means something
different — a publisher restart or a weekly sequence reset — and is handled by confirming
the venue's restart signal and calling `resynchronize()`, not by waiting.

## 4. Gap recovery

1. Compute the missing range `[watermark + 1, seq - 1]` and transition to
   `RECOVERING_GAP`.
2. If `rest_gap_fill_fn is None` → latch (§5).
3. If the gap is wider than `max_gap_fill_size` → latch, without calling the endpoint. An
   outage-sized hole is a re-snapshot, and paging it spends rate-limit weight against a
   venue that is already degraded.
4. Call `rest_gap_fill_fn(symbol, first_missing, last_missing)`. Any exception is caught,
   logged with traceback, and treated as a failed fill.
5. Validate the response: it must be a sequence of `WSMessage`, of exactly the expected
   length, all for the requested symbol, with sequence ids ascending and contiguous from
   `first_missing`. A short page, an empty list, `None`, a reordered range or a foreign
   symbol all fail.
6. On success, emit the recovered messages **before** the triggering message, advance the
   watermark across them, and increment `gap_fill_success_count`.

The callback runs while the manager's lock is held. Give it its own network timeout — a
hung HTTP request freezes ingestion for every symbol, not just the broken one.

## 5. The latch, and clearing it

A failed fill records a `SequenceGap(symbol, first_missing, last_missing, reason)`,
increments `gap_fill_failure_count`, holds the state at `RECOVERING_GAP` and logs at
`ERROR`. From then on, for that symbol only:

- `is_synchronized(symbol)` is `False`; `is_synchronized()` with no argument is `False`
  while *any* symbol is latched.
- `unrecovered_gaps()` returns the exact missing ranges and why each one failed.
- every subsequent message returns `[]` and increments `withheld_message_count`.
- other symbols are unaffected and keep emitting.

Clearing it is the venue's documented recovery, in this order:

1. Fetch a fresh snapshot (`GET /api/v3/depth`, or the venue's equivalent).
2. Rebuild the consumer's local state from that snapshot — discard the old book, do not
   merge.
3. Call `resynchronize(symbol, snapshot_last_update_id + 1)`. The watermark is set so the
   next expected sequence is exactly that value, and the latch is released.

Gate order entry and any state-derived signal on `is_synchronized()` throughout. Detecting
that the book is unreliable is not the same as protecting capital while it is — that is
`capital-preservation-mode-for-degraded-conditions` and
`kill-switch-and-drawdown-circuit-breakers`.

## 6. Operational instrumentation

Export these to monitoring; each one names a distinct failure:

| Counter | Rising means |
|---|---|
| `reconnect_attempts` | consecutive failed reconnects — a venue or network problem |
| `duplicate_message_count` | replayed/out-of-order frames — usually a reconnect boundary |
| `gap_fill_success_count` | gaps repaired without human involvement |
| `gap_fill_failure_count` | gaps that needed a re-snapshot — investigate every one |
| `withheld_message_count` | messages dropped while latched; a rising count with no `resynchronize()` means the consumer is blind and nobody noticed |

## Production Implementation Reference

- Reference code: `scripts/ws_recovery.py`
  (`WebSocketStateRecoveryManager`, `ConnectionState`, `SequenceGap`, `WSMessage`).
- Automated unit tests: `scripts/test_ws_recovery.py`.

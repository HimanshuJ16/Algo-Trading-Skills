# Deep Workflow Reference — feed-handler-canary-deployment

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Before the canary starts

- Confirm both handlers consume the **same** stream and hold **independent** state. A
  shared order book makes the comparison meaningless, because a canary defect has already
  corrupted the baseline's output.
- Confirm you can pair outputs by message identity — exchange sequence number, or symbol
  plus exchange timestamp. If neither handler exposes one, add it before deploying; do not
  fall back to arrival order.
- Confirm entitlement and capacity for a second full-universe consumer (see
  `references/standards.md`, Section 4).
- Write down, before starting: the observation window in **message counts and session
  events covered**, the promotion criteria, the rollback criteria, and the named
  authoriser.

## 1. Configure the allocation

```python
router = FeedHandlerCanaryRouter(
    canary_percentage=10.0,
    canary_symbols=["BRK.A", "ZVZZT"],   # pinned awkward formats, not mega-caps
    max_allowed_error_rate=0.01,
    price_tolerance=0.0,                 # exact agreement; see step 3
    min_ticks_before_rollback=1_000,     # calibrate to the feed's tick rate
    max_allowed_exceptions=0,
)
```

- Allocation is a stable digest bucket at 0.01% granularity. It is reproducible across
  processes and restarts; Python's salted `hash()` is not (`PYTHONHASHSEED`).
- The whitelist is where representativeness is bought. The hash samples the universe, and
  a universe is mostly ordinary symbols; parser defects are not.
- `min_ticks_before_rollback` at the default of 10 is right for a test harness and far too
  low for a live feed, where 10 ticks pass in milliseconds. Set it to a sample size at
  which the error *rate* means something.

## 2. Route output and audit aligned pairs

```python
decision = router.route_symbol(tick.symbol)
publish(tick_from(decision.version_tag))

router.audit_tick_pair(
    tick.symbol,
    price_stable=stable_decoded.price,
    price_canary=canary_decoded.price,
    sequence_number=stable_decoded.seq,
    canary_sequence_number=canary_decoded.seq,
)
```

- Call `route_symbol()` per tick. A routing table cached at startup will not observe a
  rollback, and will keep publishing canary output after the breaker has tripped.
- Pass both sequence numbers whenever available. Disagreement raises `ValueError` — that
  is a caller alignment bug, and it is deliberately **not** counted as a canary error.
- `decision.reason` distinguishes `whitelist` from `hash_bucket` from `rolled_back`, which
  is what you need when explaining after the fact why a given symbol was on the canary.

## 3. Grade agreement

Default `price_tolerance=0.0`. Both handlers decode the same message, and exchange prices
are exact fixed-point integers, so any difference is a defect.

A mismatch is recorded when:

| Condition | Reason code | Why it is not "close enough" |
|---|---|---|
| Either price is `NaN` or `±Inf` | `non_finite_price` | `abs(nan - p) / p > tol` is `False`, so a relative check scores `NaN` as agreement. |
| Either price is `<= 0` | `non_positive_price` | A zero price is the finding, not a divide-by-zero to route around. |
| Prices differ, `price_tolerance == 0` | `exact_mismatch` | Same input, same expected output. |
| Relative difference exceeds tolerance | `tolerance_breach` | Only reachable when you have deliberately loosened the default. |

`audit_tick_pair()` returns whether the **deployment** may continue, not whether the tick
matched. A single mismatch still returns `True`. Read `get_audit_summary()` for counts.

## 4. Monitor

- `router.error_rate` — combined mismatch+exception fraction of audited ticks.
- `router.get_audit_summary(universe)` — status, current split, counts, rollback reason.
  After a rollback the split reports 0 canary symbols, because that is the current routing
  state; check `is_rolled_back` before reading the counts as a description of the run.
- Watch mismatch reasons, not only the rate. An error rate made of `non_finite_price` is a
  different bug from one made of `tolerance_breach`.

## 5. Ramp, promote, or roll back

```python
router.set_canary_percentage(50.0, authorised_by="release-manager")
router.promote_to_full(authorised_by="release-manager")
```

- Ramping only adds symbols. A symbol on the canary at 10% is still on it at 50%, so the
  ramp never reassigns ownership of an instrument mid-deployment.
- The breaker trips on: combined error rate above `max_allowed_error_rate` once
  `min_ticks_before_rollback` ticks have been audited, or the first unhandled canary
  exception beyond `max_allowed_exceptions`.
- After a rollback the deployment is over. `set_canary_percentage()` raises
  `RuntimeError`; there is no reduced-percentage retry. `force_rollback()` is always
  available and is idempotent — the first reason is the one retained.
- Export `router.events` (timestamped `RAMP` / `PROMOTION` / `MANUAL_ROLLBACK` /
  `ROLLBACK`, each with `authorised_by`) into your change management record.

## 6. Concurrency

The router is called from feed handler threads and guards all mutable state with a lock.
If you reimplement it, note that `self.total_ticks_processed += 1` is not atomic: lost
increments understate the denominator of the error rate, which is the number the breaker
fires on.

## Production Implementation Reference

- Reference code: `scripts/canary_router.py` (`FeedHandlerCanaryRouter`, `CanaryStatus`,
  `CanaryRoutingDecision`, `CanaryAuditSummary`, `CanaryEvent`).
- Automated unit tests: `scripts/test_canary_router.py`.

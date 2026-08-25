# Deep Workflow Reference — execution-algo-twap-vwap-slicing

The full technical procedure behind `SKILL.md`. Load this when implementing, not when
deciding whether the skill applies.

Reference implementation: `scripts/slicer.py`. Tests: `scripts/test_slicer.py`.

## Public surface

| Symbol | Purpose |
|---|---|
| `allocate_lots(total_qty, weights, lot_size, jitter_pct, rng)` | Largest-remainder apportionment in whole lots. Exact conservation, no negative clips. |
| `twap_schedule(total_qty, num_intervals, jitter_pct, lot_size, rng)` | Equal-weight schedule. |
| `vwap_schedule(total_qty, curve, jitter_pct, lot_size, rng)` | Volume-curve-weighted schedule. |
| `ExecutionSlicer` | Stateful parent-order engine. |
| `ChildOrderSlice` | One child order: target, time, fills, status, reject reason. |
| `SlicerType` / `OrderSide` / `SliceStatus` / `CatchUpPolicy` | Enums; all subclass `str`. |
| `ExecutionReport` | Post-trade result. |

## Full procedure

### 1. Benchmark and parameter selection

```python
slicer = ExecutionSlicer(
    total_qty=100_000,                  # whole multiple of lot_size
    algo_type=SlicerType.VWAP,
    num_intervals=12,
    interval_seconds=300.0,
    historical_volume_curve=curve,      # len(curve) MUST equal num_intervals
    jitter_pct=0.15,                    # bounded to [0, 0.5)
    catch_up_policy=CatchUpPolicy.GIVE_UP_AT_DEADLINE,
    max_child_multiple=2.0,             # cap catch-up; see step 4
    side=OrderSide.BUY,                 # required to sign slippage
    lot_size=1.0,
    seed=20260824,                      # reproducible backtests
    start_time=window_open_epoch,
    deadline=window_close_epoch,
)
```

Everything is validated at construction and raises `ValueError` on a bad value — a
mis-parameterised execution algorithm should fail before it routes, not halfway through
a live parent order. In particular: `num_intervals <= 0`, non-positive or non-finite
`total_qty`, `jitter_pct >= 0.5`, a VWAP curve of the wrong length, a missing VWAP curve,
a negative curve weight, and a `total_qty` that is not a whole multiple of `lot_size` are
all rejected.

`start_time=0.0` is honoured as a real epoch, not treated as absent.

### 2. Schedule generation and anti-pattern jitter

- Weights are normalised (they need not sum to 1.0), jittered by `±jitter_pct`, then
  apportioned by **largest remainder (Hamilton)** over integer lot counts.
- Guarantees: `sum(target_qty) == total_qty` exactly; every clip is a non-negative whole
  number of lots; fractional instruments work (`lot_size=0.001` for a 0.5 BTC parent).
- Timing: slice $i$ is placed at `start_time + (i ± jitter_pct) * interval_seconds`. The
  first slice draws **one-sided** jitter, so it is not clamped onto an exactly
  predictable `start_time`. `jitter_pct < 0.5` guarantees timestamps stay ordered.
- Randomness comes from a slicer-local `random.Random(seed)`. The module never reads or
  mutates the process-wide `random` state.
- If the parent is fewer lots than intervals, some slices are zero-sized and a warning
  names the achievable count. Send only `slicer.actionable_slices()`.

### 3. Live loop — fills, expiries, rejections

```python
for child in slicer.actionable_slices():
    wait_until(child.target_time)
    ack = place_child_order(child)          # via order-placement-idempotency
    for exec_report in ack.fills:
        slicer.on_child_fill(child.slice_id, exec_report.qty, exec_report.price)
    if ack.rejected:
        slicer.on_child_reject(child.slice_id, reason=ack.reject_code)
    elif ack.residual_outstanding:
        slicer.on_child_expired(child.slice_id)
    assert slicer.quantity_invariant_ok()
```

**Fill accounting and child-order closure are separate on purpose.**
`on_child_fill()` only accumulates quantity and the quantity-weighted average price; a
working child order may fill more. Closing the child order —
`on_child_expired` / `on_child_reject` / `on_child_cancel` — truncates its `target_qty`
to what actually filled and releases the residual to the catch-up policy. Doing both at
once, or releasing residual without truncating, is what breaks the quantity invariant.

Error behaviour, all deliberate:

| Event | Behaviour |
|---|---|
| Unknown `slice_id` | Raises `KeyError`. Swallowing it would erase a fill that really happened at the broker. |
| Non-finite / non-positive fill qty or price | Raises `ValueError`. |
| Fill on an already REJECTED/CANCELLED slice | Logged at ERROR and **recorded anyway** — the position is real. Reconcile before routing more. |
| Total fills exceed the parent | `overfill_qty` set, logged at ERROR, surfaced in the report. Never discarded. |
| Closing an already-closed slice | Logged at WARNING and ignored (idempotent). |

If the placement request **timed out**, that is not a rejection — the venue may have
accepted it. Reconcile before calling `on_child_reject`, or the catch-up policy will
schedule a duplicate.

### 4. Catch-up policy

Invariant after every transition:
`sum(target_qty) + unassigned_qty == total_qty`, re-checkable via
`quantity_invariant_ok()`.

| Policy | Behaviour |
|---|---|
| `PASSIVE_CONTINUE` (default) | Open slices untouched. Residual accumulates in `unassigned_qty` and is reported as unfilled. Caps impact, accepts execution risk. |
| `AGGRESSIVE_CATCHUP` | Residual redistributed across open slices **pro-rata to their existing targets**, so a VWAP curve is preserved rather than flattened to equal sizes. |
| `GIVE_UP_AT_DEADLINE` | Slices at or after `deadline` are CANCELLED and their quantity abandoned; the residual is redistributed only into slices strictly before the deadline. |

`max_child_multiple` caps any one slice at that multiple of its **originally scheduled**
size. Quantity above the cap stays unassigned rather than becoming an oversized clip —
this is the client-side counterpart of an RTS 6 Art. 15 maximum-order-volume control.
Leaving it `None` under a non-passive policy logs a warning, because uncapped catch-up
lets a single late child order absorb the entire residual.

### 5. VWAP re-weighting on volume divergence

```python
slicer.reweight_pending(observed_curve)   # full-length curve, one weight per interval
```

Redistributes currently-open quantity plus `unassigned_qty` onto the updated curve.
Filled and abandoned quantity is untouched. Raises if the curve length does not match
`num_intervals`, or if the weights on the open slices are negative or all zero.

`max_child_multiple` is **not** applied here. Re-weighting onto a back-loaded curve is
meant to grow late slices, so capping against the original schedule would fight the
caller's explicit instruction — validate the curve you pass.

### 6. Post-execution reporting

```python
report = slicer.get_execution_report(benchmark_price=interval_vwap, final_price=close)
```

| Field | Meaning |
|---|---|
| `vwap_achieved_price` | $\sum q_i p_i / \sum q_i$ over fills. |
| `slippage_bps` | Side-adjusted cost on the **filled** portion. Positive = worse than benchmark. |
| `opportunity_cost_bps` | Side-adjusted cost on the **unfilled** remainder. `None` unless `final_price` is given. |
| `implementation_shortfall_bps` | $f \cdot \text{slippage} + (1-f) \cdot \text{opportunity cost}$. `None` unless `final_price` is given. |
| `unfilled_qty`, `overfill_qty`, `status_counts`, `quantity_invariant_ok` | Execution hygiene. |

Supply `final_price`. Without it, an algorithm that gave up early posts excellent
slippage on the small portion it filled, and the cost of everything it did not do is
invisible.

## Failure modes observed in production

- **Schedule sums to more than the parent order.** Redistributing a partial fill's
  residual without truncating the partly-filled slice's target. A 1000-share parent with
  one 50-share partial fill schedules 1200 shares and over-executes by 200.
- **VWAP silently degrades to TWAP.** Catch-up flattens every open slice to
  `remaining / count`, discarding the volume curve while keeping the VWAP label.
- **Fractional instrument executes nothing.** Integer rounding turns a 0.5 BTC parent
  into an all-zero schedule.
- **Negative child order.** Patching the accumulated rounding residual onto the last
  slice drives it below zero; downstream this reads as an order on the opposite side.
- **Good sells reported as bad execution.** Side-blind slippage.
- **Deterministic slicing pattern.** Identical clips on exact interval boundaries,
  trivially detectable and tradeable against.
- **Irreproducible backtests.** Jitter drawn from the process-wide RNG.
- **Lost fill.** An execution report for an unrecognised child-order id dropped silently,
  leaving the book short a position that exists at the broker.
- **Uncapped catch-up.** A single late clip absorbing the whole residual — the impact
  event the algorithm was deployed to avoid.

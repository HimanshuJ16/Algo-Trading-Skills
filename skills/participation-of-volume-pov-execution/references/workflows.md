# Workflows — participation-of-volume-pov-execution

Deep procedure for `scripts/participation_of_volume_pov_execution.py`. `SKILL.md` carries
the summary; this file carries the reasoning, the state machine, and the failure handling.

---

## 0. Decide the volume basis before the first tick

The POV identity needs **away** volume — what everyone else traded. Consolidated tape
volume already contains your own executions, because a trade prints once and your fill
is part of that print.

| `config.volume_basis` | Caller supplies | Engine behaviour |
|---|---|---|
| `VolumeBasis.AWAY` (default) | Volume with own executions already excluded | Used as-is |
| `VolumeBasis.CONSOLIDATED` | Raw tape volume | Nets off fills reported since the previous update, carrying any un-netted remainder forward so each own share is netted exactly once |

`CONSOLIDATED` is an approximation: a fill reported to the engine before its print reaches
the tape is netted one interval early. It is bounded and self-correcting — the carry-forward
guarantees no own share is netted twice or dropped — but `AWAY` is the accurate setting
whenever the caller can identify its own prints.

**Getting this wrong is not a rounding error.** At $R = 0.15$ a run fed its own prints back
as away volume inflates the cumulative denominator by every share it trades, and the error
grows with the participation rate.

---

## 1. Validate the parent order at construction

`POVParentOrder.__post_init__` raises on:

| Condition | Why it cannot be tolerated |
|---|---|
| `target_rate` outside $(0,1)$ | $R = 1$ makes $\frac{R}{1-R}$ undefined; $R > 1$ makes it negative; $R \le 0$ never trades |
| `target_rate > max_rate` | Silently clamping executes at a rate nobody authorised, with no record |
| `min_slice_qty > max_slice_qty` | No slice can satisfy both bounds — the algorithm pauses forever with a healthy-looking status |
| `total_qty <= 0`, non-int quantities, empty symbol, side outside BUY/SELL | Meaningless order |
| non-finite rates | Propagates NaN into every downstream participation number |

Rates are validated, never coerced. A rate rewritten behind the caller's back is the
failure mode that produces an execution no one can account for afterwards.

---

## 2. Consume a volume update

`process_volume_update(market_interval_volume, last_price) -> (slice_qty, report)`

### 2.1 Reject bad ticks

A **negative** interval volume shrinks the cumulative denominator and inflates every
participation figure derived from it — a single corrected tick can turn a genuine 9%
participation into a reported 50%. Non-int volumes, non-finite prices and non-positive
prices are rejected on the same principle: resynchronise the feed, do not absorb it.

### 2.2 Short-circuit states

Evaluated in order, before any sizing:

| Order | State | Condition |
|---|---|---|
| 1 | `ENGINE_DISABLED` | `config.enabled` is false. No volume is accumulated — a disabled engine must not silently build a participation history it will act on when re-enabled. |
| 2 | `COMPLETED` | `filled_qty >= total_qty` |
| 3 | `AWAITING_FILLS` | Whole remainder is already working; nothing schedulable |
| 4 | `RATE_CAPPED` | Projected participation exceeds `max_rate` |

### 2.3 Cumulative target

$$Q_{\text{target}} = \left\lfloor \frac{R}{1-R} \times V_{\text{away, cum}} + \varepsilon \right\rfloor$$

The tolerance $\varepsilon = 10^{-9}$ is not cosmetic. At $R = 1/3$, IEEE-754 evaluates
$\frac{R}{1-R}$ as `0.49999999999999994`; without the tolerance, 2 away shares floor to 0
instead of 1, and the loss recurs at every exact-ratio boundary.

Flooring (not rounding) keeps the target rate an upper bound rather than something the
algorithm rounds its way past.

### 2.4 Deficit sizing

$$Q_{\text{slice}} = \max\Big(0,\ \min\big(Q_{\text{target}} - Q_{\text{filled}} - Q_{\text{working}},\ \text{MaxSliceQty},\ Q_{\text{total}} - Q_{\text{filled}} - Q_{\text{working}}\big)\Big)$$

Three things are load-bearing here:

1. **Cumulative, not per-interval.** A per-interval slice permanently abandons every share
   lost to flooring, to a paused interval, or to a child order that did not fill. With
   `min_slice_qty` above any single interval's target, a per-interval POV emits **zero
   forever** while reporting `VOLUME_PAUSED` — no error, no alert, no fills. The cumulative
   target makes the deficit recoverable: the shares accrue and are sent once they clear
   the minimum.
2. **Working quantity is subtracted.** Quantity already in the market is quantity the tape
   will see. Omit it and each update re-sends the same deficit, multiplying live exposure
   by the number of updates before the first fill report arrives.
3. **The parent bound uses the same subtraction**, so the engine cannot send more than the
   parent order's outstanding quantity even across several unresolved child orders.

### 2.5 Minimum-clip gate and the tail exception

```
if 0 < slice_qty < min_slice_qty and schedulable_remainder >= min_slice_qty:
    slice_qty = 0      # wait for volume to accrue
```

The second condition is the exception that matters. Once the schedulable remainder is
*itself* below `min_slice_qty` — an odd-lot tail — the gate is bypassed and the tail is
sent. Without it the parent order can never complete: the residual is permanently too
small to pass its own minimum.

Note this gate is about *scheduling*, not venue rules. `min_slice_qty` must be set at or
above the instrument's genuine minimum tradable quantity — see
`minimum-fill-size-and-lot-rounding-logic`.

---

## 3. Resolve every share that was sent

The returned `slice_qty` is **working**, not filled. It stays working until the caller
resolves it.

### 3.1 `record_fill(qty, price=None)`

Moves quantity from working to filled and adds it to the pending own-print netting used by
`CONSOLIDATED`. A fill larger than the working quantity is an **over-fill**: it is recorded
truthfully, logged at WARNING, and accumulated in `overfill_qty`.

Clamping an over-fill away would hide a real position mismatch — the one condition where
the engine's view and the broker's view have already diverged and a human needs to look.
The resulting projected participation then trips the `max_rate` backstop, which stops
further scheduling until the position is reconciled.

### 3.2 `record_unfilled(qty, reason)`

Releases quantity from a cancelled, expired or rejected child order back to the schedule.
It is re-offered on the next update against the same cumulative target, so a rejection
costs the order latency, not shares.

**Classify before releasing.** These are not the same event:

| Outcome | Correct handling |
|---|---|
| Explicit broker rejection (min-notional, tick size, buying power, halt) | `record_unfilled` with the reason; fix the cause before the quantity is re-offered, or it will be rejected again |
| Child order expired or cancelled unfilled | `record_unfilled` |
| Partial fill, then expiry | `record_fill(filled_part)` **then** `record_unfilled(remainder)` |
| **Request timed out — outcome unknown** | **Neither.** Reconcile against the broker first. |

The timeout row is the dangerous one. A lost response does not mean a lost order: the
broker may already have accepted it. Calling `record_unfilled` on a live order releases
quantity that is genuinely in the market, and the next slice re-sends it — a duplicate
execution that no participation cap in this engine can prevent, because the engine was
told the quantity was dead. Route every child order through
`order-placement-idempotency` and reconcile before releasing.

Releasing more than the working quantity raises: the engine's view has diverged from the
broker's and continuing would compound the error.

---

## 4. Monitor

$$\text{RealizedRate} = \frac{Q_{\text{filled}}}{V_{\text{away, cum}} + Q_{\text{filled}}}$$

Fills only. Working quantity has not printed and is not participation.

Because the scheduled curve is bounded by the cumulative target, realized participation is
bounded above by `target_rate` **by construction** — it cannot drift past the cap on its
own. `max_rate` is therefore a backstop for participation the schedule did not produce:
over-fills, or fills reported against this parent from another source. When projected
participation exceeds it, scheduling stops with `RATE_CAPPED` rather than continuing to
add quantity to a position that is already too large a share of the market.

Alert on, at minimum:

- `RATE_CAPPED` or a non-zero `overfill_qty` — reconcile immediately.
- A long run of `VOLUME_PAUSED` with `cum_target_qty` flat — volume has stopped; the order
  is not progressing and will not complete on its own.
- `working_qty` that stays non-zero across many updates — child orders are not being
  resolved, and the engine is holding quantity out of the schedule.
- Realized rate materially below `target_rate` late in the session — the order is behind,
  and the decision to accept an unfilled residual or switch to a scheduled algorithm is a
  human one.

---

## 5. What sits outside this engine

| Concern | Owner |
|---|---|
| Order placement, cancellation, idempotency keys | `order-placement-idempotency` |
| Rate limiting and broker throttles | `multi-broker-rate-limit-handling` |
| Exposure, capital, price collars, kill switch | `execution-algorithm-kill-switch-integration` |
| Halt / auction state handling | `execution-algo-behavior-under-halted-instrument` |
| Benchmark measurement and TCA | `transaction-cost-analysis-tca-integration` |
| Cost of the unfilled residual | `implementation-shortfall-minimization` |
| Multi-session budgets for orders larger than one day's participation | `multi-day-execution-schedules-for-very-large-orders` |

The engine is stateful and **not thread-safe**. Serialise calls per parent order, or hold
one engine instance per parent order per thread.

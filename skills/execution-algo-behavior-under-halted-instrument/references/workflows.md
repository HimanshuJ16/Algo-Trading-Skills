# Workflows — execution-algo-behavior-under-halted-instrument

Reference procedure for one parent algo instance across a non-continuous
trading episode. Every step takes an explicit `event_ts`; the engine reads no
wall clock, so replay and live sessions produce identical decisions.

## 1. Status classification (exhaustive, fail safe)

1. Normalise the venue's native status code onto the engine's token set.
   Map it explicitly — do **not** prefix-match on `"HALTED"`.
2. Dispatch on the token:

| Token class | Slicing | Marketable children | Cancels | Parent state |
|---|---|---|---|---|
| `TRADING_CONTINUOUS` | on | permitted | accepted | `RUNNING` |
| `LIMIT_STATE`, `STRADDLE_STATE` | on (passive only) | **rejected at the band** | accepted | unchanged |
| `HALTED_*`, `PAUSED_VELOCITY_LOGIC` | off | — | accepted | `PAUSED_HALTED` |
| `PRE_OPEN_NO_CANCEL`, `VOLATILITY_FREEZE` | off | — | **refused** | `PAUSED_HALTED` |
| `AUCTION_REOPENING`, `PRE_OPEN`, `AUCTION_CLOSING` | off | — | accepted | `REBALANCING_POST_RESUMPTION` |
| `CLOSED`, `CLOSE_FINAL` | off | — | **refused** | unchanged |
| unrecognised | off | — | none issued | unchanged, escalate |

3. An unrecognised token suspends slicing and raises an operator alert. It
   deliberately issues no cancels: acting on a malformed or newly-introduced
   feed value is itself an unrequested trading action.

## 2. Halt reaction

1. Stamp `halt_started_ts` **once**. A duplicate halt message must not restamp
   it — the measured outage drives the recovered horizon in step 5.
2. Check `cancel_permitted` before doing anything else. In a no-cancel phase
   there is no cancel to send; the live orders are committed exposure into the
   auction print and the correct action is escalation, not retry.
3. For each child in `RESTING`, issue a cancel request and move it to
   `PENDING_CANCEL`. Skip children already `PENDING_CANCEL` — re-requesting
   draws a reject and inflates the order-to-trade ratio.
4. Freeze slice dispatch. Record `cancel_requests_issued` and
   `orders_still_live_count` separately; only the second is exposure.

## 3. Acknowledgement reconciliation

Feed every venue response back through `apply_cancel_ack`:

| Acknowledgement | Effect | Still live? |
|---|---|---|
| `CANCELLED` | order retired; any partial fill that printed first is added to `executed_qty` | no |
| `FILLED` | order executed before the cancel landed; `executed_qty` increases | no |
| `CANCEL_REJECTED` | order returns to `RESTING` | **yes** |

Duplicate acknowledgements and fills exceeding the child's unfilled quantity
are rejected rather than absorbed. Do not compute remaining quantity,
participation rate or position from intent — only from acknowledged state.

## 4. Auction phase

1. Transition to `REBALANCING_POST_RESUMPTION` with slicing **off**. There is
   no continuous matching during price discovery.
2. Retry cancels for any child that a reject returned to `RESTING`, if the
   phase accepts them.
3. Keep the halt clock running — the instrument is not continuously tradable
   until `TRADING_CONTINUOUS`, so the reopening auction counts as outage.
4. Auction participation itself (fair-value pricing, LOC/imbalance-only order
   types) is out of scope here — see `black-swan-playbook-for-halted-markets`.

## 5. Resumption and re-benchmarking

1. Compute `halt_duration = resume_ts − halt_started_ts`.
2. Give back the lost horizon, clamped at the hard deadline:
   `new_end = min(schedule_end + halt_duration, hard_end_ts)`.
   The session close is not extendable.
3. Compare rates:
   - original: `total_target_qty / (schedule_end − schedule_start)`
   - required: `remaining_qty / (new_end − resume_ts)`
4. **If `required > max_rate_multiple × original`, do not resume.** Hold in
   `REBALANCING_POST_RESUMPTION` with slicing off and `rebenchmark_breach=True`,
   and escalate. Working a large residual into a thin reopening is how an algo
   triggers a *secondary* pause. Do not extend `schedule_end_ts` while tripped —
   leaving it fixed is what makes repeated resumption messages yield a stable
   required rate instead of a drifting one.
5. Otherwise commit `schedule_end_ts = new_end`, clear the halt clock, and
   return to `RUNNING`.
6. If no schedule was supplied (volume-driven parents), report
   `rebenchmark_applied=False` and resume — the guard did not run, and the
   parent algo owns rate limiting the residual.
7. Before dispatching the first post-halt slice, re-check
   `orders_still_live_count`. Any unconfirmed child is working exposure that
   the new schedule does not account for.

## 6. Audit

Emit `AlgoHaltAuditReport` on every transition, including no-ops. The record
carries the state transition, cancel request/confirmation/live counts, the
re-benchmark inputs and outputs, and the `reconciliation_breach` and
`status_recognised` flags for post-incident review.

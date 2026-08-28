# Workflows for Queue Position Modeling for Passive Orders

Deep procedure reference. The short form is in `SKILL.md`; the sourcing for every
factual claim below is in `references/standards.md`.

## 0. Gate: is a FIFO queue model applicable at all?

Before writing any code against this skill, resolve for **the specific product**:

| Question | Where to look | If the answer is wrong |
|---|---|---|
| Is the matching algorithm strict price-time? | Venue's published matching-algorithm assignment per product group (for CME, the Client Systems Wiki) | Pro-rata and threshold pro-rata allocate by size — abandon the queue model |
| Is it a split algorithm? | Same | Only the FIFO fraction is modellable (CME's Configurable K is currently 40% FIFO for grain and oilseed futures and spreads) |
| Can a TOP order jump ahead? | Same, plus `TOP Min` / `TOP Percentage` for the product | A later-arriving TOP order executes before you and never appears in the level tally |
| Do displayed orders outrank hidden ones at the same price? | Venue rulebook (Nasdaq: Equity 4, Rule 4757) | If hidden interest can outrank displayed, L2-derived volume ahead is a systematic underestimate |

This gate is not optional and it is not a one-off: matching-algorithm
assignments change, and a strategy that silently follows a product roll onto a
differently-matched contract inherits an invalid model.

## 1. Snapshot the level at order entry

Capture from **one** book image:

- `initial_queue_ahead` — displayed volume at your limit price with strictly
  better time priority at the instant your order is acknowledged. Use the
  exchange acknowledgement timestamp, not the send timestamp: everything that
  arrived during your outbound latency is ahead of you.
- `total_level_volume` — total volume at that price *including* your own
  quantity, from the same image.
- `our_quantity`, `price`, `side`, `order_id`.

The engine enforces `total_level_volume >= initial_queue_ahead + our_quantity`.
A violation means the two numbers came from different moments; treat it as a
feed synchronisation failure, not a market state, and re-snapshot.

**If your order is a reserve/iceberg order**, `our_quantity` should be the
quantity that actually holds queue priority at this price. Replenished tranches
generally receive a new timestamp and rejoin at the back — model them as separate
trackers, not as one large resting order.

## 2. Accumulate level activity — venue-local, price-specific, cumulative

Maintain two running totals per resting order, both measured **since the order's
acknowledgement**:

- `accumulated_fills` — volume executed at exactly `price`, on **your** venue.
- `accumulated_cancellations` — volume cancelled (or amended down) at exactly
  `price`, on your venue.

Three ways this goes wrong:

1. **Consolidated-tape volume.** Roughly half of US equity share volume prints
   off-exchange through a FINRA TRF and never touches your queue; prints from
   other exchanges at the same price do not either. Using tape volume drains the
   modelled queue faster than the real one.
2. **Increments instead of cumulative totals.** `update_queue_position` restarts
   from the entry-time snapshot every call. Handing it a per-tick increment
   understates progress on every call after the first, silently.
3. **Amendments.** A quantity decrease at your price is a partial cancellation
   and belongs in `accumulated_cancellations`. A price change is a cancel at the
   old level plus a new order at the new one. An amendment *up* generally loses
   priority and rejoins the back — it does not increase volume ahead of you.

## 3. Update volume ahead

```
queue_after_fills = max(0, initial_queue_ahead - accumulated_fills)

other_resting     = total_level_volume - our_quantity
uniform_share     = min(1, queue_after_fills / other_resting)     # 0 if other_resting <= 0
cancels_ahead     = min(queue_after_fills,
                        accumulated_cancellations * uniform_share * alpha)

current_queue_ahead = max(0, queue_after_fills - cancels_ahead)
```

Order matters: fills are credited before the share is computed, so once the queue
ahead is empty no cancellation can be credited to it. The `min` against
`queue_after_fills` is what keeps a large cancellation total from driving volume
ahead negative.

`alpha` (`Config.cancellation_share_alpha`) is a haircut on the
uniform-cancellation assumption, in $[0, 1]$:

| `alpha` | Meaning | When |
|---|---|---|
| `1.0` | Pure uniform cancellation — the tractable literature assumption | Baseline for comparison against published models |
| `0.5` | Module default; uncalibrated pessimistic prior | Starting point only |
| `0.0` | Credit no cancellations ahead at all | Most conservative bound; useful as a worst case |

Empirically cancellations skew toward the back of the queue, so uniform
allocation over-credits cancellations ahead of you. Calibration procedure: from
reconstructed L3 message logs, for each of your historical resting orders,
compare the true count of cancellations that occurred ahead of it against
$V_{\text{cancel}} \cdot Q_{\text{ahead}} / (Q_{\text{total}} - q_{\text{our}})$,
and take $\alpha$ as the ratio of realised to predicted, per venue and liquidity
tier.

## 4. Rank

```
orders_ahead   = ceil(current_queue_ahead / average_order_size)
estimated_rank = orders_ahead + 1
```

Ceiling, not floor: an order ahead of you that is half consumed still has to
finish before you start. Flooring reports a rank one better than reality at
every non-integral queue depth.

`average_order_size` is a per-instrument, per-venue, per-time-of-day quantity.
The `100.0` default is a placeholder in the module's own units. Estimate it as
mean displayed size per order at the touch from L3 data; rank scales inversely
with it.

## 5. Fill probability

```
mu                    = (fill_rate_per_sec * horizon_sec) / average_trade_size
trades_for_full_fill  = ceil((current_queue_ahead + our_quantity) / average_trade_size)
trades_for_any_fill   = floor(current_queue_ahead / average_trade_size) + 1

P_full    = P(N >= trades_for_full_fill  | N ~ Poisson(mu))
P_partial = P(N >= trades_for_any_fill   | N ~ Poisson(mu))
```

`P_partial >= P_full` always. The gap between them is the size dimension of the
decision: a wide gap means the order is likely to be *touched* but unlikely to
be *completed* within the horizon, which argues for reducing displayed size
rather than repricing.

Why not the deterministic ratio $\min(1, \lambda \Delta t / (Q_{\text{ahead}} +
q_{\text{our}}))$: it carries no dispersion and reaches certainty as soon as
expected volume merely equals required volume. Worked comparison, at
$\bar{S}_{\text{trade}} = 100$, $\lambda \Delta t = 250$:

| Situation | Ratio | Poisson |
|---|---|---|
| Front of queue, 100 to fill ($\mu = 2.5$, $k = 1$) | **1.000** | 0.918 |
| 474 ahead, 100 ours → 574 to fill ($\mu = 2.5$, $k = 6$) | 0.436 | **0.042** |

The ratio is not conservative in one direction; it is wrong in both, and most
wrong exactly where the decision is hardest.

The Poisson model's assumptions — independent arrivals, constant rate, constant
average trade size, no queue jumping, no hidden or TOP interception — all bias
the result **upward**. Read the output as an upper bound. A reported `1.0` means
"at least $1 - 10^{-16}$ under these assumptions", not a guarantee.

Estimate `fill_rate_per_sec` from executed volume at the level over a window
matching the intended horizon, conditioned on the same session period. A rate
estimated over a full session and applied to a 5-second horizon at the open is a
different distribution.

## 6. Act on the report

| Report state | Interpretation | Typical action |
|---|---|---|
| `FRONT_OF_QUEUE`, `P_full` high | Next execution at this price reaches us | Hold; monitor toxicity, because this is also the most adversely-selected position |
| `QUEUE_PRIORITY_TRACKING`, rank improving | Queue is draining as modelled | Hold |
| Rank static while the level's total volume grows | Volume is joining behind us and nothing is leaving ahead | Hold — priority is improving in relative terms even if $Q_{\text{ahead}}$ is flat |
| `P_full` low and `P_partial` high | Likely touched, unlikely completed | Reduce displayed size rather than reprice |
| Rank deteriorating, or book imbalance turning against the side | Priority is not being earned and the price may move away | Consider cancel/reprice — `post-only-limit-repricing-under-fast-markets` |
| `QueuePositionValidationError` raised | Input integrity failure | Do **not** substitute a default and continue. Stop estimating for that order until the feed is trusted again |

**Do not reprice on queue rank alone.** Cancelling and re-joining resets priority
to the back of the queue at the new price, and a repricing loop that fires on
every rank deterioration will burn an order-rate budget while never earning
priority anywhere. Cross-check with
`order-to-trade-ratio-fee-penalty-avoidance`.

## 7. Validation and failure handling

Every numeric input is validated for type, finiteness and sign before any
arithmetic. Clamping is not validation: `max(0.0, nan)` returns `0.0` and
`min(1.0, nan)` returns `1.0` in CPython, so an unvalidated `NaN` emerges as
front-of-queue, rank 1, fill probability `1.0` — the most aggressive output the
model can produce, from corrupt data.

`QueuePositionValidationError` is raised rather than returning a degraded report,
because there is no safe default for "how much volume is ahead of me". A caller
that catches it must stop acting on queue position for that order, not fall back
to a guess.

## 8. Audit trail

`QueuePositionReport.audit_notes` records the order, side, price, status,
entry-time and current volume ahead, the fills and the *credited* portion of
cancellations against the total observed, the rank, and both probabilities. The
credited-versus-observed cancellation split is the field that makes an $\alpha$
calibration reviewable after the fact — without it, a queue estimate cannot be
reconciled against the message log that produced it.

## Related workflow references

- `historical-order-book-reconstruction-from-message-logs` — the L3 data needed to
  calibrate $\alpha$, $\bar{S}_{\text{order}}$ and $\bar{S}_{\text{trade}}$.
- `adverse-selection-measurement-for-passive-orders` — whether the priority you
  earned was worth having.
- `execution-realistic-simulation` — turning a fill probability into simulated
  fills, which this skill deliberately does not do.
- `exchange-matching-engine-behavior-under-load` — queueing delay at the engine,
  a separate queue from the one modelled here.

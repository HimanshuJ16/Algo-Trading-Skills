---
name: queue-position-modeling-for-passive-orders
description: >-
  Use when resting liquidity in a strict price-time book and you need to know how much
  volume is still ahead of your order and what that is worth, tracking fills and cancels
  as they consume the level.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: queue-position, fifo-order-book, price-time-priority, passive-execution, fill-probability, poisson-queue, adverse-selection, microstructure
  brokers_frameworks: "CME Globex Matching Algorithms; Nasdaq Equity 4 Rule 4757; Generic L2/L3 Depth Feed; Python Dataclasses; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy rests passive liquidity in a **strict price-time
(FIFO)** limit order book and needs to answer one question: *how much volume is
still ahead of my order, and what is that worth?* Market making, passive slices
of a TWAP/VWAP parent, peg orders and post-only repricing all depend on it — the
value of a resting order is almost entirely its queue priority, and priority is
not observable, only estimable.

The engine maintains three things from an entry-time snapshot plus cumulative
level activity:

- **Volume ahead** $Q_{\text{ahead}}$ — decremented by executions in full, and
  by cancellations only in proportion to the share assumed to sit ahead of us.
- **Queue rank** — $Q_{\text{ahead}}$ divided by a calibrated average order size,
  rounded **up**, plus one. A partially-consumed order in front still blocks us.
- **Fill probability** — the probability that enough trades arrive inside a
  forward horizon to clear the queue and then our own quantity, under an
  explicit Poisson trade-count model. Reported both for a **complete** fill and
  for **any** fill.

## When NOT to Use

- **On a book that is not strict price-time.** The whole model rests on "volume
  ahead of me executes before I do". CME Globex runs several allocation
  algorithms other than FIFO — Pro-Rata, Split FIFO/Pro-Rata (Configurable K,
  currently set at 40% FIFO for grain and oilseed futures and spreads), Threshold
  Pro-Rata and Allocation. Under pro-rata your share of an incoming aggressor
  depends on your *size*, not your arrival time, and a volume-ahead number
  carries no information about it. Confirm the matching algorithm for the
  specific product before using this at all.
- **On a level where a TOP order can jump ahead of you.** CME's Allocation and
  Configurable algorithms grant priority to the first order that betters the
  market, and "orders having TOP priority match first regardless of size". A TOP
  order arriving *after* you still executes before you, so it increases your
  effective volume ahead without appearing in any queue arithmetic here.
- **As an order book.** This consumes an already-maintained book and a running
  tally of level activity. Book construction, depth mutation and L3 order-ID
  bookkeeping belong to `order-book-depth-processing-l2-l3`; reconstruction from
  message logs to `historical-order-book-reconstruction-from-message-logs`.
- **As a backtest fill engine.** A fill probability is not a fill. Wiring this
  output into a backtest as if it were an execution decision reintroduces exactly
  the optimism it was written to remove; use `execution-realistic-simulation`.
- **As a measure of whether the fills you got were any good.** Queue priority and
  adverse selection are different questions — a front-of-queue order fills
  fastest *and* is selected against hardest. Measure the second with
  `adverse-selection-measurement-for-passive-orders`.
- **With uncalibrated `average_order_size` / `average_trade_size`.** The `100.0`
  defaults are placeholders in the module's own quantity units. They are not
  findings about any instrument, and rank and fill probability both scale with
  them directly.
- **Across threads on one tracker.** `PassiveOrderTracker` is a plain mutable
  dataclass and the engine takes no lock. The engine itself holds no per-order
  state, so shard trackers by order and the calls are independent.

## Prerequisites

- An entry-time level snapshot per order: `order_id`, `side` (`'BUY'`/`'SELL'`),
  `price`, `our_quantity`, `initial_queue_ahead`, `total_level_volume`.
  `total_level_volume` **includes** our own resting quantity, so it can never be
  below `initial_queue_ahead + our_quantity`; the engine rejects a snapshot that
  claims otherwise.
- **Venue-local** cumulative executed and cancelled volume at that exact price,
  measured since the order joined the queue. Not consolidated-tape volume — see
  the pitfalls.
- Calibrated `Config`: `cancellation_share_alpha` (haircut in $[0, 1]$ on the
  uniform-cancellation assumption, default `0.5`), `average_order_size`,
  `average_trade_size`, `front_of_queue_tolerance`.
- Python 3.10+. Standard library only.

## Workflow

1. **Confirm the matching algorithm before anything else.** If the product is
   pro-rata, split, threshold pro-rata or allocation-based, or if TOP priority
   applies, stop here — the rest of this workflow produces a number with no
   meaning on that book. This is a per-product property, not a per-venue one:
   the same exchange runs different algorithms across its product groups.

2. **Snapshot the level at order entry, and validate it.** Record
   $Q_{\text{ahead}}^{(0)}$ and $Q_{\text{total}}$ together, from the same book
   image. A snapshot assembled from two moments produces a
   `total_level_volume` below $Q_{\text{ahead}} + q_{\text{our}}$ and is
   rejected — that inconsistency is a synchronisation failure, not a market
   state.

3. **Subtract executions in full.**
   $Q_{\text{ahead}} \leftarrow \max(0, Q_{\text{ahead}}^{(0)} - V_{\text{fill}})$.
   - **Decision point — count only executions on your own venue at your own
     price.** In US equities roughly half of share volume prints off-exchange
     through a FINRA TRF and never touches your queue (Cboe reported the
     off-exchange share passing 50% for the first time in January 2025, and it
     moves month to month). Feeding consolidated-tape volume in here drains the
     modelled queue far faster than the real one, so the model reports
     front-of-queue while the order is still buried.

4. **Credit cancellations only in proportion to the share assumed ahead of you,
   then haircut it.**
   $$V_{\text{cancel}}^{\text{ahead}} = \min\!\left(Q_{\text{ahead}},\;
   V_{\text{cancel}} \cdot \frac{Q_{\text{ahead}}}{Q_{\text{total}} - q_{\text{our}}}
   \cdot \alpha\right)$$
   - **Decision point — the denominator excludes your own quantity.** A
     cancellation can only come from *another* participant's resting volume.
     Dividing by the full $Q_{\text{total}}$ dilutes the share by your own size
     and credits too few cancellations ahead.
   - **Decision point — $\alpha < 1$ is a deliberate pessimistic haircut, not a
     tuning knob.** The uniform-cancellation assumption is known to be
     optimistic: queue position is a strong determinant of cancellation, and
     orders later in the queue are cancelled more often (Dahlström & Nordén,
     *Financial Review*, 2024). $\alpha = 1$ is the pure uniform model;
     $\alpha = 0.5$ is the module default and an uncalibrated prior. Calibrate it
     against reconstructed L3 data before trusting either.

5. **Round the order count up, not down.** $\text{Rank} = \lceil Q_{\text{ahead}}
   / \bar{S}_{\text{order}} \rceil + 1$. An order ahead of you that is half
   consumed still has to finish before you start; flooring the count reports a
   rank one better than reality at every non-integral queue depth.

6. **Price the fill as a probability, not as a coverage ratio.** With expected
   executed volume $\lambda \Delta t$ and average trade size
   $\bar{S}_{\text{trade}}$, the expected trade count is
   $\mu = \lambda \Delta t / \bar{S}_{\text{trade}}$ and
   $$P_{\text{full}} = \Pr\!\left(N \ge \left\lceil \frac{Q_{\text{ahead}} + q_{\text{our}}}{\bar{S}_{\text{trade}}} \right\rceil\right), \quad N \sim \text{Poisson}(\mu).$$
   - **Decision point — never report $\lambda \Delta t / (Q_{\text{ahead}} +
     q_{\text{our}})$ clamped to one.** That ratio hits `1.0` whenever expected
     volume merely equals required volume, asserting a certain fill from a
     coin-flip situation. Front of queue with 100 shares and 250 expected: the
     ratio says 100%, the Poisson model says 91.8%. Deeper in the queue the gap
     is an order of magnitude — 43.6% versus 4.2% at 574 shares required.
   - $P_{\text{partial}}$ (at least one share) is reported alongside and is
     always $\ge P_{\text{full}}$. Size decisions need the gap between them.

7. **Refuse to estimate from unvalidated numbers.** Every input is checked for
   type, finiteness and sign before any arithmetic, and a bad one raises
   `QueuePositionValidationError` rather than producing a report.
   - **Decision point — clamping is not validation.** `max(0.0, float('nan'))`
     returns `0.0` in CPython, so a `NaN` volume-ahead run through the clamp
     emerges as front-of-queue, rank 1, and — through `min(1.0, nan)` — a fill
     probability of `1.0`. A corrupt feed value becomes the single most
     aggressive signal the model can emit.

8. **Read the report, and remember the call is cumulative.**
   `update_queue_position` recomputes from the entry-time snapshot given
   **cumulative** volumes since entry. It accumulates nothing across calls.
   Passing per-tick increments understates queue progress on every call after
   the first, and does so silently.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying a FIFO queue model to a pro-rata product.** The arithmetic runs
  happily and means nothing. Under CME's Split FIFO/Pro-Rata only the FIFO
  portion of the aggressing quantity is allocated by time; the rest goes by size
  share. Check the product's matching algorithm, not the exchange's reputation.
- **Draining the queue with consolidated-tape volume.** Off-exchange prints, and
  prints from other exchanges at the same price, do not consume your venue's
  queue. This is the error that most reliably produces a confident,
  wrong `FRONT_OF_QUEUE`.
- **Treating a clamp as a guard.** `max(0.0, nan) == 0.0` and
  `min(1.0, nan) == 1.0`. Both defaults fail *open*, toward maximum aggression.
  Validate finiteness before the arithmetic, not after.
- **Reporting a clamped volume ratio as a probability.** It saturates at 1.0 for
  ordinary inputs and carries no notion of dispersion in arrival volume. Fill
  probability under state-dependent order flow is a genuinely non-trivial
  quantity (Lokin & Yu, arXiv:2403.02572); a Poisson trade count is a stated
  simplification of it, a clamped ratio is not a model at all.
- **Assuming cancellations are uniform across the queue.** They are not — later
  queue positions cancel more. Uniform allocation therefore over-credits
  cancellations ahead of you and flatters your priority.
- **Dividing the cancellation share by total level volume including your own
  order.** Your own quantity is not a candidate for someone else's cancellation.
- **Flooring the queue rank.** A part-filled order ahead still blocks you.
- **Forgetting that hidden and reserve liquidity is not in your snapshot.** On
  Nasdaq, non-displayed interest — including the reserve portion of an order with
  Reserve Size — ranks behind equally-priced displayed interest regardless of
  timestamp (Equity 4, Rule 4757), so it does not sit ahead of a displayed order
  at the same price. It does absorb aggressing volume at *better* prices, which
  changes the rate at which flow reaches your level. Iceberg replenishment is a
  separate matter: see `iceberg-order-simulation-and-detection`.
- **Passing increments to a cumulative API.** `accumulated_fills` means "since
  the order joined the queue". Per-tick increments produce a queue estimate that
  is wrong on every call after the first and never raises.
- **Reading a `1.0` fill probability as a guarantee.** It means "at least
  $1 - 10^{-16}$ *under this model's assumptions*" — assumptions that exclude
  queue jumping, hidden liquidity, TOP priority and your own cancellation. It is
  an upper bound, not a promise.
- **Treating front-of-queue as unambiguously good.** It is the position that
  fills first *and* the position that fills first when someone informed is about
  to move the price. Pair the queue signal with a markout measurement.

## Verification

- **Fail-closed inputs**: a `NaN`, `inf`, `-inf`, `True` or non-numeric value in
  any of `price`, `our_quantity`, `initial_queue_ahead`, `total_level_volume`,
  `accumulated_fills`, `accumulated_cancellations`, `time_horizon_sec` or
  `historical_fill_rate_per_sec` must raise `QueuePositionValidationError`. In
  particular `initial_queue_ahead = NaN` must **not** return `FRONT_OF_QUEUE`.
- **Sign and domain**: negative fills or cancellations, non-positive quantity,
  price or horizon, a `side` outside `{'BUY', 'SELL'}`, a blank `order_id`, and a
  `total_level_volume` below `initial_queue_ahead + our_quantity` must all be
  rejected. `' buy '` must normalise to `'BUY'`.
- **Known-value queue case**: 1,000 ahead, 100 ours, 2,000 at the level, 500
  filled and 200 cancelled gives
  $500 - 200 \cdot \tfrac{500}{1900} \cdot 0.5 = 473.6842\ldots$ ahead — *not*
  the 475.0 produced by dividing through the full 2,000 — and rank #6.
- **Poisson known values**: $P(N \ge 1 \mid 2.5) = 1 - e^{-2.5}$;
  $P(N \ge 2 \mid 1) = 1 - 2/e$; $P(N \ge 3 \mid 2) = 1 - 5e^{-2}$. Zero
  intensity gives exactly `0.0`; $k \le 0$ gives `1.0`.
- **No certainty at the front**: 0 ahead, 100 ours, 50/s over 5 s must report
  $1 - e^{-2.5} = 0.9179\ldots$, strictly below `1.0`.
- **Numerical guard**: $\mu = 1000$ at $k = 1000$ must return roughly one half,
  not `1.0` — `math.exp(-mu)` underflows near $\mu = 745$, so the exact
  summation is replaced by a normal approximation above $\mu = 500$.
- **Invariants**: $P_{\text{partial}} \ge P_{\text{full}}$ across queue depths
  and order sizes; both in $[0, 1]$; fill probability monotone decreasing in
  volume ahead and increasing in horizon; credited cancellations never exceed
  the volume actually ahead; volume ahead never negative.
- **Report agreement**: `current_queue_ahead <= front_of_queue_tolerance` must
  match `is_front_of_queue` and `status` on every result — 0.04 units ahead must
  not be reported as `0.0` next to `is_front_of_queue = False`.
- Run `python -m unittest discover -s skills/queue-position-modeling-for-passive-orders/scripts`
  and confirm 57/57 pass.

## Related Skills

- `adverse-selection-measurement-for-passive-orders`
- `post-only-limit-repricing-under-fast-markets`
- `peg-order-types-for-passive-execution`
- `order-book-imbalance-signal-pipeline`
- `order-book-depth-processing-l2-l3`
- `historical-order-book-reconstruction-from-message-logs`
- `exchange-matching-engine-behavior-under-load`
- `execution-realistic-simulation`
- `iceberg-order-simulation-and-detection`
- `post-only-and-maker-taker-fee-optimization`

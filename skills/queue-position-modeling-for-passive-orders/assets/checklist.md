# Pre-Flight Checklist — Queue Position Modeling for Passive Orders

## Applicability gate
- [ ] Is the matching algorithm for **this specific product** strict price-time (FIFO), verified against the venue's published assignment rather than assumed?
- [ ] If it is a split algorithm (e.g. CME's Configurable K, currently 40% FIFO for grain and oilseed futures and spreads), is the model applied only to the FIFO fraction?
- [ ] Has pro-rata / threshold pro-rata / allocation matching been ruled out — where allocation is by size, not arrival time, and volume ahead carries no information?
- [ ] Can a later-arriving TOP order jump ahead of us on this product, and if so is that acknowledged as unmodelled volume ahead?
- [ ] Is the gate re-checked on contract roll, so a strategy cannot silently follow a roll onto a differently-matched product?

## Level snapshot
- [ ] Are `initial_queue_ahead` and `total_level_volume` taken from **one** book image, at the exchange acknowledgement timestamp rather than the send timestamp?
- [ ] Does `total_level_volume` include our own quantity, so that `total_level_volume >= initial_queue_ahead + our_quantity` holds?
- [ ] Is a snapshot that violates that inequality treated as a feed synchronisation failure and re-taken, rather than patched?
- [ ] For reserve/iceberg orders, is each replenished tranche tracked separately, given it rejoins the queue with a new timestamp?

## Level activity inputs
- [ ] Is executed volume **venue-local and price-specific** — not consolidated-tape volume, roughly half of which prints off-exchange and never touches our queue?
- [ ] Are `accumulated_fills` and `accumulated_cancellations` **cumulative since order acknowledgement**, never per-tick increments (which understate progress on every call after the first, silently)?
- [ ] Are quantity-decrease amendments counted as cancellations, and price amendments as a cancel-plus-new-order?
- [ ] Are amendments *up* excluded from volume ahead, given they lose priority and rejoin the back?

## Calibration — none of the defaults are findings
- [ ] Was `cancellation_share_alpha` calibrated per venue and instrument, rather than left at the uncalibrated `0.5` prior?
- [ ] Is it understood that `alpha = 1.0` is the pure uniform-cancellation model, and that uniform allocation is **optimistic** because cancellations skew toward the back of the queue?
- [ ] Was `average_order_size` estimated from L3 data for this instrument, given queue rank scales inversely with it?
- [ ] Was `average_trade_size` estimated for this instrument and session period, given it sets the Poisson trade-count scale?
- [ ] Is `historical_fill_rate_per_sec` estimated over a window matching the intended horizon and the same session period — not a full-session average applied to a 5-second horizon at the open?

## Queue arithmetic
- [ ] Are executions subtracted from volume ahead in full, and cancellations only in proportion to the assumed ahead-share?
- [ ] Does the cancellation share divide by `total_level_volume - our_quantity`, excluding our own order, which is not a candidate for someone else's cancellation?
- [ ] Are fills credited **before** the share is computed, so no cancellation is credited to an already-empty queue?
- [ ] Are credited cancellations capped at the volume actually ahead, so volume ahead can never go negative?
- [ ] Does the queue rank round the order count **up** — a part-consumed order ahead still blocks us?

## Fill probability
- [ ] Is the reported figure a probability from a stated stochastic model, not a clamped `expected volume / required volume` ratio (which returns 1.0 whenever expected merely reaches required)?
- [ ] Is a front-of-queue order reported as **less than certain** to fill?
- [ ] Are both complete-fill and partial-fill probabilities used, with a wide gap read as "likely touched, unlikely completed" — a size decision, not a repricing decision?
- [ ] Is the probability read as an **upper bound**, given that every Poisson assumption (independent arrivals, constant rate and size, no queue jumping, no hidden or TOP interception) biases it upward?
- [ ] Is a reported `1.0` understood as "at least $1 - 10^{-16}$ under the model", not a guarantee?

## Input integrity
- [ ] Are non-finite (`NaN`, `inf`) and non-numeric inputs **rejected before any arithmetic**, given that `max(0.0, nan)` returns `0.0` and `min(1.0, nan)` returns `1.0` — a `NaN` fails *open*, reporting front-of-queue with a certain fill?
- [ ] Are `bool` values rejected, rather than passing as `1.0` via the `int` subclass?
- [ ] Are negative fills and cancellations rejected, rather than growing volume ahead past the total volume at the level?
- [ ] Is `side` constrained to `{'BUY', 'SELL'}`, rather than any string being upper-cased and reported as a side?
- [ ] Does a `QueuePositionValidationError` stop queue-based decisions for that order, rather than triggering a fallback guess?
- [ ] Is `cancellation_share_alpha` inside $[0, 1]$ and are the average sizes strictly positive, checked at construction?

## Report consistency and action
- [ ] Does `current_queue_ahead <= front_of_queue_tolerance` agree with `is_front_of_queue` and `status` on every result — no rounded figure reported next to a flag computed from the raw one?
- [ ] Is front-of-queue understood as "the next execution at this price reaches us", not as a guaranteed fill?
- [ ] Is front-of-queue paired with a markout measurement, given it is also the most adversely-selected position?
- [ ] Is repricing driven by more than queue rank alone, given that cancel-and-rejoin resets priority to the back and a rank-triggered loop burns order-rate budget without earning priority anywhere?
- [ ] Are the credited-versus-observed cancellation figures in `audit_notes` retained, so an `alpha` calibration can be reconciled against the message log afterwards?

## Testing
- [ ] `python -m unittest discover -s skills/queue-position-modeling-for-passive-orders/scripts` — 57/57 pass.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

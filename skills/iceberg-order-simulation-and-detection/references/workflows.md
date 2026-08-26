# Workflows for Iceberg Order Detection

The screen runs per price level. Every step below exists to keep one specific false
positive or false negative out of the output; see `standards.md` for sources.

## 1. Canonicalize the level key

Bin the price to integer ticks (`round(price / tick_size)`) before it is used as a
tracker key. Without `tick_size`, round to a fixed high precision. Raw floats are not
safe keys: `0.1 + 0.2 != 0.3`, so a feed that computes a price and one that parses it
produce unequal values, splitting one economic level into two half-populated trackers
that each stay below threshold.

Reject non-finite prices. A `NaN` key is never equal to itself, so it spawns an
unreachable tracker on every message. Do **not** reject negative prices — CME WTI
settled below zero on 2020-04-20, and calendar and option spreads quote negative
routinely.

## 2. Guard the ingest path

- **Duplicate prints**: ignore a `trade_id` already accumulated. Reconnects and
  snapshot+delta recovery redeliver executions; a redelivered print lands directly in
  the hidden-size estimate. Keep the seen-ID window bounded (FIFO).
- **Out-of-order depth**: drop a snapshot whose timestamp precedes the last one
  processed for that level. Applying it shows depth "increasing" back to its earlier
  value and books a refill that never happened.
- **Out-of-order trades**: still accumulate them — the execution really happened — but
  log it. Volume is order-independent; refill counting is not.

## 3. Accumulate same-side volume only

A SELL aggressor lifts resting bids; a BUY aggressor lifts resting asks.

| Tracked side | Aggressor | Destination |
|---|---|---|
| BID | SELL | `cumulative_traded` ($V_{\text{cum}}$) |
| BID | BUY | `contra_traded` (diagnostic, scored as a penalty) |
| ASK | BUY | `cumulative_traded` |
| ASK | SELL | `contra_traded` |

Contra-side volume did not come out of the resting order being measured. Folding it
into $V_{\text{cum}}$ inflates $\hat{Q}_{\text{hidden}}$ by the entire print, and
because a price level flips between bid and ask across a session this is a routine
event, not an edge case.

## 4. Count refills and record peaks

Count a refill when displayed depth increases at a level that has already traded on
the tracked side, and append the size it refilled *to*. Peak sizes are the
discriminating evidence: a venue-held iceberg replenishes to a repeatable peak, while
organic replenishment by unrelated participants arrives in arbitrary sizes.

Nasdaq **Random Reserve** randomizes the display size, so peak consistency modulates
the score and must never gate detection.

## 5. Re-baseline when the resting order is gone

Reset $Q_0$, $V_{\text{cum}}$, contra volume and refill count when either holds:

- **The level flipped sides** (BID ↔ ASK). Different resting order entirely.
- **The level sat empty past `level_reset_dwell_nanos`.** Venue refreshes are
  immediate, so a momentary zero between refills must still count as a refill; a level
  that stays empty and then returns is a new order.

Without this, a level revisited hours later is scored against a stale morning baseline
— and the tracker table grows without bound. Cap it with LRU eviction.

## 6. Screen, estimate, classify

1. Skip the level if $Q_0 \le 0$. $V_{\text{cum}}/0$ reports ordinary displayed volume
   as 100% hidden.
2. Flag when $V_{\text{cum}}/Q_0 \ge$ `min_volume_ratio` **and**
   $N_{\text{refills}} \ge$ `min_refill_count`. The refill requirement — not the volume
   ratio — is what separates a hidden-liquidity screen from a large-print detector: a
   single 5,000-share sweep into a 500-share level clears the ratio on its own.
3. Estimate $\hat{Q}_{\text{hidden}} = \max(0, V_{\text{cum}} - Q_0)$, reported as a
   **lower bound** conditional on all refills coming from one resting order.
4. Classify from the tracked **book side**: `BID` → `BULLISH_HIDDEN_BUY`, `ASK` →
   `BEARISH_HIDDEN_SELL`. Never from an individual print's aggressor side.

## 7. Score without claiming certainty

```
score  = 0.50
       + 0.10 * min(volume_ratio, 3.0)      # ratio cap: one huge print must not saturate
       + 0.10 * min(refill_count, 5)
       - 0.15 if contra-side volume present  # the level changed sides
       - 0.10 if refill peaks inconsistent
score  = clamp(score, 0.0, 0.95)             # never 1.0
```

The cap is the point: on aggregated price-level depth the hypothesis is not
confirmable, so the engine must not report certainty. The score is ordinal, is not
calibrated against labelled data, and is not a probability.

## 8. Emit the report with its diagnostics

`IcebergDetectionReport` carries `contra_side_traded_quantity`,
`observed_refill_peaks`, `refill_peaks_consistent`, `volume_ratio` and
`is_initial_detection` alongside the estimate, so a downstream reader can judge the
call rather than trust the label. A report is emitted on every qualifying trade at the
level; use `is_initial_detection` to separate first detection from updates.

## 9. Escalate to order-level data

Where MBO / L3 is available, use it: CME native icebergs keep the same OrderID across
a refresh and synthetic ones do not, a distinction that simply does not exist in
aggregated depth. Treat this screen as a candidate generator that tells you *which
levels are worth reconstructing from message logs*.

---
name: iceberg-order-simulation-and-detection
description: >-
  Market microstructure screen for hidden institutional Iceberg / Reserve orders, flagging price levels where cumulative same-side trade volume exceeds displayed Level 2 depth with repeated visible refills, and estimating a lower bound on hidden size.
domain: Market Microstructure & Latency
subdomain: Order Flow Toxicity & Iceberg Detection
tags: ["iceberg-detection", "hidden-liquidity", "market-microstructure", "level-2-depth", "order-flow", "institutional-accumulation"]
brokers_frameworks: ["Level 2 Order Book Feeds", "Trade Print Logs", "Bookmap / Sierra Chart", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in order-flow research, book-toxicity monitoring, and execution-algo pre-trade checks where you need to know whether a price level is holding more liquidity than it displays. An iceberg (Nasdaq: *Reserve Order*; CME Globex: *Display Quantity*, FIX tag 1138) carries a total size and a smaller display peak; the venue replenishes the peak from the hidden remainder as it executes. This engine tracks each price level and flags candidates where cumulative volume consumed *from the tracked resting side* exceeds the depth ever displayed there ($V_{\text{cum}} \ge 1.5 \times Q_0$) **and** the level was visibly refilled at least twice, then classifies `BULLISH_HIDDEN_BUY` (hidden bid) vs `BEARISH_HIDDEN_SELL` (hidden ask).

Treat every output as a **candidate for further work**, not a confirmation — see below.

## When NOT to Use

- **As proof that an iceberg exists.** This consumes aggregated price-level (L2 / Market-by-Price) depth. At that aggregation, a level replenished by twenty independent participants is indistinguishable from one iceberg refilling twenty times. The engine cannot tell them apart and neither can you from this input.
- **When order-level (MBO / L3) data is available — use that instead.** CME native icebergs keep the **same OrderID** across a display refresh while trade summary messages carry the true trade volume; CME states this makes them detectable "unambiguously and accurately" from Market by Order data. ISV-held (synthetic) icebergs submit a new order per refresh and get a **new OrderID**. Both distinctions are invisible in aggregated depth. See `historical-order-book-reconstruction-from-message-logs`.
- **As a surveillance or enforcement signal.** Iceberg and Reserve orders are ordinary, explicitly supported order types on every venue cited here. A positive screen is not evidence of spoofing, layering, or abuse — see `wash-trade-and-spoofing-self-detection` for that, which is a different problem.
- **As a calibrated probability.** `confidence_score` is an ordinal heuristic bounded strictly below 1.0 because the hypothesis is not confirmable from this input. Do not size positions off it.
- **With the shipped thresholds unchanged on an unfamiliar instrument.** `min_volume_ratio = 1.5` and `min_refill_count = 2` are tunable defaults, not published venue or regulatory values. Calibrate per liquidity tier — see `algo-parameter-defaults-by-instrument-liquidity-tier`.

## Prerequisites

- Trade print stream (`trade_id`, `price`, `quantity`, `aggressor_side`, `timestamp_nanos`). A stable `trade_id` is required for duplicate suppression across reconnects.
- Level 2 depth snapshot stream per price level (`price`, `side`, `displayed_quantity`, `timestamp_nanos`), timestamped from the same clock as the trade stream.
- `tick_size` for the instrument. Strongly recommended: it bins prices to integer ticks so two float representations of one economic level cannot split into two half-populated trackers.
- Detection parameters, calibrated per instrument (`min_volume_ratio`, `min_refill_count`, `level_reset_dwell_nanos`).

## Workflow

1. **Ingest depth and prints against a canonical level key**:
   - Bin every price to integer ticks before using it as a level key. Raw floats are not safe keys: a feed computing $100.1 + 0.2$ and one parsing `"100.30"` produce unequal values that split one level into two trackers, suppressing detection at both.
   - Drop a depth snapshot whose timestamp precedes the last one processed for that level. A late-arriving stale snapshot reads as depth *increasing* back to its earlier value and books a refill that never happened.
   - Suppress a `trade_id` already accumulated. Reconnects and snapshot+delta recovery redeliver prints, and re-adding an execution inflates the very quantity being estimated.
2. **Accumulate same-side volume only**:
   - A SELL aggressor lifts resting bids; a BUY aggressor lifts resting asks. Add volume to $V_{\text{cum}}$ only when the aggressor consumes the *tracked* side. A print on the other side did not come out of the resting order being measured — record it as contra-side flow instead.
3. **Count refills, and re-baseline when the resting order is gone**:
   - Count a refill when displayed depth increases at a level that has already traded on the tracked side, and record the peak size it refilled *to*.
   - Re-baseline the level (new $Q_0$, zeroed volume and refills) when the level flips between bid and ask, or when it sits empty longer than the reset dwell. A venue refresh is immediate; a level that stays empty and then returns is a **different order**, and carrying old volume forward is how a level revisited hours later false-positives.
4. **Screen and estimate**:
   - Flag when $V_{\text{cum}} / Q_0 \ge$ `min_volume_ratio` **and** $N_{\text{refills}} \ge$ `min_refill_count`. Skip the level entirely if $Q_0 \le 0$ — dividing by a zero baseline reports ordinary displayed volume as 100% hidden.
   - Estimate $\hat{Q}_{\text{hidden}} = \max(0,\ V_{\text{cum}} - Q_0)$. This is a **lower bound**, and only under the assumption that every refill came from the same resting order.
5. **Classify from the book side, then score**:
   - Resting side is authoritative: `BID` $\implies$ `BULLISH_HIDDEN_BUY`, `ASK` $\implies$ `BEARISH_HIDDEN_SELL`. Deriving the side from the aggressor instead lets one contra-side print invert the call.
   - Score is heuristic and capped below certainty. Deduct for contra-side flow (the level changed sides) and for refill peaks of inconsistent size (a venue-held iceberg replenishes to a repeatable peak — though Nasdaq **Random Reserve** randomizes it, so this is supporting evidence only, never a gate).
6. **Emit `IcebergDetectionReport`** with the diagnostics that let a reader judge the call: contra-side volume, observed refill peaks, peak consistency, volume ratio, and whether this is the first emission at the level.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a positive screen as a confirmed iceberg.** Aggregated price-level depth cannot attribute a refill to an order. The published CME method resolves this with order-level messages precisely because price-level data cannot.
- **Confusing a single aggressive sweep with an iceberg.** A 5,000-share market order into a 500-share level clears the volume ratio on its own. The refill requirement — not the volume ratio — is what makes this a hidden-liquidity screen rather than a large-print detector.
- **Counting both aggressor sides toward hidden size.** A level flips between bid and ask across a session. Adding BUY-aggressor volume to a level tracked as a hidden bid inflates $\hat{Q}_{\text{hidden}}$ by the entire contra-side print.
- **Deriving the signal from the aggressor rather than the book side.** One SELL-tagged print at an ask level — a crossed seller, or a Lee-Ready misclassification — will otherwise flip a resting sell iceberg into a "bullish institutional support floor."
- **Never resetting a price level.** Trackers keyed by price and never re-baselined accumulate across the whole session, so a level revisited hours later is scored against a stale morning baseline. Bound the tracker table too: an unbounded per-price dict on a long session is a memory leak.
- **Double-counting replayed prints.** After a WebSocket reconnect or a snapshot+delta recovery, the same execution arrives twice. Without `trade_id` suppression it is counted twice, and the duplicate lands directly in the hidden-size estimate.
- **Assuming replenishment fires at exactly zero.** Nasdaq replenishes a Reserve Order when execution drops the display *below a round lot*, not at zero, and issues the replenished display a **new timestamp** while the reserve keeps its original one.

## Verification

- Instantiate `IcebergDetectorEngine(symbol="AAPL", tick_size=0.01)`. Simulate a $\$100.00$ bid with $Q_0 = 500$, then inject 4 SELL-aggressor prints of $400$ shares each ($1{,}600$ traded) with the level refilling to $500$ between them $\implies$ verify `BULLISH_HIDDEN_BUY` with $\hat{Q}_{\text{hidden}} = 1{,}100$, 3 refills, consistent peaks, and a heuristic score of $0.95$ (the cap — the engine never reports certainty).
- Verify the inversion guard: run the same pattern on the **ask** with BUY aggressors, then inject one SELL print at that price $\implies$ the signal must stay `BEARISH_HIDDEN_SELL`, and the SELL volume must land in `contra_side_traded_quantity`, not in `estimated_hidden_quantity`.
- Verify feed-integrity handling: replay a `trade_id` already seen $\implies$ `None` returned and $V_{\text{cum}}$ unchanged; apply a depth snapshot older than the last one processed $\implies$ refill count unchanged.
- Run `python scripts/test_iceberg_order_simulation_and_detection.py`.

## Related Skills

- `iceberg-order-native-broker-support-vs-simulation`
- `historical-order-book-reconstruction-from-message-logs`
- `order-book-microstructure-signal-research`
- `order-book-depth-processing-l2-l3`
- `market-data-snapshot-plus-delta-reconciliation`

---
name: cross-venue-latency-arbitrage-defensive-design
description: Defensive market-making module scoring lead-venue depth imbalance and the
  cancel-versus-sweep latency race, returning asymmetric spread skew, size reduction,
  and preemptive quote-pull directives for the secondary venue.
domain: Market Microstructure & HFT
subdomain: Latency Arbitrage & Adverse Selection
tags:
- latency-arbitrage
- hft
- market-making
- micro-price
- stale-quote
- adverse-selection
- cross-venue
brokers_frameworks:
- Order Book Engine
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when quoting passively on a secondary venue while price discovery happens on a geographically separated lead venue (e.g. CME Chicago `ES` futures against a Nasdaq Carteret `SPY` quote). When the lead venue moves, faster participants send aggressive orders to take the now-stale quote before the local cancel arrives. This module scores top-of-book depth imbalance on the lead venue, evaluates whether a cancel can beat the incoming sweep, and returns defensive directives: which side to widen, how much size to show, and whether to pull quotes entirely. It is the same class of signal that IEX's Crumbling Quote Indicator implements natively for its D-Limit order type (SEC Release 34-89686, approved Aug 2020; upheld by the D.C. Circuit in *Citadel Securities v. SEC*, 2022).

**Terminology (important):** `calculate_micro_price()` returns the **imbalance-weighted mid**, `(V_ask·P_bid + V_bid·P_ask) / (V_bid + V_ask)`. This is *not* Stoikov's micro-price, which is defined as the long-run expectation of the mid conditional on available information and which that paper reports as a *better* short-horizon predictor than the weighted mid. The method name is retained for API compatibility; treat the output as the weighted mid.

## When NOT to Use

- **As a registered market maker's excuse to stop quoting.** Under MiFID II RTS 8 (Commission Delegated Regulation (EU) 2017/578, Arts. 1–2), a firm in a market-making agreement must post firm, simultaneous two-way quotes of comparable size and competitive prices for **at least 50% of daily trading hours**. Art. 3's exceptional-circumstances list (extreme volatility triggering volatility mechanisms, war/industrial action/civil unrest/cyber sabotage, disorderly trading conditions, inability to maintain prudent risk management, non-equity suspensions) does **not** include "the engine detected adverse selection". Routine defensive pulls must fit inside the agreement, not override it.
- **Without an order-to-trade-ratio budget.** Cancel-heavy defense consumes OTR headroom; venues set and monitor OTR limits per member and per instrument, in both volume and number, under MiFID II RTS 9 (Reg. (EU) 2017/566). See `order-to-trade-ratio-fee-penalty-avoidance`.
- **As a queue-position or fill model.** The engine says nothing about where your order sits in the queue or whether the widened quote will ever fill. See `queue-position-modeling-for-passive-orders`.
- **With mean latencies as the only input.** The margin is a point estimate; pick-off risk is driven by the tail of the latency distribution, not its mean. A margin of "+20 µs on average" is not protection when the 99th percentile is +200 µs of jitter — size the `latency_safety_margin_us` buffer from measured percentiles.
- **As an order router or execution component.** It returns directives only; it never sends or cancels an order.

## Prerequisites

- Lead-venue top-of-book snapshot: `bid_price`, `ask_price`, `bid_volume`, `ask_volume`, and the instrument's true `tick_size`.
- One-way latencies in microseconds: the competitor's lead-event-to-secondary-venue sweep time, and your cancel's delivery time to the same venue, on one synchronized clock domain (see `cross-datacenter-clock-sync-validation`).
- A toxicity threshold **calibrated from your own realized adverse-selection data** — no universal value exists, which is why the normalized threshold ships disabled.

## Workflow

1. **Lead-Venue Imbalance Audit** (`calculate_micro_price`):
   - Weighted mid $P_w = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$ — a convex combination of the touch prices, so $P_w \in [P_{\text{bid}}, P_{\text{ask}}]$ **always**.
   - Decision point: with no displayed depth on either side there is no imbalance information, so the mid is returned unchanged rather than a fabricated skew.
   - Values are **not** rounded. Rounding to 4 decimals erases the signal entirely on finely quoted instruments (at a 0.00001 FX tick, a 100:1 imbalance rounds to zero toxicity).
2. **Toxicity Scoring** — two measures, because one of them is spread-dependent:
   - $\tau_{\text{ticks}} = |P_w - P_{\text{mid}}| / \text{TickSize}$ (legacy). **Hard bound: $\tau_{\text{ticks}} \le$ half-spread in ticks**, since $P_w$ cannot leave the touch. In a one-tick-wide market it can never exceed 0.5, so any tick threshold above that never fires there. `half_spread_ticks` is reported so the ceiling is visible.
   - $\tau_{\text{norm}} = |P_w - P_{\text{mid}}| / (\text{spread}/2) = \left|\frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}\right| \in [0, 1]$ — scale-free and comparable across instruments and spread regimes. Computed from the volumes rather than by differencing two nearly equal prices, so a balanced book scores exactly zero.
   - `toxic_side` names the exposed side: $P_w >$ mid means buying pressure and an exposed **ASK**.
3. **Cross-Venue Latency Race**:
   - $\Delta t_{\text{margin}} = (t_{\text{lead}} + t_{\text{sweep}}) - (t_{\text{cancel\_sent}} + t_{\text{cancel\_delivery}})$.
   - Decision point: **a margin of exactly zero is a loss** — the venue processes messages in arrival order — so defenses trigger on $\Delta t_{\text{margin}} \le$ `latency_safety_margin_us`, not on $< 0$.
   - A `cancel_sent_timestamp_us` earlier than the lead event raises: a cancel cannot react to an event that has not happened, and unchecked it reports a lost race as won.
4. **Defensive Directives**:
   - **Locked or crossed book** ($P_{\text{bid}} \ge P_{\text{ask}}$) short-circuits to maximum defense with size 0. There $P_w$ and the mid coincide, so a toxicity score of 0.0 would otherwise report the most dangerous state as benign.
   - **Race lost $\implies$ size 0.** Widening a quote you cannot cancel in time protects nothing; the only remaining defense is not being on the book.
   - **Widening is asymmetric**: only the exposed side moves, to $S_{\text{base}}(1 + k\,\tau_{\text{norm}})$, bounded by $S_{\text{base}}(1+k)$. Scaling on $\tau_{\text{ticks}}$ instead is unbounded and spread-dependent — a 25-tick-wide book produced a 13.5× base spread from one imbalance reading.
   - Size tapers linearly, $Q_{\text{base}}(1 - \tau_{\text{norm}})$, reaching 0 as the weighted mid pins against the touch.
5. **Audit Report**: `LatencyArbitrageDefenseReport` with both toxicity measures, `half_spread_ticks`, `toxic_side`, and machine-readable `trigger_reasons` (`TOXICITY_TICKS`, `TOXICITY_NORMALIZED`, `LATENCY_RACE_LOST`, `LOCKED_BOOK`, `CROSSED_BOOK`) — branch on these, not on the free-text recommendations.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Setting a Tick Threshold the Metric Cannot Reach**: because $|P_w - P_{\text{mid}}| \le \text{spread}/2$ by construction, a "τ ≥ 2.0 ticks" rule is dead code in any market quoted tighter than 4 ticks — exactly the tight, liquid books where latency arbitrage happens. Calibrate on the normalized score, or state the spread regime the tick threshold assumes.
- **Rounding Prices Before Differencing Them**: a rounded weighted mid quantizes the toxicity signal by whole ticks — and to nothing at all on FX (5 decimals) or crypto (8 decimals) instruments.
- **Differencing Nearly Equal Prices**: computing the imbalance skew as `weighted_mid - mid` leaves float residue on a perfectly balanced book, enough to report a phantom toxic side and shave the quote size. Derive it from the volumes.
- **Widening Both Sides Equally**: the imbalance signal's entire value is telling you *which* side gets picked off. Symmetric widening pays the spread cost twice and discards the direction.
- **Treating a Dead Heat as Safe**: a zero-microsecond margin means the cancel and the sweep arrive together, and arrival order decides. Defend on `<= 0`, with a jitter buffer sized from measured latency percentiles.
- **Confusing RTT with One-Way Delivery**: the race depends on the time for the cancel to *reach* the matching engine. Passing a full round trip (which includes the venue's acknowledgement returning) makes the check conservative but is not the quantity being raced.
- **Announcing a Quote Pull While Recommending Size**: a report that sets `is_preemptive_cancel_triggered=True` and a positive `defensive_quote_size` leaves the caller quoting into the sweep it just warned about.
- **Scoring a Crossed Book as Calm**: on a locked or crossed book the weighted mid equals the mid, so any deviation-based score reads zero. Detect the state explicitly.
- **Pulling Quotes Against a Market-Making Agreement**: MiFID II RTS 8 requires two-way presence for at least 50% of daily trading hours; adverse-selection risk is not one of the Art. 3 exceptional circumstances.
- **Ignoring the OTR Cost of Defense**: cancel-heavy quoting burns order-to-trade-ratio budget that venues monitor and penalize under RTS 9.

## Verification

- Confirm the bound: a one-tick market with a 1e9:1 imbalance yields `toxicity_index_ticks < 0.5` and `half_spread_ticks == 0.5`, while `toxicity_index_normalized > 0.99`.
- Confirm scale-freedom: the same 10:1 imbalance in a 1-tick and a 50-tick book gives identical `toxicity_index_normalized` and very different `toxicity_index_ticks`.
- Cross-check the score: for $V_{\text{bid}}=1000, V_{\text{ask}}=100$, expect `toxicity_index_normalized == 900/1100`, and confirm it equals $|P_w-\text{mid}|/(\text{spread}/2)$ recomputed from the reported prices.
- Fine-tick regression: bid 1.10500 / ask 1.10501, volumes 1000/10, tick 0.00001 — expect non-zero toxicity and `toxic_side == "ASK"`.
- Direction: heavy bid volume gives `ASK`, heavy ask volume gives `BID`, equal volumes give `NONE` (exactly, not approximately).
- Asymmetry: with an exposed ask, `defensive_bid_spread_ticks` stays at base while `defensive_ask_spread_ticks` rises and stays $\le S_{\text{base}}(1+k)$.
- Race: a margin of exactly `0.0` must trigger; a lost race must return `defensive_quote_size == 0`; a `cancel_sent_timestamp_us` before the lead event must raise `ValueError`.
- Crossed book (bid 100.05 / ask 100.00) must trigger `CROSSED_BOOK`, size 0 — not "normal market making".
- Reject `tick_size <= 0`, negative volumes, and non-finite prices or latencies.
- Run `python -m unittest discover -s skills/cross-venue-latency-arbitrage-defensive-design/scripts`.

## Related Skills

- `adverse-selection-measurement-for-passive-orders`
- `latency-arbitrage-defensive-order-sizing`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `queue-position-modeling-for-passive-orders`

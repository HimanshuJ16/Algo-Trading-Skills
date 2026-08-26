---
name: latency-arbitrage-defensive-order-sizing
description: >-
  Defensive sizing for a passive quote exposed to stale-quote sniping across a cross-venue cancel latency gap. Scores a Poisson sniping hazard, scales the shown quantity down, widens the quoted spread, pulls the quote above a risk threshold or below the instrument's round lot, and fails closed on unreadable latency telemetry.
domain: Market Microstructure & Latency
subdomain: HFT Defense & Passive Liquidity Risk
tags: ["latency-arbitrage", "adverse-selection", "sniping-risk", "defensive-order-sizing", "market-making", "microstructure", "spread-widening"]
brokers_frameworks: ["Cross-Venue Latency Telemetry", "FIX Order Gateway", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a **passive, liquidity-providing** quote rests on a venue that is slower to reach than the venue where price discovery actually happens — a Nasdaq `SPY` quote against CME `ES`, a secondary listing against the primary, a regional venue against the consolidated tape. When the lead venue moves, faster participants race your cancel with an aggressive order against the now-stale quote. Budish, Cramton & Shim (2015) model this directly: the fundamental value follows a **compound Poisson jump process**, a resting quote is snipe-able only when a jump exceeds half the spread, and the liquidity provider loses the resulting race with probability $\frac{N-1}{N}$ against $N-1$ snipers.

This module answers one narrow question — *how much size should be showing given that race* — and returns a defensive quantity, a spread multiplier, an already-widened spread, and a cancel directive:

$$P_{\text{snipe}} = 1 - e^{-h \Delta\tau}, \quad h = \lambda \sigma, \qquad Q_{\text{def}} = \left\lfloor Q_0 (1 - P_{\text{snipe}}) \right\rfloor_{\text{lot}}, \qquad W = 1 + 2 P_{\text{snipe}}$$

## When NOT to Use

- **With the shipped `lambda_scaling = 0.50` unchanged.** It is a **placeholder**, not a calibrated or published value, and its units are the unusual "snipe-able events per millisecond per unit of *annualized* volatility". At that default the engine stops quoting entirely above **6.93 ms** at $\sigma = 0.20$ and above **1.73 ms** at $\sigma = 0.80$ — on a venue with a 3 ms cancel path it would never quote a volatile name. Calibrate against realized markouts on your own passive fills; see `adverse-selection-measurement-for-passive-orders`.
- **As a derivation of sniping probability from volatility.** It is not one. At 20% annualized volatility a 1 ms window carries a diffusive standard deviation of about **0.026 bps**, which puts a 1 bp half-spread roughly **38σ** away. The shipped 9.5% cannot come from a diffusion — sniping is a *jump* phenomenon, which is exactly BCS's point. $\lambda\sigma$ is a monotone proxy for the BCS intensity $\lambda_{\text{jump}}\Pr(J > s/2)$, not a derivation of it.
- **As a registered market maker's licence to stop quoting.** Under MiFID II RTS 8 (Reg. (EU) 2017/578) Art. 1(1), a firm inside a market making agreement must post firm, simultaneous two-way quotes of comparable size and competitive prices for **at least 50% of the daily trading hours** of continuous trading. Art. 3's exhaustive exceptional-circumstances list does **not** include "the sizing engine scored elevated sniping risk". A routine defensive pull is not an Art. 3 event.
- **On one side of a two-sided quote without checking divergence.** Art. 1(2)(c) defines quotes as "of comparable size" only where "their sizes do not diverge by more than 50% from each other". Shrinking one side by $P_{\text{snipe}}$ while the other stands unscaled breaches that once the divergence passes 50% — the engine reports `size_divergence_ratio` and `breaches_comparable_size_one_sided` so the caller can see it, but it sizes one quote and cannot enforce two-sided parity itself.
- **As a cancel-race model.** This module *conditions on losing the race*; it has no cancel-vs-sweep margin, no lead-venue imbalance, no clock-domain reconciliation. For the race itself, use `cross-venue-latency-arbitrage-defensive-design`.
- **With a mean latency as the input.** Pick-off risk lives in the **tail** of the latency distribution. A gap that averages 2 ms and reaches 20 ms at the 99th percentile is a 20 ms problem — feed a measured high percentile. See `latency-monitoring-percentile-based-slas`.
- **As an order router.** It returns directives. It never sends, amends, or cancels an order.

## Prerequisites

- A **measured** cross-venue exposure window `latency_gap_ms` — lead-event-to-sweep minus your cancel's delivery time, from one synchronized clock domain, taken at a high percentile rather than the mean.
- `volatility_annualized` for the instrument, and a baseline `spread_bps`.
- `min_lot_size` set to the instrument's **actual** round lot. For NMS stocks this is price-tiered, not a flat 100: 100 / 40 / 10 / 1 shares by prior-Evaluation-Period average closing price (17 CFR 242.600(b)(93)). Use `round_lot_for_nms_price()`; the shipped default of 100 is correct only at or below \$250.00 per share.
- `lambda_scaling` calibrated from realized passive-fill toxicity — **not** inherited from the default.

## Workflow

1. **Reject unusable telemetry before it reaches the arithmetic**:
   - A non-finite `latency_gap_ms` or `volatility_annualized`, or a negative volatility, returns `INVALID_INPUT_CANCEL` at $P_{\text{snipe}} = 1.0$ with the quote pulled. Decision point: the engine **fails closed**. A defensive sizer that reads a dropped latency probe as "no risk" posts *maximum* size into exactly the degraded condition it exists to detect.
   - A **negative** gap is not an error — it means your cancel beats the sweep, so exposure is genuinely zero and the full base size stands.
   - Structural inputs (`base_quote_qty`, `min_lot_size`, `lot_increment`, `spread_bps`, `symbol`) raise `ValueError` on construction instead. Those are configuration errors, not runtime market conditions, and should surface loudly rather than as a cancel.
2. **Score the sniping hazard**:
   - $h = \lambda \sigma$ events/ms; $P_{\text{snipe}} = 1 - e^{-h\Delta\tau}$, reported to 4 dp. Rounding is applied *before* the cancel threshold is tested, which can only ever move a borderline case toward cancelling.
3. **Size, floor, and widen**:
   - $Q_{\text{def}} = \lfloor Q_0 (1 - P_{\text{snipe}}) \rfloor$, then floored again to a multiple of `lot_increment`. Decision point: **down, never up** — rounding a defensive size up shows more risk than the model just authorised.
   - $W = 1 + 2P_{\text{snipe}}$, and `defensive_spread_bps` $= $ `spread_bps` $\times W$ is returned already applied. Note the model's own gap: $P_{\text{snipe}}$ is **spread-independent**, so widening does not feed back into it. In BCS a wider spread lowers $\Pr(J > s/2)$ and so lowers the risk directly; here $W$ is a directive to the quoter, not a risk reduction this engine has already banked.
4. **Apply the cancel precedence, highest first** — each stage returns a full report so the decision stays auditable:
   - `INVALID_INPUT_CANCEL` → `HIGH_SNIPING_RISK_CANCEL` ($P_{\text{snipe}} \ge$ threshold, **inclusive**: at the point of indifference the safe answer is not being on the book) → `MIN_LOT_CANCEL` (a sub-round-lot residual pays the fee without holding a usable quote, and on an NMS stock it is odd-lot information under Rule 600(b)(69) rather than a protected quotation) → `QUOTE_DEFENSIVELY_SIZED`.
5. **Check the size divergence before sending**:
   - `size_divergence_ratio` measures the reduction against the **smaller** quote — the conservative reading, since Art. 1(2)(c) does not name a denominator. A pulled quote reports `inf`, not a small number.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing open on degraded telemetry.** A NaN latency sample does not compare greater than zero, and `max(0.0, nan)` is `0.0` — so a naive implementation scores 0% sniping risk and posts the full quote. This engine's single worst failure mode is silently treating "I cannot measure the gap" as "there is no gap".
- **Inheriting `lambda_scaling`.** No venue, regulator, or paper publishes this constant. Shipping the default into production sets your quoting cut-off at an arbitrary latency you did not choose.
- **Reading $P_{\text{snipe}}$ as a calibrated probability.** It is an ordinal risk score built on a proxy hazard. Do not price options off it, and do not report it to a risk committee as a probability of being picked off.
- **Shrinking one side of a two-sided quote.** Under a market making agreement, sizes that diverge by more than 50% are no longer "of comparable size" (RTS 8 Art. 1(2)(c)). The defensive reduction that protects you from sniping is the same reduction that breaches the agreement.
- **Treating a defensive pull as an Art. 3 exceptional circumstance.** Art. 3 lists extreme volatility triggering volatility mechanisms; war, industrial action, civil unrest, cyber sabotage; disorderly trading conditions; inability to maintain prudent risk management; and non-equity suspensions. Elevated sniping risk is on none of those lists. (A genuinely broken data feed may fall under Art. 3(d)(i) — routine elevated latency does not.)
- **Hard-coding a 100-share minimum.** On a \$1,500 stock the NMS round lot is **10** shares, so a valid 40-share defensive quote is cancelled by the default for no reason.
- **Posting static size through a latency spike.** Holding a 10,000-share passive quote while the cross-venue gap widens to 20 ms is the failure this skill exists to prevent — but note that at the shipped defaults the engine reaches full cancellation long before 20 ms, so verify your calibrated cut-off is where you intend it.
- **Feeding a mean latency.** The gap that picks you off is the tail one.

## Verification

- **Normal market.** `MarketStateSpec("AAPL", 1000, latency_gap_ms=1.0, volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100)` $\implies$ $h = 0.10$/ms, $P_{\text{snipe}} = 1 - e^{-0.1} = 0.0952$, `defensive_quote_qty = 904` (floored from 904.8, **not** 905), $W = 1.19$, `defensive_spread_bps = 2.38`, `size_divergence_ratio` $= 96/904 \approx 0.106$, no comparable-size breach, `QUOTE_DEFENSIVELY_SIZED`.
- **High latency, volatile.** $\Delta\tau = 25$ ms, $\sigma = 0.80$ $\implies$ exponent $-10$, $P_{\text{snipe}}$ rounds to $1.0$, `defensive_quote_qty = 0`, `size_divergence_ratio = inf`, `HIGH_SNIPING_RISK_CANCEL`.
- **Threshold is inclusive.** $\Delta\tau = \ln 2 / 0.10 = 6.931$ ms at $\sigma = 0.20$ puts $P_{\text{snipe}}$ at exactly $0.50$ $\implies$ must cancel, not quote.
- **Fails closed.** `latency_gap_ms = float("nan")` on a 10,000-share base $\implies$ `INVALID_INPUT_CANCEL`, `defensive_quote_qty = 0`, $P_{\text{snipe}} = 1.0$. It must **not** return `QUOTE_DEFENSIVELY_SIZED` with 10,000 shares.
- **Round lot is price-tiered.** `round_lot_for_nms_price(250.00) == 100`, `(250.01) == 40`, `(1000.01) == 10`, `(10000.01) == 1`.
- Run `python scripts/test_latency_arbitrage_defensive_order_sizing.py`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `adverse-selection-measurement-for-passive-orders`
- `queue-position-modeling-for-passive-orders`
- `latency-monitoring-percentile-based-slas`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `minimum-fill-size-and-lot-rounding-logic`
- `mifid-ii-algo-trading-compliance-eu`

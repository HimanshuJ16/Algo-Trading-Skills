---
name: message-rate-limit-vs-latency-tradeoff-tuning
description: >-
  Use when sizing the quote reprice interval for a strategy sharing a session with a
  message-rate ceiling, trading queue priority and adverse selection against the message
  budget. A parameter calculator, not a runtime throttle.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: message-rate-limit, latency-tradeoff, quote-suppression, adverse-selection, cme-ilink3, mps-tuning, hft-optimization
  brokers_frameworks: "CME iLink 3 MPS; Binance Rate Limits; MiFID II RTS 6; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when sizing the quote reprice interval for an automated market making or algorithmic execution strategy that shares a venue session subject to a message rate ceiling. Repricing on every price flick preserves queue priority but burns the session's message budget; inserting a large reprice delay leaves stale quotes exposed to being picked off ($\tau_{\text{exposure}}$). This module derives the smallest reprice delay ($\Delta t_{\text{optimal\_ms}}$) that keeps the projected session message rate inside a configured safety buffer, and refuses to certify a configuration it cannot bring inside that budget.

Under **MiFID II RTS 6 Art. 15(1)(d)** an EU investment firm engaged in algorithmic trading must operate "maximum messages limits, which prevent sending an excessive number of messages to order books pertaining to the submission, modification or cancellation of an order." This skill sizes that limit; it does not enforce it.

## When NOT to Use

- **As the enforcement mechanism.** This is an offline/periodic parameter calculator, not a runtime throttle. RTS 6 Art. 15(1)(d) requires a control that actually *blocks* excess messages in the order path — pair this with `matching-engine-throttle-and-message-gapping-detection`. A recommended delay that nothing enforces is not a pre-trade control.
- **For burst control.** The model is steady-state: it reasons about an average rate, not the sub-second bursts that venues measure over a rolling window (CME evaluates administrative message thresholds as an average over a **three-second** window). A configuration that passes here can still trip a burst threshold on a tick storm.
- **To derive the price threshold.** `price_threshold_bps` is an operator-supplied input echoed into the audit notes. The engine does **not** compute an optimal quote-suppression threshold — deriving one requires a tick-size and price-move distribution this module does not take.
- **As an adverse-selection cost model.** `adverse_selection_exposure_score` is an ordinal staleness heuristic in bps-milliseconds, not a currency cost and not a calibrated pick-off probability. See Common Pitfalls.
- **For order-to-trade ratio economics.** Staying under an MPS ceiling does not keep you under a message-efficiency benchmark; those are separate regimes with separate penalties — see `order-to-trade-ratio-fee-penalty-avoidance`.

## Prerequisites

- The venue's **actual** published limit for the session in question (`exchange_max_mps`). This is not a universal constant — see `references/standards.md`. Where a venue publishes both a reject and a terminate threshold, configure the lower (reject) one.
- Tuning configuration (`symbol`, `target_safety_buffer_pct` in $(0, 100]$, `min_reprice_delay_ms`, `max_reprice_delay_ms`, `price_threshold_bps`).
- Real-time market state (`ticks_per_sec`, `price_volatility_bps`, `active_quoting_pairs`, and `baseline_session_mps` for any co-resident flow on the same session).

## Workflow

1. **Target Rate & Unthrottled Velocity Calculation**:
   - Compute target MPS limit: $R_{\text{target\_mps}} = R_{\text{max\_mps}} \times \frac{\text{safety\_pct}}{100.0}$.
   - Compute unthrottled message velocity: $R_{\text{unthrottled}} = \text{TicksPerSec} \times \text{ActiveQuotingPairs}$.
   - Deduct co-resident flow to get the repricing budget: $R_{\text{available}} = R_{\text{target\_mps}} - R_{\text{baseline}}$. Tuning is required when $R_{\text{unthrottled}} + R_{\text{baseline}} > R_{\text{target\_mps}}$.
2. **Optimal Quote Reprice Delay Tuning**:
   - Compute required reprice delay, **rounded up** to the reported precision — rounding a throttle delay to nearest puts the realised rate back above the target:
     $$\Delta t_{\text{optimal\_ms}} = \text{clamp}\left( \left\lceil \frac{1{,}000.0 \times \text{ActiveQuotingPairs}}{R_{\text{available}}} \right\rceil_{0.01},\ \Delta t_{\text{min\_ms}},\ \Delta t_{\text{max\_ms}} \right)$$
3. **Feasibility Gate — do not skip**:
   - Project the session rate: $R_{\text{projected}} = \min\left(R_{\text{unthrottled}},\ \frac{1{,}000.0 \times \text{ActiveQuotingPairs}}{\Delta t_{\text{optimal\_ms}}}\right) + R_{\text{baseline}}$.
   - If $R_{\text{projected}} > R_{\text{target\_mps}}$ the delay ceiling (or baseline flow) binds and no delay can meet the budget. Emit `RATE_LIMIT_TARGET_UNREACHABLE` with `is_target_achievable = False`. **Do not deploy these parameters** — reduce `active_quoting_pairs`, widen `price_threshold_bps`, or split the flow across sessions. Capping the delay and shipping anyway is what terminates the session.
4. **Adverse Selection Exposure Audit**:
   - Calculate the staleness heuristic: $\text{Score} = \Delta t_{\text{optimal\_ms}} \times \text{VolatilityBps}$, and compare it across candidate configurations only.
5. **Audit Report Generation**: Output structured `TuningReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Repricing Quotes on Minor Noise**: Updating resting limit orders for sub-tick price fluctuations, consuming the whole message budget during quiet markets. `price_threshold_bps` is the suppression knob — this engine reports it but will not choose it for you.
- **Ignoring High-Volatility Spikes**: Holding a static reprice delay through a crash, so the quote is stale exactly when informed flow is fastest. Re-run the tuner on the current `price_volatility_bps`, not a calm-market default.
- **No Safety Buffer**: Sizing for 100% of the exchange limit, leaving no headroom for tick bursts, retransmissions, or the cancels that unwind the position.
- **Sizing Against the Terminate Threshold**: Venues that publish two levels reject messages at the lower one and drop the session at the higher one. Budgeting against the terminate threshold means the strategy operates permanently inside the reject band, silently losing quotes.
- **Counting Only Quote Traffic**: RTS 6 Art. 15(2) requires *all* orders sent to the venue to count against the pre-trade limit. Hedges, cancels, and other strategies on the same session consume the same budget — pass them as `baseline_session_mps` or the projection is fiction.
- **Assuming One Message Per Reprice**: `active_quoting_pairs` assumes one cancel/replace amendment per side. A strategy that sends a separate cancel and a separate new order emits two messages per side and must double the value, or it will under-project the rate by 2×.
- **Reading the Exposure Score as a Cost**: The score is linear in delay, but pick-off risk is not. A resting quote is a free option written to the market (Copeland & Galai, 1983); under a driftless diffusion the expected absolute mid displacement over a staleness window $\Delta t$ grows with $\sigma\sqrt{\Delta t}$, not $\Delta t$. Use the score to rank candidate delays, never to size a spread or book a P&L expectation.
- **Trusting a Capped Delay**: Always branch on `is_target_achievable`, not on `status != "DIRECT_PASS_NO_TUNING_REQUIRED"`. A configuration whose required delay exceeds `max_reprice_delay_ms` is reported as unreachable precisely so it cannot be mistaken for a tuned pass.

## Verification

- Instantiate `MessageRateLatencyTunerEngine`. Audit a high-frequency stream ($100\text{ ticks/sec}$, $5\text{ quoting pairs}$, unthrottled rate $= 500\text{ MPS}$, session limit $= 500\text{ MPS}$, target buffer $= 80\% \implies 400\text{ MPS}$) $\implies$ verify the engine tunes the optimal reprice delay to $\Delta t_{\text{optimal}} = 12.5\text{ ms}$, projects rate $= 400\text{ MPS}$, and approves `RATE_LIMIT_TUNING_APPLIED`.
- Feasibility gate: $1{,}000\text{ quoting pairs}$ against the same $400\text{ MPS}$ target requires a $2{,}500\text{ ms}$ delay, beyond the $500\text{ ms}$ ceiling $\implies$ verify `RATE_LIMIT_TARGET_UNREACHABLE`, `is_target_achievable = False`, and projected rate $= 2{,}000\text{ MPS}$ (4× the exchange ceiling).
- Rounding direction: session limit $375\text{ MPS}$ at $80\%$ gives a $300\text{ MPS}$ target; the required delay $1000/300 = 3.\overline{3}\text{ ms}$ must round **up** to $3.34\text{ ms}$ ($299.4\text{ MPS}$), not to nearest ($3.33\text{ ms} \implies 300.3\text{ MPS}$, over budget).
- Shared session: $50\text{ ticks/sec} \times 2\text{ pairs} = 100\text{ MPS}$ passes alone, but with $380\text{ MPS}$ of `baseline_session_mps` $\implies$ verify tuning to a $100\text{ ms}$ delay and a projected $400\text{ MPS}$.
- Invalid budgets (`target_safety_buffer_pct` of $0$, negative, or $> 100$; NaN/negative `ticks_per_sec`) must raise `ValueError` at construction, never produce a report.
- Run `python -m unittest discover -s skills/message-rate-limit-vs-latency-tradeoff-tuning/scripts`.

## Related Skills

- `matching-engine-throttle-and-message-gapping-detection`
- `latency-monitoring-percentile-based-slas`
- `order-to-trade-ratio-fee-penalty-avoidance`

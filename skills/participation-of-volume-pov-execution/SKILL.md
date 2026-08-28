---
name: participation-of-volume-pov-execution
description: >-
  Use when a parent order must be worked as a target percentage of live market volume rather than on a clock — converting away volume into whole-share child orders via the R/(1-R) participation identity, correcting drift from the cumulative target, and keeping sent, working, and filled quantities separate so the reported participation rate reflects real fills.
domain: algorithmic-trading
subdomain: execution-algorithms
tags:
- execution-algorithms
- pov
- participation-of-volume
- market-impact
- child-order-scheduling
- fix-participationrate
- transaction-cost-analysis
brokers_frameworks:
- FIX 4.4 TargetStrategy(847)=Participate / ParticipationRate(849)
- Interactive Brokers TWS API PctVol
- Binance Futures Algo VP (newOrderVp)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a parent order is large enough that its own trading would move the price, and the right response is to let the market set the pace. A POV algorithm consumes a stream of market volume and sizes each child order so that the order's cumulative executions are a chosen fraction of total volume. Volume surges, it trades more; volume dries up, it trades less.

The rate is a fraction of **total** volume — the order's own prints included. If $V_{\text{away}}$ is what everyone *else* trades:

$$R = \frac{Q_{\text{own}}}{V_{\text{away}} + Q_{\text{own}}} \quad\Longrightarrow\quad Q_{\text{own}} = \frac{R}{1 - R} \times V_{\text{away}}$$

So a 15% participation target means trading ~17.65% of away volume, and 33.3% participation means matching away volume at ~50%. This is the convention FIX encodes as `TargetStrategy(847) = 2`, *"Participate (aim to be x percent of the market volume)"*.

A POV order **caps participation; it does not promise completion.** If volume never arrives, the order never fills. That is the trade-off you are choosing over a TWAP/VWAP schedule.

## When NOT to Use

- **When the order must complete by a deadline.** POV has no completion guarantee — its whole design is to defer to volume. A hard deadline needs a schedule (`execution-algo-twap-vwap-slicing`) or an urgency-aware trajectory (`implementation-shortfall-minimization`), and the reconciliation of the two is a business decision, not an algorithm parameter.
- **When alpha decays inside the execution horizon.** Participating passively while a signal decays converts alpha into opportunity cost that a fill-only benchmark will never show you.
- **In the closing auction or an opening cross.** Auction volume is a single crossing event, not a stream this engine can participate in incrementally — see `close-auction-participation-strategy`.
- **On a halted or auction-state instrument.** Volume is zero or non-continuous and the participation denominator is meaningless — see `execution-algo-behavior-under-halted-instrument`.
- **As the compliance control for a buy-back programme.** The regulatory caps for repurchases are stated against **average daily volume** measured over past sessions, not against live volume — a live participation rate is not evidence of compliance with them. See `references/standards.md`.
- **As the pre-trade risk gate.** This engine sizes child orders. Exposure, capital, price collars and the kill switch sit outside it, where a scheduling bug cannot disable them (`execution-algorithm-kill-switch-integration`).

## Prerequisites

- A validated parent order: `POVParentOrder(symbol, total_qty, side, target_rate, max_rate, min_slice_qty, max_slice_qty)`. Every field is validated on construction and a misconfiguration **raises** — it is never silently clamped.
- A market-volume stream, and an explicit answer to *what that stream counts*. Set `VolumeBasis.AWAY` if you can already exclude your own executions; set `VolumeBasis.CONSOLIDATED` if you are feeding raw tape volume, so the engine nets its own prints off.
- A fill feed. The engine cannot infer fills, and treats nothing as filled until you say so.
- `min_slice_qty` at or above the instrument's real minimum tradable quantity (`minimum-fill-size-and-lot-rounding-logic`), and a child-order path that is idempotent (`order-placement-idempotency`).

## Workflow

1. **Declare the volume basis before anything else.**
   Consolidated tape volume *already contains your own executions*. Feeding it in as away volume inflates the denominator by exactly the quantity you just traded, so the algorithm sizes the next slice off its own footprint and participates above target — persistently, and in the direction that costs money.

2. **Accumulate away volume and compute the cumulative target.**
   $$Q_{\text{target},t} = \left\lfloor \frac{R}{1-R} \times V_{\text{away, cum}, t} \right\rfloor$$
   - **Decision point — cumulative, not per-interval.** Sizing each slice from that interval's volume alone means every share lost to flooring, to a paused thin interval, or to an unfilled child order is lost permanently: the order silently under-participates and can stall at zero fills indefinitely. Working from the cumulative target makes the deficit recoverable.
   - The target is **floored**, so the rate is a ceiling the algorithm cannot round its way past.

3. **Size the slice from the deficit, not from the target.**
   $$Q_{\text{slice}} = \min\big(Q_{\text{target}} - Q_{\text{filled}} - Q_{\text{working}},\ \text{MaxSliceQty},\ Q_{\text{total}} - Q_{\text{filled}} - Q_{\text{working}}\big)$$
   - **Decision point — subtract working quantity.** Quantity already sent and not yet resolved is quantity the market will see. Omit it and every interval re-sends the same deficit, multiplying exposure by the number of updates before the first fill report.

4. **Apply the minimum-clip gate, with the tail exception.**
   A slice below `min_slice_qty` waits for volume to accrue. **But** once the schedulable residual is *itself* below the minimum, the gate must be bypassed — otherwise an odd-lot tail can never be sent and the parent order can never complete.

5. **Report every share back.**
   `record_fill(qty)` confirms an execution; `record_unfilled(qty, reason)` releases quantity from a cancelled, expired or rejected child order back to the schedule. Every share sent must be resolved by one or the other.
   - **Decision point — a request timeout is not a rejection.** The broker may have accepted the order before the response was lost. Reconcile against the broker before calling `record_unfilled`; releasing quantity that is actually live double-counts it into the next slice.
   - **Decision point — an over-fill is not clamped.** A fill exceeding the working quantity is recorded truthfully and surfaced as `overfill_qty`; the projected participation it creates then trips the `max_rate` backstop and stops further scheduling until you reconcile.

6. **Monitor realized participation.**
   $$\text{RealizedRate} = \frac{Q_{\text{filled}}}{V_{\text{away, cum}} + Q_{\text{filled}}}$$
   Computed from **fills only**. Working quantity has not printed and is not participation. Because the scheduled curve never exceeds the target, realized participation is bounded above by `target_rate` by construction; `max_rate` is the cap that was signed off, enforced as a backstop against fills the schedule did not produce.

> Full procedure: see `references/workflows.md`.
> Standards, jurisdictions, and broker parameter ranges: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting your own prints as market volume.** The tape prints your fill once, in the consolidated volume. Feed that back in as away volume and the algorithm chases its own footprint — the error compounds with participation rate and is invisible in any report that uses the same inflated denominator.
- **Reporting sent quantity as filled quantity.** The most damaging version of this bug is silent: participation looks on-target, the order looks half-done, and the position is flat. `success: true` from a broker's algo endpoint is an acknowledgement, not an execution — Binance says so explicitly for its VP endpoint.
- **Per-interval sizing that strands the order.** With `min_slice_qty` above any single interval's target, a per-interval POV emits zero forever while reporting a healthy "paused for volume" status. There is no error, no alert, and no fills.
- **Expecting completion.** A POV order with a large parent quantity in a thin name simply does not finish. Sizing capital or hedges on the assumption that it will is the failure, not the algorithm's behaviour.
- **Reading a 30% cap as a rule.** It is a common risk-policy default, nothing more. IBKR's TWS API documents `pctVol` at 10–50%; FIX `ParticipationRate(849)` accepts values up to 99.99%. Set the cap from your own impact analysis and record who approved it — do not cite it as a standard.
- **Confusing a live participation rate with a regulatory ADV cap.** SEC Rule 10b-18(b)(4) and EU Delegated Regulation 2016/1052 Art. 3(3) both cap buy-back purchases at 25% of *average daily volume* measured over prior sessions. A POV algorithm targeting 20% of *today's* volume can breach a 25% ADV limit outright on a quiet day.
- **A negative or corrected volume tick.** Feeding one in shrinks the cumulative denominator and inflates every participation number computed from it. Reject the tick and resynchronise; do not net it off.
- **Silently clamping a misconfigured rate.** Rewriting a 40% target down to a 30% cap produces an execution nobody authorised and no record that it happened. Raise instead.
- **Floating-point flooring.** At $R = 1/3$, `R/(1-R)` evaluates to `0.49999999999999994`, so a naive floor loses a share at every exact-ratio boundary. The engine applies a tolerance before flooring.

## Verification

- Instantiate `ParticipationOfVolumePovExecutionEngine` with a 1,000-share parent at `target_rate=0.15`, `max_slice_qty=500`. Feed 1,000 away shares $\implies$ slice $= \lfloor 0.15/0.85 \times 1000 \rfloor = 176$, `working_qty` $= 176$, `filled_qty` $= 0$, `realized_participation_rate` $= 0.0$. Feed 5,000 more $\implies$ cumulative target $\lfloor 1058.82 \rfloor = 1058$, deficit 882, clamped to the 500-share `max_slice_qty`.
- Drift recovery: with `min_slice_qty=100` and 100 away shares per interval, the first five intervals return 0 (`VOLUME_PAUSED`) and the sixth returns 105 once the cumulative target $\lfloor 0.17647 \times 600 \rfloor$ clears the minimum. A per-interval implementation returns 0 forever.
- Own-volume netting: under `VolumeBasis.CONSOLIDATED`, 1,000 then 1,176 tape shares (the second including 176 own prints) accumulate to 2,000 away shares. The same inputs read as `AWAY` accumulate to 2,176 and schedule 208 rather than 176 — the over-participation this setting prevents.
- Over-fill backstop: fill 60,000 against a 176-share child order $\implies$ `overfill_qty` $= 59{,}824$ and the next update returns 0 with status `RATE_CAPPED`.
- Negative checks: `target_rate` of 0, 1.0 or above `max_rate`; `min_slice_qty > max_slice_qty`; a non-positive `total_qty`; a side outside BUY/SELL; a negative, fractional or non-finite volume tick; a non-positive price; a release larger than the working quantity — each must raise.
- Run `python -m unittest discover -s skills/participation-of-volume-pov-execution/scripts` and confirm a 100% pass rate.

## Related Skills

- `execution-algo-twap-vwap-slicing`
- `implementation-shortfall-minimization`
- `multi-day-execution-schedules-for-very-large-orders`
- `close-auction-participation-strategy`
- `execution-algorithm-kill-switch-integration`
- `execution-algo-behavior-under-halted-instrument`
- `order-placement-idempotency`
- `minimum-fill-size-and-lot-rounding-logic`
- `transaction-cost-analysis-tca-integration`

# Workflows for Liquidity Seeking Across Lit and Dark Venues

Scope: US NMS stocks. This is a **pre-trade planner** — every quantity it emits
is a projection under a deterministic expected-fill model, not a broker fill.

## 0. Validate inputs before pricing anything

- Reject a `side` that is not `BUY` or `SELL` after normalisation. Defaulting an
  unrecognised side into the `else` branch sells what was meant as a purchase.
- Reject a `historical_fill_rate` outside $[0, 1]$: a rate above 1.0 projects a
  fill larger than the quantity routed and over-fills the parent.
- Reject non-positive prices, non-integer or non-positive quantities, NaN/Inf,
  a venue whose own bid exceeds its own ask, and duplicate `venue_id`s
  (duplicates double-count liquidity).

## 1. Synthetic NBBO and midpoint

- $P_{\text{NBB}} = \max$ bid over lit venues **with `bid_qty > 0`**;
  $P_{\text{NBO}} = \min$ ask over lit venues **with `ask_qty > 0`**. A quote
  displaying no size is not a quotation and must not set a touch.
- $P_{\text{mid}} = (P_{\text{NBB}} + P_{\text{NBO}}) / 2$.
- **Crossed** ($P_{\text{NBB}} > P_{\text{NBO}}$) → raise and re-snapshot. The
  midpoint of a crossed book sits outside both touches; cf. 17 CFR 242.610(e),
  which requires SRO rules against displaying locking or crossing quotations.
- **Locked** ($P_{\text{NBB}} = P_{\text{NBO}}$) → warn and continue. The
  midpoint equals both touches, so the dark stage yields no improvement.
- This NBBO is *synthetic*. The official NBBO comes from the SIP under the
  CTA/UTP plans, and protected status also requires the quote be automated and
  immediately accessible — neither is modelled here.

## 2. Stage 1 — dark ATS midpoint sweep

1. **Limit gate, both sides.** If the parent limit does not permit execution at
   $P_{\text{mid}}$ — $P_{\text{mid}} > \text{limit}$ for a BUY, or
   $P_{\text{mid}} < \text{limit}$ for a SELL — skip the entire stage and record
   `LIMIT_PRICE_THROUGH_MIDPOINT`. Do not raise: the lit stage may still be able
   to work the order.
2. Order dark venues by descending `historical_fill_rate`.
3. Per venue, $Q_{\text{route}} = \min(Q_{\text{rem}}, \text{venue liquidity})$.
   Skip with `PING_BELOW_MIN_DARK_FILL_QTY` when
   $Q_{\text{route}} < Q_{\text{min\_dark}}$ — the gate is on the **routed**
   quantity, not the venue's depth.
4. Project $\lfloor Q_{\text{route}} \times \text{FillRate} \rfloor$, capped at
   $Q_{\text{rem}}$. If it falls below $Q_{\text{min\_dark}}$, record
   `EXPECTED_FILL_BELOW_MIN_QTY` and treat it as **no fill** — IOC + `MinQty`
   executes at least MinQty or not at all.
5. Emit the child with `price_instruction="MIDPOINT_PEG"`
   (`ExecInst(18)='M'` / `PegPriceType(1094)=2`) and
   $\text{MinQty} = \min(Q_{\text{min\_dark}}, Q_{\text{route}})$, so MinQty can
   never exceed the routed quantity. **Do not transmit the midpoint as a limit
   price** — a one-cent spread midpoints to a half cent, and Rule 612 bars
   accepting a sub-penny-priced order even though the sub-penny *execution*
   itself is permitted.
6. Signed improvement per share versus the far touch:
   $P_{\text{NBO}} - P_{\text{exec}}$ (BUY) or $P_{\text{exec}} - P_{\text{NBB}}$
   (SELL). Signed, not absolute — a route that executes worse than the touch
   must report a negative number.

## 3. Stage 2 — lit sweep in strict price priority

1. Sort lit venues by execution price: ascending `ask_price` for a BUY,
   descending `bid_price` for a SELL. **Never** sweep in the order the venues
   arrived in — that is how a protected quotation gets traded through while
   every child still looks inside its own limit.
2. Skip venues with no displayed size, and venues whose price breaches the
   parent limit. Never reprice to reach a venue.
3. Fill $\min(Q_{\text{rem}}, \text{displayed size})$.
4. Flag `requires_iso_marking` when the price is inferior to the protected NBBO.
   Price priority makes the Rule 611(b)(5)-(6) precondition hold structurally:
   an inferior venue is only reached once the **full displayed size** of every
   better-priced protected quotation has been taken. That child must be
   identified as an ISO (`ExecInst(18)='f'`) and routed simultaneously with the
   better-priced orders — the marking does not create the exception by itself.

## 4. Audit report

- Assert `total_executed_qty + unfilled_qty == target_quantity` before
  returning; a broken invariant is a bug, not a rounding artefact.
- Carry `nbbo_bid` / `nbbo_ask`, `fill_model`, `requires_iso_marking`, and every
  `dark_skip_reasons` entry, so the sequencing decision is reconstructable after
  the fact.
- Treat `requires_iso_marking=True` and any non-empty `dark_skip_reasons` as
  events the caller must handle, not as log noise.

## 5. Close the loop

Reconcile projected fills against realised executions and feed the measured
filled/sent ratio back into `historical_fill_rate`. Without that feedback, dark
fill-rate decay is invisible: the projection keeps allocating to a venue that
stopped filling. Route markouts into `adverse-selection-measurement-for-passive-orders`
and venue scoring into `dark-pool-routing-logic`.

# Pre-Flight Checklist

Scope: US NMS stocks. Sign off before this sequencing logic works a live parent.

## Market data integrity

- [ ] Is the NBBO built only from lit venues **quoting size**, so a zero-size quote cannot set a touch?
- [ ] Is a **crossed** book ($P_{\text{NBB}} > P_{\text{NBO}}$) rejected and re-snapshotted rather than midpointed?
- [ ] Is a **locked** book surfaced, given the midpoint then equals both touches and the dark stage buys nothing?
- [ ] Is it understood that this NBBO is *synthetic* — the official NBBO is the SIP's, and protected status also requires the quote be automated and immediately accessible?
- [ ] Are venue books validated at ingest (finite positive prices, own bid $\le$ own ask, non-negative integer sizes, fill rate in $[0,1]$, no duplicate `venue_id`)?

## Order safety

- [ ] Is an unrecognised `side` **rejected**, never defaulted into the SELL branch?
- [ ] Does the parent limit bind on **every** stage, symmetrically on both sides — including the dark midpoint?
- [ ] Is a stage the limit does not permit **skipped with a recorded reason**, rather than repriced or raised?
- [ ] Does `total_executed_qty + unfilled_qty == total_requested_qty` hold on every report, with no negative residual?

## Dark stage

- [ ] Is $Q_{\text{min\_dark}}$ gated on the **routed quantity**, not on the venue's available liquidity? (A deep pool does not make a 300-share ping safe.)
- [ ] Is a projected fill below $Q_{\text{min\_dark}}$ treated as **no fill**, matching IOC + `MinQty(110)` semantics?
- [ ] Does every dark child satisfy $\text{MinQty} \le Q_{\text{route}}$ — no order that can never trade?
- [ ] Is the child sent as a **midpoint peg** (`ExecInst(18)='M'` / `PegPriceType(1094)=2`) rather than as an explicit — possibly sub-penny — limit price?
- [ ] Is $Q_{\text{min\_dark}}$ calibrated on your own venue minimums and realised fills, not left at the 500-share engineering default?

## Lit stage (Reg NMS)

- [ ] Are lit venues swept in **strict price priority**, never in the order they arrived in the input list?
- [ ] Is every child priced inferior to the protected NBBO flagged, and is it actually marked `ExecInst(18)='f'` downstream?
- [ ] Are the Rule 611(b)(5)-(6) conditions genuinely met — simultaneous routing against the **full displayed size** of every better-priced protected quotation — rather than assumed from the ISO marking?
- [ ] Is it clear that Rule 611 binds *trading centers* while FINRA Rule 5310 best execution binds the router, so `requires_iso_marking` is an engineering flag and not a compliance determination?
- [ ] Has the pending SEC proposal to rescind Rule 611 and Rule 610(e) (Release 34-105655, 11 Jun 2026, comments closed 17 Aug 2026 — still a **proposal**) been re-checked for finalisation?

## Model honesty

- [ ] Is it understood that report quantities are **projections** under `DETERMINISTIC_EXPECTED_FILL`, not broker-reported fills?
- [ ] Are projections reconciled against realised executions, with the measured filled/sent ratio fed back into `historical_fill_rate` on a defined cadence?
- [ ] Is a non-empty `dark_skip_reasons` treated as an event the caller handles, not as log noise?
- [ ] Are idempotency, timeout, retry, and order-state handling supplied by the layer that actually sends orders — since none of them exist here?

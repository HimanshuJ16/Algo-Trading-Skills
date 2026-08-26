# Pre-Flight Checklist — Latency-Arbitrage Defensive Order Sizing

## Calibration (do this before anything else)
- [ ] Has `lambda_scaling` been calibrated from **realized markouts on your own passive fills**, rather than inherited from the 0.50 placeholder?
- [ ] Is the implied quoting cut-off known and intended? At the defaults the engine fully cancels above **6.93 ms** at $\sigma = 0.20$ and above **1.73 ms** at $\sigma = 0.80$.
- [ ] Is that cut-off actually above your venue's measured cancel path — i.e. will the engine ever quote at all?
- [ ] Is $\lambda$ refit per liquidity tier rather than shared across a mixed universe?
- [ ] Are the calibration date, sample window, and instrument set recorded?

## Measurement inputs
- [ ] Is $\Delta\tau$ measured as lead-event-to-sweep **minus** cancel delivery, on one synchronized clock domain?
- [ ] Is a measured **high percentile** fed in, not a mean? (Pick-off risk lives in the tail.)
- [ ] Is a negative $\Delta\tau$ passed through as zero exposure rather than clamped to an error?
- [ ] Is a stale or dropped probe passed through as-is, with **no** last-known-good or zero substituted?
- [ ] Is `spread_bps` the true baseline spread, and `volatility_annualized` current for this instrument?

## Fail-closed behaviour
- [ ] Does a non-finite `latency_gap_ms` produce `INVALID_INPUT_CANCEL` with size 0 — **never** `QUOTE_DEFENSIVELY_SIZED` at full size?
- [ ] Does a non-finite or negative `volatility_annualized` do the same?
- [ ] Is the `ERROR` log on the fail-closed path routed somewhere a human sees it? A quote silently cancelled all session is an outage, not a defence.
- [ ] Do structural errors (`base_quote_qty`, `min_lot_size`, `lot_increment`, `spread_bps`, `symbol`) raise on construction rather than becoming a cancel?

## Lot and size logic
- [ ] Is `min_lot_size` the instrument's **actual** round lot, not a hard-coded 100? For NMS stocks it is price-tiered 100 / 40 / 10 / 1 (17 CFR 242.600(b)(93)) — use `round_lot_for_nms_price()`.
- [ ] Is `lot_increment` set to the venue's real size increment?
- [ ] Are sizes floored, never rounded up?
- [ ] Is the cancel threshold understood as **inclusive** ($P_{\text{snipe}} \ge \theta$ pulls the quote)?
- [ ] Is it understood that a surviving odd lot is odd-lot information (Rule 600(b)(69)), not a protected quotation?

## Spread handling
- [ ] Is `defensive_spread_bps` consumed from the report rather than recomputed by the caller?
- [ ] Is it understood that $P_{\text{snipe}}$ is **spread-independent**, so the widening's risk reduction is *not* already banked in the score?

## EU market making agreement (if one applies)
- [ ] Has `breaches_comparable_size_one_sided` been checked before sending, or is the same reduction applied to both sides? (RTS 8 Art. 1(2)(c): sizes must not diverge by more than 50%.)
- [ ] Is cumulative cancelled time tracked against the Art. 1(1)(b) **50% of continuous trading hours** presence floor?
- [ ] Do the defensive size and spread stay inside the minimum presence/size/spread obligations written into the agreement (Art. 2(1)(b))?
- [ ] Is it understood that elevated sniping risk is **not** an Art. 3 exceptional circumstance, and that only the venue publishes Art. 3(b)/(c)/(e) events?
- [ ] Is the cancel traffic budgeted against the order-to-trade ratio?

## Interpretation and governance
- [ ] Is $P_{\text{snipe}}$ documented downstream as an **ordinal risk score on a proxy hazard**, never as a calibrated probability, and never used to price anything?
- [ ] Is it understood that this module conditions on **losing** the cancel race and does not model it?
- [ ] Is the cancel-race model itself covered elsewhere (`cross-venue-latency-arbitrage-defensive-design`)?
- [ ] Is every `DefensiveSizingReport` retained, cancels included, so decisions are reviewable?
- [ ] Is it understood that this module returns directives only and never sends, amends, or cancels an order?

# Pre-Flight Checklist — IV Surface Construction

## Quote hygiene
- [ ] Is spot adjusted for discrete cash dividends paid before expiry?
- [ ] Are the quotes European, or has the American early-exercise premium been accounted for?
- [ ] Is every quote strictly inside the no-arbitrage price bounds — above intrinsic and below $Se^{-q\tau}$ (call) / $Ke^{-r\tau}$ (put)?
- [ ] Have quotes flagged as poorly identified (near-zero vega) been excluded from the fit rather than left to pull the wings?

## Inversion
- [ ] Is the solver bracketed (bisection), not bare Newton-Raphson on the raw price?
- [ ] Does an out-of-bounds quote raise rather than return a clamped volatility?
- [ ] Does a round trip price $\to$ IV $\to$ price recover the input over the moneyness range actually used?

## Calibration
- [ ] Is each smile fitted to **one** expiration only?
- [ ] Do the quotes span at least three distinct moneyness levels?
- [ ] Is the RMS residual small enough that a quadratic actually describes this chain?
- [ ] Is the fitted $\sigma_{\text{ATM}}$ positive?

## Arbitrage audit — the point of this skill
- [ ] Is calendar spread arbitrage audited at **fixed log-forward moneyness** $k = \ln(K/F_\tau)$, **not** at fixed strike?
- [ ] Is the dividend yield $q$ carried into the forward, not just into the pricing?
- [ ] Is total implied variance $w = \sigma^2\tau$ non-decreasing in $\tau$ at every audited $k$?
- [ ] Is butterfly convexity audited with **spacing weights** $(K_3-K_2)/(K_3-K_1)$ and $(K_2-K_1)/(K_3-K_1)$, not $0.5/0.5$?
- [ ] Was the audit re-run **after** calibration? Slice-by-slice fits carry no calendar guarantee.

## Reading the report
- [ ] Did both audits actually run — `calendar_audit_performed` and `butterfly_audit_performed` both `True`?
- [ ] Is the conclusion taken from `is_arbitrage_free`, rather than from "no violations were listed"?
- [ ] Are there clamp warnings in the log? If so, the wings are unaudited and the report is inconclusive there.
- [ ] Is the grid dense enough that a clean result is meaningful evidence rather than a sparse sample?

## Model limits acknowledged
- [ ] Is the surface being evaluated only within the moneyness range the quotes cover, given that a quadratic smile violates Lee's linear-growth bound in the far wings?
- [ ] Is it understood that this is a quadratic moneyness smile, **not** SVI?
- [ ] Are IVs left unrounded until the presentation layer, so rounding cannot flip a marginal arbitrage comparison?

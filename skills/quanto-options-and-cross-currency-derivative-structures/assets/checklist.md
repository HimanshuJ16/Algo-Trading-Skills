# Pre-Flight Checklist

## Conventions (get these wrong and the price is plausible but wrong)

- [ ] Is $\rho$ estimated against the FX rate quoted **domestic per foreign**? If it came from the inverted series, has it been negated?
- [ ] Is the strike expressed in the **foreign** asset's units (same as spot), not domestic? If the term sheet fixes it in domestic currency, has it been divided by $F_X$?
- [ ] Is this actually a **quanto** (fixed conversion rate) and not a **composite/compo** (converts at prevailing spot, strike fixed in domestic currency)?
- [ ] Is $F_X$ the contractual fixed rate from the term sheet, not today's spot?

## Model inputs

- [ ] Is the foreign asset drift adjusted by $-\rho\sigma_S\sigma_X$, i.e. $\mu = r_f - q - \rho\sigma_S\sigma_X$?
- [ ] Is the **domestic** rate $r_d$ used for discounting and nowhere else, and the **foreign** rate $r_f$ used for drift and nowhere else?
- [ ] Are all rates and volatilities continuously compounded and annualized?
- [ ] Is $\rho \in [-1, 1]$, $\sigma_S > 0$, $\sigma_X \ge 0$, $S, K, T, F_X > 0$, and every field finite?
- [ ] Does an unrecognized `option_type` raise rather than default to a put?

## Greeks

- [ ] Does the reported Vega include the **drift channel** $(\partial V/\partial\mu)(-\rho\sigma_X)$, not just the Black-Scholes term?
- [ ] Do call and put Vega **differ**? (Equal values mean the drift channel is missing.)
- [ ] Is $\partial V/\partial\rho$ **negative for calls and positive for puts** before it is aggregated across the book?
- [ ] Have the Greeks been checked against finite differences of the price at both positive and negative $\rho$?

## Risk management

- [ ] Is $\partial V/\partial\rho$ re-marked against a **stressed** correlation, not only the trailing estimate?
- [ ] Has the size of the adjustment $\rho\sigma_S\sigma_X T$ been checked against the tenor? It scales with $T$ and is material on multi-year structures.
- [ ] Are Greeks quantized only at the presentation layer, with full precision preserved through the risk pipeline?
- [ ] Is the European-exercise, flat-volatility, constant-correlation scope acceptable for how this number will be used?

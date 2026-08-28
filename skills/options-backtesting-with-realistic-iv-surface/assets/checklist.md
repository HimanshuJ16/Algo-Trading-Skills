# Pre-Flight / Sign-off Checklist — options-backtesting-with-realistic-iv-surface

Use this before considering the skill's implementation complete.

## Surface

- [ ] **Tenor anchoring:** $\alpha$ and $\beta$ were calibrated at the 30-day reference tenor, and the tenor is recorded next to the values.
- [ ] **Skew present across strikes:** OTM puts ($K/S < 1$) carry IV above ATM; $\sigma(0.90, 30\text{d}) = 0.235$ for $\alpha=-0.30$, $\beta=0.50$, $\sigma_{\text{atm}}=0.20$.
- [ ] **Skew decays across expirations:** the same $10\%$-OTM put shows a materially smaller offset at 2 years than at 1 week; $s(4T_{\text{ref}}) = 0.5$.
- [ ] **No double-counted term decay:** either $\alpha/\beta$ are anchored at one tenor with $\gamma > 0$, or they are fitted per expiration with `skew_term_decay=0.0` — not both.
- [ ] **No clamp warnings in the strike universe traded:** any `MIN_STRIKE_IV`/`MAX_STRIKE_IV` clamp logged means the smile is being extrapolated, not quoted.

## Pricing

- [ ] **Black-Scholes benchmark:** flat smile, $S=42$, $K=40$, $r=0.10$, $\sigma=0.20$, $T=0.5$ gives call $\$4.76$ / put $\$0.81$ (Hull).
- [ ] **Put-call parity:** $C - P = Se^{-qT} - Ke^{-rT}$ to at least 10 decimals, on the skewed surface and with $q > 0$.
- [ ] **No price flooring or rounding:** a deep-OTM option returns a value below one tick, not a synthetic minimum; quantization happens at the fill-simulation layer.
- [ ] **Dividends handled:** continuous $q$ set, and discrete cash dividends removed from $S$ before pricing.
- [ ] **Both legs of a spread priced on the same strike IV.**

## Greeks

- [ ] **Analytic vs numerical:** $\Delta$, $\Gamma$, $\Theta$, $\nu$ each match a central finite difference of the price function.
- [ ] **Units recorded:** $\Theta$ per calendar day, $\nu$ per 1 volatility point — confirmed against whatever consumes them.
- [ ] **Skewed IV used for the Greeks too**, not $\sigma_{\text{atm}}$.

## Robustness

- [ ] **Invalid input raises:** non-finite $S$/$K$/$T$/$\sigma$/$q$, $S \le 0$, $K \le 0$, $\sigma \le 0$, $T < 0$, and any `option_type` other than `CALL`/`PUT`.
- [ ] **Unknown option type is rejected, not coerced:** `"C"` must raise, not return a put.
- [ ] **Expiry settles at intrinsic:** $T=0$ returns the terminal payoff, `is_expired=True`, zero gamma/theta/vega.

## Scope

- [ ] **Exercise style confirmed:** the instruments backtested are European, or the early-exercise premium is accounted for elsewhere.
- [ ] **Skew drag quantified:** the backtest was re-run with $\alpha=\beta=0$ and the P&L difference reported alongside the headline return.

## Testing

- [ ] **Automated Testing:** Run `python scripts/test_options_iv_backtester.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

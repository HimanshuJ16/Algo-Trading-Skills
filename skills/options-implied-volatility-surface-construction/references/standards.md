# Standards for Options Implied Volatility Surface Construction

## Static no-arbitrage conditions

These are results from the derivatives-pricing literature, not regulatory
requirements. No regulator prescribes an implied volatility surface
parameterization; what follows is mathematics, and it holds or fails
independently of jurisdiction.

| Quantity | Definition |
|---|---|
| Forward | $F_\tau = S e^{(r-q)\tau}$ (continuous dividend yield $q$). |
| Log-forward moneyness | $k = \ln(K / F_\tau)$. The coordinate the calendar condition is stated in. |
| Spot moneyness | $m = K / S$. The coordinate this engine's smile is parameterized in. Not interchangeable with $k$. |
| Total implied variance | $w(k, \tau) = \sigma_{\text{BS}}(k, \tau)^2 \, \tau$. |

| Condition | Statement | Source |
|---|---|---|
| Calendar spread arbitrage | A volatility surface is free of calendar spread arbitrage **iff** $\partial_\tau w(k, \tau) \ge 0$ for all $k \in \mathbb{R}$ and $\tau > 0$, assuming dividends proportional to the stock price. The proof compares options at equal forward moneyness, $K_1/F_{\tau_1} = K_2/F_{\tau_2}$. | Gatheral & Jacquier, *Arbitrage-free SVI volatility surfaces*, Quantitative Finance 18(6), 2014 — Lemma 2.1 and Definition 2.2. arXiv:1204.0646 |
| Butterfly arbitrage | A slice is free of butterfly arbitrage **iff** the risk-neutral density is non-negative, equivalently call prices are decreasing and convex in strike. The density follows from $p(k) = \partial^2 C/\partial K^2$. | Gatheral & Jacquier, Definition 2.3 and Lemma 2.2; density identity from Breeden & Litzenberger, *Prices of State-Contingent Claims Implicit in Option Prices*, Journal of Business 51(4), 621–651, 1978. |
| Analytic butterfly test | $g(k) := \left(1 - \frac{k w'(k)}{2 w(k)}\right)^2 - \frac{w'(k)^2}{4}\left(\frac{1}{w(k)} + \frac{1}{4}\right) + \frac{w''(k)}{2} \ge 0$ for all $k$, together with $\lim_{k \to +\infty} d_+(k) = -\infty$, where $d_\pm(k) = -k/\sqrt{w(k)} \pm \sqrt{w(k)}/2$. | Gatheral & Jacquier, equation (2.1) and Lemma 2.2. |
| Extreme-strike bound | $\sigma_{\text{BS}}^2(k, \tau) \le \beta \lvert k \rvert / \tau$ with $\beta \in [0, 2]$: total implied variance may grow **at most linearly** in $\lvert k \rvert$. $\beta_R = 2$ iff the underlying has no finite moments of order $> 1$; $\beta_R = 0$ iff it has finite moments of all orders. | Lee, *The Moment Formula for Implied Volatility at Extreme Strikes*, Mathematical Finance 14(3), 469–480, 2004. |
| Pricing model | European Black-Scholes-Merton with continuous yield $q$: $C = Se^{-q\tau}N(d_1) - Ke^{-r\tau}N(d_2)$, $d_1 = [\ln(S/K) + (r - q + \sigma^2/2)\tau]/(\sigma\sqrt{\tau})$. | Merton, *Theory of Rational Option Pricing*, Bell Journal of Economics and Management Science 4(1), 1973. |
| Inversion robustness | Newton-like iterations on the raw price are ill-conditioned across the full domain: deep OTM prices underflow, near-ITM prices lose time value, and prices near the upper bound are poorly conditioned in volatility. Production solvers use normalized coordinates with rational seeding rather than plain Newton-Raphson. | Jäckel, *Let's Be Rational*, Wilmott 2015. |

## Engine conventions (implementation choices, not standards)

These are decisions made by this engine. They are not derived from a regulator,
an exchange, or a published result, and should be tuned or removed with the
model rather than cited as authority.

| Convention | Value | Rationale |
|---|---|---|
| Strike IV clamp | $[0.05, 3.00]$ | The quadratic smile is unbounded in both directions and produces negative volatilities in the wings. A numerical guard. Every binding clamp is logged because a clamped wing is no longer the parametric surface. |
| IV solver bracket | $[10^{-6}, 5.0]$ | Bisection bracket. A quote implying more than 500% volatility is reported as out of range rather than clamped to the ceiling. |
| IV conditioning warning | $\sigma$ resolution $> 10^{-6}$ | Estimated as (float64 price resolution) / vega. Separates well-conditioned quotes (~$10^{-16}$) from degenerate ones (~$10^{-2}$). |
| Calendar tolerance | $10^{-12}$ absolute, in variance units | Floating-point slack. $w$ is order $10^{-2}$ to $10^{0}$; a violation this small is not tradeable. Not an economic threshold. |
| Butterfly tolerance | $10^{-10}\times$ spot | Same intent, scaled to the price level. At spot 100 this is $10^{-8}$ dollars — eight orders of magnitude below one cent. |

## Scope limits

- The parameterization here is a **quadratic in spot moneyness**, not SVI. The
  arbitrage *conditions* above come from the SVI literature; the model does not.
- The audits check the finite grid supplied, not all $k \in \mathbb{R}$ and
  $\tau > 0$. A clean report is evidence, not a proof.
- Calendar spread theory as stated assumes **proportional** dividends. Under
  discrete cash dividends the forward-moneyness argument does not hold as
  written.
- European exercise only. Inverting an American price through this formula
  returns a volatility contaminated by the early-exercise premium.

# Workflows for Quanto Options and Cross-Currency Derivative Structures

Notation: $S$, $K$ in the **foreign** asset's currency; $F_X$ the contractual
domestic-per-foreign multiplier; $r_d$ domestic rate (discounting only); $r_f$
foreign rate (drift only); $q$ continuous dividend yield; $\sigma_S$ asset
volatility; $\sigma_X$ FX volatility; $\rho$ the correlation between the foreign
asset's return and the return on the FX rate quoted **domestic per foreign**.

## 1. Normalize inputs and confirm conventions

1.1. **Confirm the FX quoting direction behind $\rho$.** If the correlation was
estimated against a foreign-per-domestic series, negate it. Nothing downstream
detects this — a wrong-signed $\rho$ produces a plausible price with the drift
adjustment applied backwards, a $2\rho\sigma_S\sigma_X$ error.

1.2. **Convert the strike to foreign units** if the term sheet fixes it in domestic
currency: $K_{\text{foreign}} = K_{\text{domestic}} / F_X$.

1.3. **Validate at the boundary and raise, do not clamp or retry.** Required:
$S, K, T, \sigma_S, F_X > 0$; $\sigma_X \ge 0$; $\rho \in [-1, 1]$; all fields
finite; `option_type` exactly `CALL` or `PUT` after trim/upper. Two of these are
non-obvious:

- Comparisons against NaN are always false, so a `value <= 0` guard passes NaN
  straight through and every field of the resulting report is NaN while its status
  still reads `QUANTO_PRICING_SUCCESSFUL`.
- $F_X \le 0$ flips the sign of the price and every Greek. It is a contractual
  multiplier, not a market variable.

$\sigma_X = 0$ (hard peg) and $\rho = \pm 1$ are legitimate and must be accepted.

## 2. Quanto drift and forward

2.1. $\mu_{\text{quanto}} = r_f - q - \rho\,\sigma_S\,\sigma_X$.

2.2. Quanto forward $F = S\,e^{\mu_{\text{quanto}}T}$, in foreign units.

2.3. **Keep the rate roles separate.** $r_d$ appears only in $e^{-r_dT}$. $r_f$
appears only in $\mu_{\text{quanto}}$, and therefore inside $d_1$. Swapping them
leaves the price positive and near the money, so no plausibility check catches it.

## 3. Price the European payoff

3.1. $d_1 = \dfrac{\ln(S/K) + \left(\mu_{\text{quanto}} + \tfrac{1}{2}\sigma_S^2\right)T}{\sigma_S\sqrt{T}}$, $d_2 = d_1 - \sigma_S\sqrt{T}$.

3.2. $V_{\text{call}} = F_X e^{-r_dT}\left[F\,N(d_1) - K\,N(d_2)\right]$.

3.3. $V_{\text{put}} = F_X e^{-r_dT}\left[K\,N(-d_2) - F\,N(-d_1)\right]$.

3.4. Branch on a *validated* option type. `if type == "CALL": ... else: <put>`
prices `'CAL'`, `'C'` and `''` as puts.

## 4. Greeks

4.1. Delta $= +F_X e^{-r_dT}e^{\mu T}N(d_1)$ (call), $-F_X e^{-r_dT}e^{\mu T}N(-d_1)$ (put).

4.2. Gamma $= F_X e^{-r_dT}e^{\mu T} n(d_1) / (S\sigma_S\sqrt{T})$, identical for both sides.

4.3. **Drift sensitivity, the quantity both quanto-specific Greeks are built from:**

$$\frac{\partial V}{\partial\mu} = \begin{cases} +F_X e^{-r_dT} S e^{\mu T} T\,N(d_1) & \text{call} \\ -F_X e^{-r_dT} S e^{\mu T} T\,N(-d_1) & \text{put} \end{cases}$$

Its sign is why the two Greeks below differ between call and put.

4.4. **Vega has two channels**, because $\sigma_S$ enters $d_1/d_2$ *and* the drift:

$$\frac{\partial V}{\partial\sigma_S} = \underbrace{F_X e^{-r_dT} F\, n(d_1)\sqrt{T}}_{\text{spot channel}} + \underbrace{\frac{\partial V}{\partial\mu}\cdot(-\rho\sigma_X)}_{\text{drift channel}}$$

Report the components separately. **Equal call and put vega means the drift channel
is missing** — that equality is a Black-Scholes property and is false here.

4.5. **Correlation sensitivity**, $\partial\mu/\partial\rho = -\sigma_S\sigma_X$:

$$\frac{\partial V}{\partial\rho} = \frac{\partial V}{\partial\mu}\cdot(-\sigma_S\sigma_X)$$

Negative for a call, **positive** for a put. Higher $\rho$ lowers the drift, which
cheapens the call and enriches the put. Sign the two sides separately before
aggregating a book, or hedged positions read as doubled ones.

## 5. Report

5.1. Emit `QuantoOptionPricingReport`: drift, quanto forward, $d_1$, $d_2$, price,
Delta, Gamma, total Vega with both components, $\partial V/\partial\rho$, status,
and audit notes.

5.2. **Do not round inside the engine.** Gamma on an index-level underlying is
$O(10^{-5})$; rounding to 6 decimals leaves one significant figure. Quantize at the
presentation layer, where the notional is known.

5.3. Re-mark $\partial V/\partial\rho$ against a stressed correlation, not only the
trailing estimate. $\rho$ is the least stable input in this model and inverts in the
regimes where the book is largest.

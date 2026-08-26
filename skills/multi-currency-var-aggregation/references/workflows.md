# Deep Workflow Reference — multi-currency-var-aggregation

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 0. Prepare and validate the inputs

- **Align every series to the same observation dates** before calling. The engine can
  only check that lengths match; it cannot check that index 17 is the same trading day
  in the Tokyo asset series and the JPY FX series. A one-day misalignment across a
  session boundary silently decorrelates asset and FX and understates the joint risk.
- **End every series at the last completed period** before the valuation date. There is
  no look-ahead protection in the module.
- **Confirm the FX quoting direction.** `fx_rate_to_base` is base units per one native
  unit: base USD, EUR position, EUR/USD = 1.10 $\Rightarrow$ `1.10`. The FX return
  series must be the return of that same rate,
  $E_t(c \rightarrow \text{base}) / E_{t-1}(c \rightarrow \text{base}) - 1$. An inverted
  quote negates every FX return and no validation can detect it — check one known move
  by hand.
- **Reject non-finite data.** A single `NaN` propagates to a `NaN` VaR that still
  reports `VAR_CALCULATION_SUCCESS`.

### 1. Value every position in the base currency

$$V_{\text{base},i} = Q_i \cdot P_{\text{native},i} \cdot E(c_i \rightarrow \text{base})$$

- A negative $Q_i$ is a short and is supported; a negative *price* is a data error and
  raises.
- Base-currency positions must carry `fx_rate_to_base == 1.0` exactly. A base-currency
  position with a non-unit rate means the base currency was misidentified.
- Keep positions distinct even when they share a `symbol`. Two lots are two exposures;
  collapsing them by symbol drops everything after the first.

### 2. Synthesise the joint base-currency return series

$$R_{\text{base},i,t} = (1 + R_{\text{native},i,t})(1 + R_{\text{FX},c_i,t}) - 1$$

This is not an approximation — it follows from the fact that base-currency value is a
*product* of price and rate. Asset-FX correlation is therefore captured inside the
series and needs no separate input.

- Compute it as $r_n + r_{fx} + r_n r_{fx}$ to avoid adding and subtracting 1.0.
- A missing FX series for a non-base currency **raises**. It is not zero-filled: that
  would turn a foreign position into a domestic one and remove the risk being measured.
- The base currency's series may be omitted (identically zero). If supplied, it must be
  all zeros — a non-zero base-on-base series means the quoting direction or the declared
  base currency is wrong.

### 3. Aggregate to a base-currency P&L series

$$\text{PnL}_t = \sum_i V_{\text{base},i} \cdot R_{\text{base},i,t}$$

Aggregate on **values**, not weights. The weight form $V_{\text{total}} \sum_i w_i R_i$
is algebraically identical for an ordinary long-only book, but it divides by net
portfolio value — which is approximately zero for a currency-hedged or market-neutral
cross-border book that plainly still carries risk.

Then take the sample moments:

$$\mu_P = \frac{1}{n}\sum_t \text{PnL}_t, \qquad \sigma_P = \sqrt{\frac{1}{n-1}\sum_t (\text{PnL}_t - \mu_P)^2}$$

- Require $n \ge \max(2, \lceil 1/(1-\alpha) \rceil)$ so the tail bucket holds at least
  one observation — 20 at 95%, 100 at 99%.
- Warn below 252 observations: 12 CFR 217.205(b)(2) requires a full year of history for
  a regulatory measure.
- $\sigma_P = 0$ is a data smell (stale or repeated observations), not a risk-free
  portfolio. The engine warns and reports zero parametric VaR rather than dividing by it.

### 4a. Parametric (variance-covariance) VaR

$$\text{VaR}_\alpha = \left(Z_\alpha \sigma_P - \mu_P \cdot \mathbb{1}[\text{drift}]\right)\sqrt{T}$$

- $Z_\alpha$ comes from `statistics.NormalDist.inv_cdf` (Wichura AS241), not a lookup
  table, so any confidence level works. A hard-coded table with a `math.erfinv` fallback
  fails outright — `math.erfinv` does not exist in Python's `math` module.
- Drift is excluded by default: the standard short-horizon convention, and the
  conservative one for a positive-drift book. `subtract_mean_drift=True` switches to
  $Z_\alpha\sigma_P - \mu_P$, which is what makes the parametric and historical measures
  directly comparable.
- $\sqrt{T}$ scaling: see the standards note. Permitted by § 217.205(b)(1), forbidden by
  MAR33.4(5) for the FRTB base horizon.

### 4b. Historical simulation VaR and Expected Shortfall

1. Build the loss series $L_t = -\text{PnL}_t \sqrt{T}$ (positive = loss).
2. Sort worst-first.
3. Take $k = \lceil n(1-\alpha) \rceil$, clamped to $[1, n]$.
4. $\text{VaR}_\alpha = L_{(k)}$, the $k$-th worst loss.
5. $\text{ES}_\alpha = \frac{1}{k}\sum_{j=1}^{k} L_{(j)}$, the mean of the same $k$.

At $n = 100$, $\alpha = 0.95$, $k = 5$: VaR is the 5th worst loss and ES the mean of the
worst five. Using $\lfloor n(1-\alpha) \rfloor$ as a 0-based index gives the 6th worst
loss and averages six — a systematic understatement whenever $n(1-\alpha)$ is an integer,
which is exactly the round-$n$ case. Other conventions exist and differ by one
observation only in that case; state whichever you adopt rather than leaving it implicit.

**Apply the ceiling with an epsilon.** In binary floating point $1 - 0.95 =
0.05000000000000004$, so `ceil(100 * (1 - 0.95))` returns **6** and silently
reintroduces the off-by-one the convention exists to remove.

Because ES averages the same $k$ observations of which VaR is the smallest,
$\text{ES} \ge \text{VaR}$ holds by construction — a useful invariant to assert in tests.

### 5. Per-currency Euler decomposition (Component VaR)

VaR is homogeneous of degree 1 in position values, so Euler's theorem decomposes it with
no residual:

$$\text{CVaR}_i = \sqrt{T}\left(Z_\alpha \frac{V_i (\boldsymbol{\Sigma}\mathbf{V})_i}{\sigma_P} - V_i \mu_i \cdot \mathbb{1}[\text{drift}]\right), \qquad \sum_i \text{CVaR}_i = \text{VaR}_\alpha$$

- $(\boldsymbol{\Sigma}\mathbf{V})_i = \sum_j \text{cov}(R_i, R_j) V_j =
  \text{cov}(R_i, \text{PnL})$, so the marginal term is one covariance against the P&L
  series already computed. Forming the full $m \times m$ covariance matrix yields the
  same numbers at $O(m^2 n)$ instead of $O(mn)$.
- Sum the position components by `native_currency` for the currency view and by `symbol`
  for the instrument view.
- Components can be **negative**: a currency that hedges the book reduces total VaR.
  That is correct and informative, not an error.
- When $\sigma_P = 0$ the risk term is zero and only the drift term (if enabled) remains,
  so the components still reconcile to the total.

### 6. Report

Emit `MultiCurrencyVarReport` with both views clearly separated:

- `currency_risk_breakdown` — net **market value** per currency. This is exposure.
- `currency_component_var_base` — Euler **risk** contribution per currency.

A currency can hold 40% of the book's value and contribute 5% of its risk, or the
reverse. Never present the first as the second. Also carry `observations_used`,
`tail_observations_used`, `holding_period_scaled`, `gross_exposure_base`,
`portfolio_volatility_base` and `portfolio_mean_pnl_base` so a reviewer can reconstruct
every number without re-running the engine.

## Interpreting the output

- **Parametric $\ll$ historical at 99%**: expected. FX returns are heavy-tailed, so the
  normal quantile understates the tail. Treat the gap as a measurement of tail shape.
- **Parametric $\approx$ historical**: consistent with near-normal joint returns over the
  sample — or with a sample too short and too calm to contain a real tail.
- **A currency's component VaR far exceeding its exposure share**: that currency's
  returns are correlated with the rest of the book. Check whether the FX quoting
  direction is right before acting on it.
- **A negative component VaR**: a genuine hedge. Confirm it is not an inverted quote.

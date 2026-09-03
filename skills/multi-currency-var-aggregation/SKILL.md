---
name: multi-currency-var-aggregation
description: >-
  Use when a book holds positions in several currencies and risk must not drop FX
  volatility or asset-FX correlation; aggregates VaR and expected shortfall across both
  risk factors. Linear payoffs only.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, multi-currency, value-at-risk, expected-shortfall, cvar, fx-risk, component-var, parametric-var
  brokers_frameworks: "Variance-Covariance VaR; Historical Simulation VaR; BCBS FRTB (MAR30-MAR33); 12 CFR 217 Subpart F (US market risk rule); Python standard library (statistics, dataclasses)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when measuring portfolio risk across assets denominated in different
currencies (US equities in `USD`, European equities in `EUR`, Japanese equities in
`JPY`). A foreign position carries **two** risk factors, not one: converting it to
the base currency and then applying the asset's own volatility measures only half of
the exposure. The base-currency return of position $i$ held in currency $c$ is the
compounded asset and FX return

$$R_{\text{base},i,t} = (1 + R_{\text{native},i,t})(1 + R_{\text{FX},c,t}) - 1$$

which follows directly from $V_{\text{base}} = Q \cdot P_{\text{native}} \cdot
E(c \rightarrow \text{base})$ — the value is a product, so the return is a product of
gross returns. Asset-FX correlation is therefore captured *inside* the synthesised
series; there is no separate correlation input to get wrong. The module produces
Parametric (variance-covariance) VaR, Historical Simulation VaR, Expected Shortfall
(CVaR), and the per-currency Euler decomposition of the parametric VaR.

## When NOT to Use

- **On options, convertibles, or any convex payoff.** Both branches here are linear:
  position value is assumed proportional to price, and the historical branch revalues
  linearly rather than repricing the instrument. Delta-normal VaR on a short-gamma
  book understates the loss it exists to bound. Use a full-revaluation engine.
- **As a regulatory capital calculation.** The numbers are internal risk measures.
  Notably, `holding_period_days > 1` applies $\sqrt{T}$ scaling, which BCBS
  MAR33.4(5) explicitly forbids for the FRTB base-horizon ES ("without scaling from a
  shorter horizon") even though 12 CFR 217.205(b)(1) permits conversion. Check your
  own supervisor's rule before reporting.
- **On a sample too short to locate the requested quantile.** A 95% historical VaR
  needs at least 20 observations for the tail bucket to hold one; 99% needs 100. The
  engine raises below that rather than returning the single worst observation dressed
  up as a quantile. 12 CFR 217.205(b)(2) requires a full year of history for a
  regulatory measure.
- **When the FX quoting direction is not verified.** See the first pitfall — an
  inverted quote produces a plausible number that is wrong in the dangerous
  direction, and no validation can detect it.
- **For P&L attribution rather than risk.** Splitting realised return into price and
  currency components is `multi-currency-pnl-and-fx-conversion`.

## Prerequisites

- Positions as (`symbol`, `native_currency`, `quantity`, `current_price_native`,
  `fx_rate_to_base`), where `fx_rate_to_base` is **base units per one native unit**
  and is exactly `1.0` for base-currency positions. Negative `quantity` = short.
- `native_symbol_returns`: aligned historical return series per symbol, all the same
  length, all ending at the last **completed** period before the valuation date.
- `fx_returns_to_base`: the return series of that same base-per-native rate, for
  **every** non-base currency in the book. The base currency's own series may be
  omitted (it is identically zero).
- `VarConfig`: `confidence_level` (0.95 / 0.99 / 0.975), `holding_period_days`,
  `base_currency`, optional `subtract_mean_drift` and `min_observations`.

## Workflow

1. **Value every position in the base currency**:
   $$V_{\text{base},i} = Q_i \cdot P_{\text{native},i} \cdot E(c_i \rightarrow \text{base})$$
   - **Decision point — index by position, not by symbol.** Two lots of the same
     instrument are two exposures. Keying return series or weights by symbol lets the
     second lot overwrite the first and the portfolio silently shrinks.

2. **Synthesise the joint base-currency return series** per position by compounding
   asset and FX returns.
   - **Decision point — a missing FX series is an error, not a zero vector.**
     Defaulting an absent series to zeros deletes exactly the currency risk being
     measured and understates VaR with no warning. Only the base currency may be
     absent, and its series must be identically zero if supplied at all.
   - **Decision point — reject non-finite and misaligned data before aggregating.**
     A single `NaN` propagates to a `NaN` VaR that still reports success; a shorter
     FX series silently truncates the sample under `zip`.

3. **Aggregate to a base-currency P&L series** as $\text{PnL}_t = \sum_i V_i \cdot
   R_{\text{base},i,t}$.
   - **Decision point — aggregate on values, not weights.** Weights require dividing
     by net portfolio value, which is near zero for a currency-hedged or
     market-neutral cross-border book. Value aggregation is algebraically identical
     for a long-only book and stays defined for the hedged one.

4. **Compute the risk measures**:
   - **Parametric**: $\text{VaR}_\alpha = Z_\alpha \cdot \sigma_P \cdot \sqrt{T}$,
     with $\sigma_P$ the $(n-1)$ sample standard deviation of the P&L series. Drift
     is excluded by default; `subtract_mean_drift` switches to $Z_\alpha \sigma_P -
     \mu_P$, which is what puts the parametric and historical measures on the same
     footing.
   - **Historical**: sort losses worst-first, take $k = \lceil n(1-\alpha) \rceil$,
     and report the $k$-th worst loss. At $n = 100$, $\alpha = 0.95$ that is the
     **5th** worst loss.
   - **Expected Shortfall**: the mean of those same $k$ worst losses, so
     $\text{ES} \ge \text{VaR}$ by construction.

5. **Decompose per currency (Euler / Component VaR)**:
   $$\text{CVaR}_i = \sqrt{T}\left(Z_\alpha \frac{V_i (\boldsymbol{\Sigma}\mathbf{V})_i}{\sigma_P} - V_i \mu_i\right), \qquad \sum_i \text{CVaR}_i = \text{VaR}_\alpha$$
   - $(\boldsymbol{\Sigma}\mathbf{V})_i = \text{cov}(R_{\text{base},i}, \text{PnL})$,
     so no $m \times m$ matrix is needed.
   - **Decision point — component VaR is not the exposure breakdown.** A currency can
     hold 40% of the book's market value and contribute 5% of its risk. Report both,
     and never present `currency_risk_breakdown` (net exposure) as a risk number.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inverted FX quoting direction**: `fx_rate_to_base` is base per native (EUR/USD =
  1.10 with base USD). Supplying the inverse quote — USD/JPY as JPY-per-USD while the
  base is USD — negates every FX return, so a currency that amplifies the equity
  drawdown is reported as hedging it. The run succeeds; nothing in the data reveals
  it. Verify the direction of every series against a known move.
- **Defaulting a missing FX series to zeros**: this is the single most damaging
  failure mode here, because it fails *quietly and low*. A `.get(currency, [0.0]*n)`
  turns a foreign position into a domestic one and removes the risk the calculation
  exists to find.
- **Off-by-one in the historical quantile**: using $\lfloor n(1-\alpha) \rfloor$ as a
  0-based index selects the $(k{+}1)$-th worst loss whenever $n(1-\alpha)$ is an
  integer — precisely the round-$n$ cases (100 at 95%, 500 at 99%) — and understates
  both VaR and ES. Worse, `ceil(100 * (1 - 0.95))` in binary floating point is
  **6**, not 5, so the fix needs an epsilon or it reintroduces the same bug.
- **Assuming Normal distributions for FX**: FX returns exhibit heavy-tail kurtosis
  ($\kappa > 3$), so parametric VaR understates the 99% level. Report the historical
  and ES numbers alongside it and treat a large parametric-vs-historical gap as a
  tail-shape signal, not noise.
- **Mixing drift conventions**: a parametric VaR of $Z\sigma$ compared against a
  historical VaR that carries the sample drift is a comparison of two different
  measures. Pick one convention for both.
- **Treating $\sqrt{T}$ scaling as free**: it assumes serially independent,
  identically distributed returns. Volatility clustering and autocorrelation break it
  in both directions, and MAR33.4(5) rules it out for the FRTB base horizon entirely.
- **Reading currency exposure as currency risk**: net market value per currency says
  nothing about contribution to VaR. Use the Euler decomposition.
- **Silent single-lot collapse**: keying the joint return series by `symbol` rather
  than by position drops every lot after the first for a duplicated instrument.

## Verification

- Instantiate `MultiCurrencyVarAggregatorEngine`. Feed one $100{,}000 USD position
  with $r_t = -t/10000$ for $t = 1..100$ (losses $10, 20, \dots, 1000$): at 95%
  confidence verify `tail_observations_used == 5`, `historical_var_base == 960.0`
  (the 5th worst loss, **not** 950.0) and `expected_shortfall_cvar_base == 980.0`
  (the mean of the 5 worst).
- Feed a \$1,000,000 USD position with returns alternating $\pm 1\%$ over 100
  periods: $\sigma_P = 10{,}000\sqrt{100/99}$ and parametric VaR
  $= 1.6448536 \cdot \sigma_P$, computed outside the module.
- Verify `_get_z_score(0.975)` returns $1.9599640$ and does not raise — the
  superseded implementation fell through to `math.erfinv`, which does not exist in
  Python's `math` module.
- Verify $\sum_i$ `currency_component_var_base` $=$ `parametric_var_base` on a
  three-currency book, with and without `subtract_mean_drift`.
- Negative checks: a missing FX series for a non-base currency, a non-zero
  base-currency FX series, a base-currency position with `fx_rate_to_base != 1.0`, a
  misaligned series length, a `NaN` return, a 30-observation sample at 99%
  confidence, `holding_period_days = 0`, and `confidence_level = 0.05` must each
  raise `ValueError`.
- Regression checks: two lots of the same symbol must give the same VaR as one
  combined lot, and a market-neutral book with ~zero net value must still produce a
  positive VaR.
- Run `python -m unittest discover -s skills/multi-currency-var-aggregation/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `multi-currency-pnl-and-fx-conversion`
- `multi-asset-backtest-currency-normalization`
- `value-at-risk-var-live-monitoring`
- `real-time-var-backtesting-kupiec-test`
- `correlation-aware-exposure-limits`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`

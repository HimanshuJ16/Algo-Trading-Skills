---
name: strategy-performance-attribution-vs-market-beta
description: >-
  Use when a strategy has outperformed and you need to know whether that was skill or
  unacknowledged factor exposure, regressing excess returns on the market and
  Fama-French factors with inference on the intercept.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: performance-attribution, market-beta, jensens-alpha, capm, fama-french, factor-regression, newey-west
  brokers_frameworks: "Jensen (1968); Fama-French 3-Factor Model (1993); Ken French Data Library; Newey & West (1994); pandas; numpy; scipy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy has outperformed and you need to know *why* before crediting it to skill or allocating more capital to it. It regresses realized strategy excess returns on the market excess return and, optionally, the Fama-French size (SMB) and value (HML) spreads, and reports the intercept — Jensen's alpha — with the standard error, $t$-statistic and $p$-value needed to judge whether it is distinguishable from zero. A levered long-only book in a bull market and a genuine alpha source produce the same headline return; only the decomposition separates them.

It also returns an *exact* additive attribution: risk-free contribution + alpha + $\sum_i \beta_i \bar{f_i}$ reconstructs the realized annual return, with `unexplained_residual_pct` as the closure check.

## When NOT to Use

- **As a forecast, or as evidence a strategy will keep working.** Alpha here is an in-sample intercept over the window you supplied. For decay diagnosis use `strategy-performance-decay-detection-vs-market-wide-decay`.
- **When the strategy's exposures are not stable over the window.** One regression estimates one constant beta. A strategy that deliberately times its beta, or that changed mandate mid-window, will show an average that describes neither regime. Split the window and run it twice.
- **To screen many strategies and report the significant ones.** A 5% test applied to 20 candidates yields roughly one "significant" alpha by chance. Correct for it — `factor-research-multiple-testing-correction`.
- **On non-equity strategies with equity factors.** SMB and HML are US equity spreads. Regressing an FX carry or commodity strategy on them produces loadings that are noise. Choose factors that plausibly span the strategy's returns, or run CAPM against the right benchmark — `benchmark-selection-for-strategy-evaluation`.
- **To rank strategies by their contribution to portfolio risk.** That is a different decomposition — `risk-adjusted-performance-attribution-per-strategy`.
- **As a risk control.** This is a reporting engine. It does not size, throttle, or halt anything.

## Prerequisites

- Strategy simple periodic returns (`strategy_returns`) as **decimal fractions** — a 0.53% day is `0.0053`, not `0.53` — net of fees, financing and borrow.
- Benchmark returns (`market_returns`) on the same index and in the same units.
- Optional Fama-French factors (`smb_returns`, `hml_returns`), same index and units.
- Annual risk-free rate in percent (`risk_free_rate_annual_pct`, default 2.0), de-annualized geometrically to a periodic simple rate.
- A **unique** index, and a **chronologically sorted** one if you intend to use `standard_errors="hac"`.
- At least $k+2$ aligned observations ($k$ = regressors including the intercept); the engine raises below that, and flags any sample shorter than one year via `insufficient_history_warning`.
- `numpy`, `pandas`, `scipy` (all already repo dependencies).

## Workflow

1. **Fix the unit and excess-return conventions before anything else**:
   - **Decision point — is `market_returns` a total return or already an excess return?** Ken French's data library ships a column literally named `Mkt-RF`, which is *already* excess of the risk-free rate. Passing it with the default `market_returns_are_excess=False` subtracts the risk-free rate a second time and biases alpha by $\beta \times R_f$. Set the flag.
   - **Decision point — units.** French's files are in **percent** (a daily row reads `20260630, 0.73, 0.10, -0.62, 0.01`); this engine expects decimals. Divide by 100. A percent/decimal mix scales every beta by 100 and is not detectable from the output alone.
   - SMB and HML are zero-investment long-short spreads and are used **raw** — never subtract the risk-free rate from them.

2. **Align and validate**:
   - Series are joined on the index and rows with any missing value are dropped. `observations` and `dropped_observations` record what actually entered the regression; check them, because a factor with a short history silently shortens the whole sample.
   - Duplicate index labels, infinities, non-numeric values, collinear factors, and samples below $k+2$ raise rather than producing a confident-looking number.

3. **Fit the regression**:
   $$R_{s,t} - R_{f,t} = \alpha + \beta_M (R_{M,t} - R_{f,t}) + \beta_S \mathrm{SMB}_t + \beta_H \mathrm{HML}_t + \varepsilon_t$$
   - **Decision point — which standard errors?** The default `"ols"` assumes residuals are iid and homoskedastic. If the strategy holds illiquid or stale-marked assets, uses overlapping holding periods, or is a momentum/trend book, its residuals are serially correlated, OLS standard errors are biased *downward*, and the alpha $t$-statistic is inflated. Use `standard_errors="hac"` (Newey-West, Bartlett kernel, default lag $\lfloor 4(n/100)^{2/9}\rfloor$) and report whichever you used. The coefficients are identical either way; only the inference changes.

4. **Judge significance against the right threshold**:
   - The engine compares $|t|$ against the exact two-sided Student-$t$ critical value with $n-k$ degrees of freedom, reported as `t_critical_value`, and returns `alpha_p_value`.
   - **Decision point — 1.96 is the large-sample limit, not the threshold.** At 252 daily observations the exact value is 1.9695, so the distinction rarely changes a verdict; at 60 monthly observations it is 2.0017 and 1.96 overstates significance. Read `t_critical_value`, not folklore.
   - **Decision point — `alpha_t_statistic` of `None` does not mean zero.** It means the residual variance is zero (a perfect in-sample fit, i.e. synthetic or degenerate data), so no sampling distribution exists. Read `undefined_metrics` for the reason.

5. **Decompose and reconcile**:
   - Each `FactorAttributionBreakdown` reports the loading, the annualized mean of the regressor *as it entered the regression*, and their product.
   - **Verify the identity before quoting any number**: `risk_free_contribution_pct + annualized_jensens_alpha_pct + Σ return_contribution_pct` must equal `total_realized_annual_return_pct`, and `unexplained_residual_pct` must be ~0. A non-zero residual is a bug, not a finding.

6. **Act on the split, not the headline**: an insignificant alpha with $\beta_M = 1.4$ is a levered index fund and should be priced as beta. A significant alpha with $\beta_S = 0.6$ is a small-cap tilt plus residual skill; the tilt is cheaply replicable and only the residual justifies an active fee.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Double-subtracting the risk-free rate from the market leg**: French's `Mkt-RF` is already an excess return. Passing it as a total return deducts $R_f$ twice, shifting alpha by $\beta \times R_f$ — about $-2\%$ a year at $\beta=1$ and a 2% rate, enough to turn a marginal alpha negative.
- **Mixing percent and decimal units**: French's factor files are in percent; strategy returns are usually decimals. Regressing `0.0053` on `0.53` returns a beta 100× too small and an alpha that absorbs the difference. Nothing in the output flags it — only a sanity check on the magnitudes will.
- **Subtracting the risk-free rate from SMB and HML**: they are zero-investment long-short spreads, already self-financing. Subtracting $R_f$ from them biases their loadings and corrupts the intercept.
- **Computing factor statistics over a different sample than the regression**: if SMB has a shorter history than the strategy, alignment shortens the *whole* regression. A factor mean taken over the full unaligned series and multiplied by a beta estimated on the short one is not an attribution of anything. Check `dropped_observations` and reconcile against `unexplained_residual_pct`.
- **Reading `t = 1.97` as proof of skill**: it is a 5% test on one strategy. Run it on twenty and you expect one such result from noise even if every alpha is truly zero.
- **Trusting OLS $t$-statistics on serially correlated residuals**: a trend-following or illiquid book violates the independence Jensen's derivation assumes, and OLS standard errors understate the true uncertainty. The alpha does not change; your confidence in it should.
- **Reading a high $R^2$ as "no alpha"**: $R^2$ measures the share of *variance* explained by the factors and says nothing about the *mean* the intercept captures. A strategy can be 95% explained by the market and still earn a large, significant alpha. The two questions are independent.
- **Treating an insignificant alpha as an estimate of zero**: it is an estimate that is imprecise. On a 60-day sample, alpha standard errors are wide enough that a genuinely excellent strategy will fail the test. `insufficient_history_warning` marks that case.
- **Interpreting `alpha_percentage_of_total_return` on a losing strategy**: it is `None` for a non-positive total return, because dividing a positive alpha by a negative total produces a negative share that reads as the opposite of the truth.
- **Assuming one beta describes the whole window**: a strategy that halves its exposure in a drawdown shows an average beta that matches neither half, and the residual variance it creates depresses the alpha $t$-statistic.

## Verification

- **Closed-form check (no library needed).** With `risk_free_rate_annual_pct=0.0`, `market_returns=[0.01, 0.02, 0.03, 0.04]` and `strategy_returns=[0.02, 0.03, 0.06, 0.07]`: $S_{xy}=0.0009$, $S_{xx}=0.0005$, so $\beta=1.8$ and $\alpha=0.045-1.8(0.025)=0$ exactly. Residuals are $[0.002,-0.006,0.006,-0.002]$, $SS_{res}=8\times10^{-5}$, $SS_{tot}=0.0017$, so $R^2=0.9529$ and adjusted $R^2=0.9294$. With $\sigma^2=4\times10^{-5}$: $\mathrm{SE}(\alpha)=\sqrt{6\times10^{-5}}=0.0077460$ and $\mathrm{SE}(\beta)=\sqrt{0.08}=0.2828427$, giving $t_\beta=6.3640$. The 5% threshold at 2 df is **4.302653**, not 1.96 — confirm `t_critical_value` reports it.
- **Cross-validation.** For a single-factor fit, `market_beta`, `annualized_jensens_alpha_pct`, `r_squared`, `alpha_standard_error` and the market `p_value` must match `scipy.stats.linregress` on the same excess series.
- **Decomposition identity.** For any input, `unexplained_residual_pct` must be zero to at least 8 decimal places.
- **Excess-return flag.** Running the same data with and without `market_returns_are_excess=True` must leave every beta unchanged and shift alpha by exactly $\beta_M \times R_{f,\text{period}} \times$ `periods_per_year` $\times 100$.
- **Zero-investment factors.** Changing `risk_free_rate_annual_pct` from 0% to 8% must leave the SMB and HML loadings bit-identical.
- **Newey-West.** With `hac_lags=0` the estimator must reduce to the textbook HC1 sandwich $(X'X)^{-1}\left(\sum_t u_t^2 x_t x_t'\right)(X'X)^{-1}\cdot n/(n-k)$; the default lag at $n=504$ must be $\lfloor 4(5.04)^{2/9}\rfloor = 5$; and on an AR(1) residual with $\rho=0.6$ the HAC alpha standard error must exceed the OLS one.
- **Negative checks** — each must raise: an empty series, fewer than $k+2$ aligned rows, non-overlapping indices, duplicate index labels, an infinity, a non-numeric value, a factor collinear with the market, `standard_errors="robust"`, `hac_lags` without `standard_errors="hac"`, `periods_per_year` of 0 or 2.5, `significance_level` of 0 or 1, and `risk_free_rate_annual_pct=-120`.
- **Undefined, not zero.** A noiseless $\beta=1.5$ series must return `alpha_t_statistic is None` with a populated `undefined_metrics`, not $t=0$.
- Run `python -m unittest discover -s skills/strategy-performance-attribution-vs-market-beta/scripts` and confirm a 100% pass rate.

## Related Skills

- `risk-adjusted-performance-attribution-per-strategy`
- `benchmark-relative-performance-attribution`
- `benchmark-selection-for-strategy-evaluation`
- `strategy-performance-decay-detection-vs-market-wide-decay`
- `factor-research-multiple-testing-correction`
- `strategy-capacity-estimation-before-scaling-capital`
- `strategy-lifecycle-retirement-criteria`

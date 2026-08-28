# Standards — risk-adjusted-performance-attribution-per-strategy

## Metric definitions (verified against the cited source)

| Metric | Definition as implemented | Source |
|---|---|---|
| Annualized return | Geometric: $\left(\prod_t (1+r_t)\right)^{252/n} - 1$ | Standard compounding convention |
| Total return | Compounded: $\prod_t (1+r_t) - 1$. **Not** $\sum_t r_t$ | Standard compounding convention |
| Annualized volatility | $\sigma_{\text{daily}} \times \sqrt{252}$, sample standard deviation ($n-1$) | Square-root-of-time rule |
| Sharpe ratio | $\dfrac{R_{\text{ann}} - R_f}{\sigma_{\text{ann}}}$ | Sharpe (1994), below |
| Sortino ratio | $\dfrac{R_{\text{ann}} - \text{MAR}}{\text{DD}_{\text{ann}}}$ | Sortino & Price (1994); Kidd (2012), below |
| Downside deviation | $\sqrt{\dfrac{1}{n}\sum_{t=1}^{n} \min(r_t - \text{MAR}, 0)^2} \times \sqrt{252}$ — averaged over **all** $n$ | Kidd (2012), below |
| Max drawdown | Largest peak-to-trough decline of the cumulative equity curve, as a positive fraction of the peak | Standard definition |
| Calmar ratio | $\dfrac{R_{\text{ann}}}{\lvert \text{MaxDD}\rvert}$ | Young (1991), below |
| Marginal risk contribution | $\mathrm{MCR}_i = (\Sigma w)_i / \sigma_p$ | Euler decomposition, below |
| Component risk contribution | $\mathrm{CR}_i = w_i \mathrm{MCR}_i$, with $\sum_i \mathrm{CR}_i = \sigma_p$ | Euler decomposition, below |

## Primary sources

**Sharpe ratio** — William F. Sharpe, "The Sharpe Ratio", *Journal of Portfolio
Management*, Fall 1994 ([author's copy](https://web.stanford.edu/~wfsharpe/art/sr/sr.htm)).

- The ex-post ratio is the average *differential* return divided by the standard
  deviation of the differential return. This engine takes a constant annual risk-free
  rate; subtracting a constant does not change the standard deviation, so the
  volatility of raw returns is used directly and the result is identical.
- Annualizing a one-period ratio by $\sqrt{T}$ holds when the differential returns
  have **zero serial correlation**. Sharpe notes that in practice "multiperiod returns
  are usually computed taking compounding into account, which makes the relationship
  more complicated. Moreover, underlying differential returns may be serially
  correlated." The $\sqrt{252}$ scaling here is therefore the standard convention and
  an approximation, not an identity.
- Sharpe explicitly notes the ratio "does not take correlations into account" and
  recommends supplementing it with correlation information where the decision affects
  portfolio correlations — which is precisely the role of the Euler decomposition below.

**Sortino ratio and downside deviation** — Frank Sortino & Lee Price, "Performance
Measurement in a Downside Risk Framework", *Journal of Investing*, 1994. Summarized in
Deborah Kidd, CFA, "The Sortino Ratio: Is Downside Risk the Only Risk that Matters?",
*Investment Performance Measurement*, CFA Institute, 2012
([PDF](https://rpc.cfainstitute.org/sites/default/files/-/media/documents/code/gips/the-sortino-ratio.pdf)).

- Formula as given: $S = (\text{Mean portfolio return} - \text{MAR}) / \text{Downside deviation}$.
- The MAR is investor-defined: "an absolute return, an index return, the risk-free
  rate, or zero". This engine uses the risk-free rate as the MAR by default.
- On the denominator: the squared differences are "divided by the total number of
  returns", not by the count of periods below the target. Dividing by the losing
  periods only is the common miscalculation and inflates the ratio.
- **Caveat carried into the code's documented limitations**: Sortino & Forsey (1996)
  show the discrete historical method "can significantly underestimate downside risk"
  when most returns are positive, and Kidd notes that "annualizing discrete data will
  overstate risk". The Sortino ratio computed here is an estimate from realized
  history, not the continuous-distribution measure Sortino & Forsey recommend.
- Sortino & Price note that using the risk-free rate as the MAR detracts from the
  ratio's usefulness as a *goal-oriented* measure; they suggest a market index return
  instead. The per-strategy `risk_free_rate_annual` override exists to allow this.
- Ratios are only comparable across strategies when the MAR is identical.

**Calmar ratio** — Terry W. Young, "Calmar Ratio: A Smoother Tool", *Futures*
(Modern Trader), Vol. 20 Issue 12, October 1991. "Calmar" is an acronym of
CALifornia Managed Accounts Reports.

- Compound annualized return divided by the absolute maximum drawdown.
- Young's convention is a trailing **36-month** window, evaluated monthly (a modified
  Sterling ratio, which uses a yearly basis). **This engine applies the formula to
  whatever window it is given**, so a Calmar produced here is comparable to a published
  Calmar only when computed over a 36-month window.

**Euler risk decomposition** — Eric Zivot, *Introduction to Computational Finance and
Financial Econometrics with R*, Ch. 14.2 and 14.4
([online](https://bookdown.org/compfinezbook/introcompfinr/eulers-theorem-and-risk-decompositions.html)).

- $\sigma_p(w) = (w'\Sigma w)^{1/2}$ is homogeneous of degree one in the weights
  (Proposition 14.1), so Euler's theorem gives an exact additive decomposition.
- $\mathrm{MCR}_i^\sigma = (\Sigma w)_i / \sigma_p(w)$ (Eq. 14.8).
- $\mathrm{CR}_i^\sigma = w_i (\Sigma w)_i / \sigma_p(w)$ (Eq. 14.9).
- $\sigma_p(w) = \sum_i \mathrm{CR}_i^\sigma$ (Eq. 14.4).

The naive alternative $w_i\sigma_i / \sum_j w_j\sigma_j$ is **not** a risk
decomposition. It equals the Euler contribution only when every pairwise correlation
is $+1$; otherwise it ignores diversification entirely and reports a strictly positive
contribution for a strategy whose true contribution is negative.

## Sample-size guidance (not a compliance gate)

The Global Investment Performance Standards (GIPS) 2020 for Firms require firms
claiming compliance to present the **3-year (36-month) annualized ex-post standard
deviation** for the composite and the benchmark, and to disclose when it is not
presented because 36 monthly returns are unavailable
([GIPS 2020 for Firms](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf)).

That standard governs firms presenting composite performance to prospective clients.
It does **not** apply to internal multi-strategy attribution, and this engine does not
implement GIPS. It is cited only as the reference point for the library's
`min_recommended_observations` default of 252 observations, which raises
`insufficient_history_warning` — a flag, never a refusal.

## Known limitations

- **Backward-looking.** Realized metrics over the supplied window; not forecasts.
- **No timestamps.** Series are aligned by position. Ragged series are rejected;
  equal-length but *misaligned* series cannot be detected and will silently corrupt
  the covariance matrix and therefore the decomposition.
- **Risk contributions may be negative** for diversifying strategies, and are `None`
  when portfolio volatility is zero.
- **Serial correlation** inflates Sharpe ratios and breaks the $\sqrt{252}$
  annualization assumption (Lo 2002, discussed in Kidd 2012).

## Category

`portfolio-risk-management`

# Standards for Strategy Performance Decay Detection vs Market-Wide Decay

## What is a published result and what is a house default

Only the statistical machinery below is drawn from published literature. The
performance thresholds are governance choices this repository defaults to; no
regulator, exchange, or standards body mandates a Sharpe ratio floor.

| Parameter | Basis | Status |
|---|---|---|
| Sharpe-difference test statistic | Jobson & Korkie (1981), corrected by Memmel (2003) | Published method |
| $z \le -1.96$ | 2.5th percentile of the standard normal; a **2.5% one-sided** false-positive rate, not "95% confidence" in the trading conclusion | Published quantile, house choice of level |
| Peer/target health threshold, annualized Sharpe $\ge 0.50$ | Governance convention | **House default — no external standard** |
| Rolling evaluation window, 60 observations | Trades responsiveness against the test's asymptotics; the statistic is liberal in small samples | **House default** |
| $\sqrt{252}$ annualization | Valid only for serially uncorrelated returns (Lo 2002) | Published caveat |

## The test statistic

For target $t$ and peer benchmark $p$ over $T$ aligned observations, with per-period
Sharpe ratios $\hat{Sh}_t, \hat{Sh}_p$ on excess returns and return correlation $\rho$:

$$
z = \frac{\hat{Sh}_t - \hat{Sh}_p}{\sqrt{\hat\theta}}, \qquad
\hat\theta = \frac{1}{T}\left[\,2 - 2\rho + \tfrac{1}{2}\left(\hat{Sh}_t^{2} + \hat{Sh}_p^{2} - 2\,\hat{Sh}_t\hat{Sh}_p\,\rho^{2}\right)\right]
$$

$\hat\theta$ follows from the delta method applied to $\Delta = \mu_t/\sigma_t - \mu_p/\sigma_p$
with the asymptotic covariance matrix

$$
\Omega = \begin{pmatrix}
\sigma_t^2 & \sigma_{tp} & 0 & 0\\
\sigma_{tp} & \sigma_p^2 & 0 & 0\\
0 & 0 & 2\sigma_t^4 & 2\sigma_{tp}^2\\
0 & 0 & 2\sigma_{tp}^2 & 2\sigma_p^4
\end{pmatrix}
$$

which is the covariance matrix Jobson & Korkie (1981) and Memmel (2003) assume, as
reproduced in Ledoit & Wolf (2008, Section 2). The statistic is invariant to
annualization: numerator and standard error both scale by $\sqrt{F}$.

## Assumptions and where they fail

$z \sim N(0,1)$ asymptotically **only** under i.i.d. bivariate normal returns.

- **Heavy tails.** $\Omega$'s lower-right block assumes the fourth moment of a normal.
  For non-normal i.i.d. data the correct entry is $E[(r-\mu)^4] - \sigma^4$, not
  $2\sigma^4$; Opdyke (2007) gives the general i.i.d. correction.
- **Serial correlation.** $\Omega$ has no autocovariance terms at all. Smoothed or
  illiquid marks — and hedge-fund-style return series generally — violate this
  materially, and the test over-rejects.
- **Remedy.** Ledoit & Wolf (2008) recommend a HAC standard error (Andrews 1991) or,
  with better finite-sample behaviour, a **studentized time-series bootstrap**
  confidence interval for the Sharpe difference, declaring the ratios different when
  the interval excludes zero. Use that before acting on a marginal result.
- **Annualization.** Lo (2002) derives the Sharpe ratio's distribution under i.i.d.,
  stationary, and time-aggregated returns, and shows the $\sqrt{F}$ rule is invalid
  under autocorrelation. This affects the reported Sharpe ratios and therefore the
  comparison against the health threshold, though not the $z$ statistic itself.
- **Multiple testing.** Running the diagnosis monthly across a book multiplies the
  false-positive count linearly in the number of tests. Correct for it, or read a
  single breach as a prompt to investigate rather than a decision.

## References

- Jobson, J.D. and Korkie, B.M. (1981). "Performance hypothesis testing with the
  Sharpe and Treynor measures." *Journal of Finance*, 36(4), 889–908.
- Memmel, C. (2003). "Performance hypothesis testing with the Sharpe ratio."
  *Finance Letters*, 1, 21–23. (Corrects the asymptotic variance in Jobson & Korkie.)
- Ledoit, O. and Wolf, M. (2008). "Robust performance hypothesis testing with the
  Sharpe ratio." *Journal of Empirical Finance*, 15(5), 850–859.
  https://www.econ.uzh.ch/dam/jcr:ffffffff-935a-b0d6-0000-00007214c2bc/jef_2008pdf.pdf
  (Sections 2–3: states the Jobson-Korkie/Memmel assumptions, shows they fail for
  heavy-tailed or serially correlated returns, and proposes HAC and bootstrap
  alternatives.)
- Lo, A.W. (2002). "The Statistics of Sharpe Ratios." *Financial Analysts Journal*,
  58(4), 36–52. (Distribution of the Sharpe ratio under i.i.d., stationary, and
  time-aggregated returns; limits of $\sqrt{F}$ annualization.)
- Opdyke, J.D. (2007). "Comparing Sharpe ratios: So where are the p-values?"
  *Journal of Asset Management*, 8(5), 308–336. (General i.i.d., non-normal
  correction to the Memmel variance; see Ledoit & Wolf 2008, Remark 3.1 for the
  limits of its time-series formulae.)

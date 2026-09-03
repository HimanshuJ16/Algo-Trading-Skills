# Standards — risk-budget-allocation-across-time-horizons

## What is a standard here, and what is not

No regulator or standards body publishes a mandatory horizon split, portfolio volatility
target, or drawdown limit. Everything in the "Configuration defaults" table below is a
library default or a firm-level policy choice. The only externally sourced material on
this page is the risk budgeting definition in the next section, which is a mathematical
result, not a rule anyone is obliged to follow.

## Risk budgeting definition (verified against the primary source)

Source: Benjamin Bruder and Thierry Roncalli, **"Managing Risk Exposures using the Risk
Budgeting Approach"**, Lyxor Asset Management, January 2012 (this version March 2012),
[MPRA Paper No. 37749](https://mpra.ub.uni-muenchen.de/37749/). Book-length treatment:
Thierry Roncalli, *Introduction to Risk Parity and Budgeting*, Chapman & Hall/CRC, 2013
([arXiv:1403.1889](https://arxiv.org/abs/1403.1889)).

| Fact | Location |
|---|---|
| For a coherent, convex risk measure, the Euler decomposition holds: $R(x_1,\dots,x_n) = \sum_i x_i \cdot \partial R / \partial x_i$ | Sec. 2.1 |
| Risk contribution of asset $i$: $RC_i(x) = x_i \cdot \partial R(x) / \partial x_i$ | Sec. 2.1 |
| A risk budget $b_i$ is **an amount of risk**; the risk budgeting portfolio is defined by $RC_i(x) = b_i$ for every $i$ | Sec. 2.1, Eq. (1) |
| With volatility as the risk measure, $R(x) = \sigma(x) = \sqrt{x^\top \Sigma x}$ and $\partial R / \partial x_i = (\Sigma x)_i / \sqrt{x^\top \Sigma x}$ | Sec. 2.1 |

**What this module implements, and what it does not.** The Euler risk contribution
requires $\Sigma$, the covariance matrix of the sleeves. This module is given only
standalone volatilities, so it cannot compute $RC_h$. It instead denominates the budget
in volatility units and sizes each sleeve so its **standalone** volatility equals its
budget share:

$$\sigma_h^{\text{target}} = b_h \sigma_p, \qquad k_h = \sigma_h^{\text{target}} / \sigma_h^{\text{base}}$$

Because volatility is sub-additive — $\sigma\big(\sum_h X_h\big) \le \sum_h \sigma(X_h)$,
with equality only under perfect correlation — this makes $\sum_h k_h\sigma_h^{\text{base}}
= \sigma_p$ an **upper bound** on realized portfolio volatility, and each sleeve's true
Euler contribution $RC_h = k_h\sigma_h^{\text{base}}\rho_{h,p} \le b_h\sigma_p$. The
allocation is therefore conservative by construction and credits no diversification
between horizons. `strategy-specific-vs-shared-risk-budget-allocation` and
`risk-parity-allocation-across-strategies` take the covariance matrix and do the Euler
decomposition properly.

## Holding-period scaling (diagnostic only)

`holding_period_vol` scales an annualized volatility to a holding period by the
square-root-of-time rule, $\sigma_T = \sigma_{\text{ann}}\sqrt{T/F}$. The rule is exact
only for iid returns with constant volatility. Under a jump-diffusion it **systematically
understates** risk, and the understatement worsens with the horizon, the jump intensity,
and the confidence level (Jón Daníelsson and Jean-Pierre Zigrand, "On time-scaling of risk
and the square-root-of-time rule", *Journal of Banking and Finance* 30(10), 2006,
pp. 2701–2713; [preprint](https://eprints.lse.ac.uk/24827/1/dp439.pdf)). It is used here
only to flag drawdown limits set inside routine noise, never as a risk figure.

## Configuration defaults (calibrate before use)

| Parameter | Default | What it actually does |
|---|---|---|
| `total_portfolio_vol_target` ($\sigma_p$) | $0.15$ | The whole budget being divided. Every horizon's vol target and scalar is linear in it. |
| `portfolio_max_drawdown_limit_pct` | `None` | When `None` the drawdown check **does not run** and `is_within_limits` is `None` — not evaluated, not passed. |
| `trading_days_per_year` | $252$ | Denominator of the holding-period diagnostic. Set it to the session count of the market actually traded. |
| `ALLOCATION_TOLERANCE_PCT` | $10^{-9}$ pp | Slack on the 100% cap, absorbing binary representation error in two-decimal percentages without hiding an economically meaningful breach. |
| `MAX_PLAUSIBLE_ANNUALIZED_VOL` | $3.0$ | Engineering guard catching the percent/fraction mix-up (`15` for `0.15`), which would otherwise produce a $100\times$ scalar. Not a published limit. |

## Engineering requirements

| Requirement | Rationale |
|---|---|
| Horizon budgets MUST sum to at most 100% of the portfolio risk budget. | The budget being divided is finite; a total above 100% means the sleeves collectively target more volatility than the portfolio tolerates. |
| Every horizon's volatility target MUST be derived from its budget, not declared alongside it. | Two independent knobs cannot be reconciled: the realized risk split becomes whatever the sizing happens to produce, and the budget is decorative. |
| Non-finite and negative inputs MUST be rejected, not summed. | `NaN > 100.0` is `False` and a negative allocation offsets a positive one — both silently pass an over-allocated budget as valid. |
| The reported total MUST NOT be rounded before the cap comparison. | An audit record reading "100.0%" beside `over_allocated=True` is self-contradicting and useless in a post-incident review. |
| Per-horizon drawdown limits MUST be checked against a portfolio limit under the assumption that horizons draw down together. | Individually green limits can sum well past the firm's tolerance; horizon correlations rise in exactly the conditions that produce simultaneous drawdowns. |
| The report MUST be treated as advisory. | It returns flags. It does not block trading, cancel orders, or flatten positions; the caller must gate on them. |

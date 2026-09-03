# Risk Standards — value-at-risk-var-live-monitoring

## Estimator conventions used by this skill

| Measure | Convention as implemented | Note |
|---|---|---|
| Parametric (variance-covariance) VaR | $z_c \cdot \sigma_p - \mu_p$, fraction of NAV | $z_c$ from `statistics.NormalDist.inv_cdf` (Wichura AS241), exact at every level — not a lookup table. $\sigma_p$ is the sample $(n-1)$ standard deviation. Set `subtract_mean_drift=False` for $z_c \sigma_p$. |
| Historical Simulation VaR | $k$-th worst loss, $k = \lceil n(1-c) \rceil$ | At $n=100, c=0.99 \Rightarrow k=1$; at $n=250 \Rightarrow k=3$. Other texts use $\lfloor n(1-c) \rfloor + 1$; the two differ by one observation exactly when $n(1-c)$ is integral, which is the common round-$n$ case — hence stated, not implied. |
| CVaR / Expected Shortfall | Mean loss of those same $k$ worst observations | $\text{CVaR} \ge \text{VaR}$ holds by construction. |
| Standard normal quantiles | $z_{0.95}=1.64485$, $z_{0.975}=1.95996$, $z_{0.99}=2.32635$, $z_{0.999}=3.09023$ | Verified against `NormalDist().inv_cdf`. |
| Default breach limit | $\ge 5.0\%$ of NAV, on parametric **and** historical VaR | Illustrative default, not a regulatory figure. CVaR joins the verdict only when `cvar_limit_pct` is set. |
| Minimum sample | $\lceil 1/(1-c) \rceil$ — 100 at 99%, 20 at 95% | Smallest sample whose tail bucket holds one observation. Warned below 252. |

## Regulatory context — verified sources

Read the **scope** of each, not just the number. None of these makes a VaR breaker
mandatory for a proprietary trading firm, and this module is not a capital calculation.

| Claim | Source | Verified text | Applicability |
|---|---|---|---|
| The FRTB capital measure is Expected Shortfall at 97.5%, not 99% VaR | BCBS d457, *Minimum capital requirements for market risk* (Jan 2019), **MAR33.3** | "In calculating ES, a bank must use a 97.5th percentile, one-tailed confidence level." | Banks using the internal models approach. Explains why CVaR is a first-class output here rather than a footnote. |
| Base liquidity horizon is 10 days, with scaling | BCBS **MAR33.4** | "The ES for a liquidity horizon must be calculated from an ES at a base liquidity horizon of 10 days with scaling applied to this base horizon result." | Bank IMA. This module produces a **1-period** measure at the frequency of the supplied returns and applies no scaling. |
| Backtesting uses one year of data at both 97.5% and 99% | BCBS **MAR32.18** (desk level), **MAR32.5** (bank-wide) | MAR32.18: "at least one year of current observations of the desk's one-day P&L"; backtesting "must compare each desk's one-day VaR measure (calibrated to the most recent 12 months' data, equally weighted) at both the 97.5th percentile and the 99th percentile." MAR32.5: bank-wide backtesting "based on a VaR measure calibrated at a 99th percentile confidence level." | Bank IMA. Source of the one-year (~250 observation) floor this skill warns below. |
| Minimum historical observation period of one year | **12 CFR 217.205(b)(2)** (US market risk rule, Federal Reserve Board) | "The VaR-based measure must be based on a historical observation period of at least one year… The Board-regulated institution must update data sets at least monthly or more frequently as changes in market conditions or portfolio composition warrant." | US Board-regulated institutions. 217.205(b)(1) sets a one-tail 99.0% confidence level and a 10-business-day holding period, and permits conversion from other holding periods. |
| Pre-trade controls on capital/credit thresholds | **17 CFR 240.15c3-5(c)(1)(i)** | Financial risk controls must be "reasonably designed to prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds." | **Broker-dealers with market access.** The rule prescribes *no* metric — a VaR limit can serve as one such threshold, it is not itself the requirement. Under 15c3-5(d)(1) the controls must be under the broker-dealer's direct and exclusive control, and 15c3-5(e) requires at least an annual review of their effectiveness. A proprietary firm trading as a *customer* of a broker-dealer is not the regulated party; its own VaR breaker is an internal control. |

### What is *not* claimed

- No jurisdiction is asserted to mandate a 5% NAV VaR limit. That default is illustrative.
- The Basel and Federal Reserve rules above bind **banks**, not algorithmic trading
  firms generally. They are cited as the authority for the estimator conventions and the
  sample-size floor, not as obligations this skill discharges.
- Nothing here has been verified against SEBI, FCA, ESMA, MAS or ASIC requirements.

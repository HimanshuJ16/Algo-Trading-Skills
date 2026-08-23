# Workflows for Cross-Sectional vs Time-Series Model Design

## 0. Architecture Selection

- Dollar-neutrality mandate over $K \ge 2$ assets $\Rightarrow$ `CROSS_SECTIONAL`.
- Directional trend on a single asset or futures contract $\Rightarrow$ `TIME_SERIES`.
- Reject contradictory mandates before transforming: neutrality over $K < 2$ assets, or
  neutrality combined with a single-asset trend flag, has no valid architecture.
- Confirm whether the mandate means *dollar* neutrality ($\sum w_i = 0$) or *beta*
  neutrality ($\sum w_i \beta_i = 0$). These workflows deliver only the former.

## 1. Cross-Sectional Workflow

1. For timestamp $t$, collect raw factor values $X_{1,t}, X_{2,t}, \dots, X_{K,t}$.
2. Reject the cross-section if any value is non-finite — do not impute.
3. Winsorize. Check the threshold can bind: the largest attainable $|z|$ over $K$
   observations is $(K-1)/\sqrt{K}$, so a $\pm 3\sigma$ clip does nothing for $K \le 10$.
   Use $\pm k \times 1.4826 \times \mathrm{MAD}$ around the median at small $K$.
4. Compute mean $\mu_{cs,t}$ and standard deviation $\sigma_{cs,t}$ across assets.
5. $Z_{i,t} = (X_{i,t} - \mu_{cs,t}) / \sigma_{cs,t}$ — a reported diagnostic.
6. Weights: $w_{i,t} = \frac{Z_{i,t} - \bar{Z}_t}{\sum_j |Z_{j,t} - \bar{Z}_t|}$.
   $\bar{Z}_t$ is already zero and $\sigma_{cs,t}$ cancels, so this equals
   $(X_{i,t} - \mu_{cs,t}) / \sum_j |X_{j,t} - \mu_{cs,t}|$.
7. Robust alternative (AMP 2013, eq. 1): $w_{i,t} \propto \text{rank}(X_{i,t}) - \overline{\text{rank}}_t$,
   averaging ranks over ties. Bounded influence — preferred whenever winsorization cannot bind.
8. Verify $|\sum_i w_{i,t}| \le 10^{-5}$ on the returned weights, and $\sum_i |w_{i,t}| = 1$.
   Do not round the weights before this check; round only for display.

## 2. Time-Series Workflow

1. For asset $i$, collect historical values of the **same quantity as the current factor**
   over window $W$: $X_{i,t-W:t}$. Raw period returns are not interchangeable with a
   trailing-horizon momentum factor — their dispersions are on different scales.
2. Require at least `min_history` observations. Fewer means no position, not a
   defaulted $\mu=0,\sigma=1$ standardization.
3. Compute mean $\mu_{ts,i}$ and standard deviation $\sigma_{ts,i}$ over time.
4. $Z_{i,t} = (X_{i,t} - \mu_{ts,i}) / \sigma_{ts,i}$. If $\sigma_{ts,i} \approx 0$, set
   $Z = 0$ and emit a flat weight.
5. Obtain $\sigma_{i,t-1}$: an **annualized** realized volatility estimated strictly from
   data before the bar being sized (MOP 2012 §2.4 use an exponentially weighted estimator
   with a 60-day centre of mass, lagged one period). Reject non-positive values — never floor them.
6. Scale weights: $w_{i,t} = \text{sign}(Z_{i,t}) \times \frac{\sigma_{target}}{\sigma_{i,t-1}}$,
   then apply the `max_leverage` cap as a risk-policy overlay (MOP apply no cap).
7. Verify weights scale inversely with realized volatility: halving $\sigma_{i,t-1}$ must
   double the weight until the cap binds.

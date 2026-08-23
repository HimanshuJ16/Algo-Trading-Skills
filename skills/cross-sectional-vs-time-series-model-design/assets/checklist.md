# Pre-Flight Checklist

- [ ] Is the model architecture (`CROSS_SECTIONAL` vs `TIME_SERIES`) selected from the strategy mandate, with contradictory mandates (neutrality over $K < 2$, or neutrality plus single-asset trend) resolved rather than defaulted?
- [ ] Is the mandate's "neutrality" confirmed as **dollar** neutrality ($\sum w_i = 0$) and not silently assumed to deliver **beta** neutrality ($\sum w_i \beta_i = 0$)?
- [ ] Are non-finite (NaN/Inf) factor values rejected upstream rather than imputed or allowed to propagate into weights?
- [ ] Can the winsorization threshold actually bind for this universe size — i.e. is $k < (K-1)/\sqrt{K}$? ($\pm 3\sigma$ is inert for $K \le 10$.)
- [ ] For small or fat-tailed cross-sections, is MAD-based clipping or rank weighting (AMP 2013) used instead of sigma-clipping?
- [ ] Are cross-sectional Z-scores standardized across the asset axis at each timestamp?
- [ ] Is it understood that $\sigma_{cs}$ cancels in the $\sum|w|=1$ normalization, so Z-scoring is a diagnostic and not a risk normalization?
- [ ] Is zero net dollar exposure verified on the **returned** weights ($|\sum w_i| \le 10^{-5}$), not on an unrounded intermediate?
- [ ] Is the gross-exposure convention reconciled ($\sum|w| = 1$ here vs $\sum|w| = 2$ in AMP 2013) before comparing to published factor returns?
- [ ] Is `asset_realized_vol_annual` **annualized** and estimated strictly from data **before** the bar being sized (MOP 2012 apply $\sigma_{t-1}$ to time-$t$ returns)?
- [ ] Does a non-positive or non-finite volatility raise, rather than being floored into maximum leverage?
- [ ] Is `historical_factor_values` the history of the same quantity as `current_factor` (not raw returns against a trailing-horizon factor)?
- [ ] Does insufficient history raise rather than defaulting to $\mu=0, \sigma=1$ and sizing a full position?
- [ ] Are time-series trend positions scaled inversely by realized volatility, with `max_leverage` sized to the mandate (MOP apply no cap)?
- [ ] Is a degenerate or zero Z-score mapped to a flat weight rather than an arbitrary direction?

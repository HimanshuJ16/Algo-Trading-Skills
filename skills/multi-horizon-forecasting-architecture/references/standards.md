# Financial ML Standards — multi-horizon-forecasting-architecture

## Horizon scale normalization

| Scaling Mode | Rescaling factor $\sigma(\tau_\star)/\sigma(\tau_k)$ | Extra input required | Use when |
|---|---|---|---|
| `EXPLICIT_VOL` | Measured $\sigma(\tau_\star) / \sigma(\tau_k)$ | Realized volatility per horizon, including $\tau_\star$ | Accuracy matters; returns are autocorrelated, heteroscedastic, or jumpy |
| `SQRT_TIME` | $\sqrt{\tau_\star / \tau_k}$ | None | Quick, reasonable default; accepted approximation |
| `NONE` | $1$ | None | Forecasts were already normalized upstream |

Rescaling is not optional bookkeeping. Forecasts stated over different horizons are
different quantities, and a weighted average of the raw numbers has no defined
horizon at all. `composite_alpha`, `normalized_predictions`, and `conflict_threshold`
all live in target-horizon return units precisely so the composite means something.

## Weighting schemes

| Scheme | Weight | Ignores | Note |
|---|---|---|---|
| `IC_WEIGHTED` | $w_k = \max(0, IC_k)\cdot c_k$ | Cross-horizon forecast correlation | $IC_k$ must be measured *at horizon* $\tau_k$ |
| `INVERSE_HORIZON_SQRT` | $w_k = c_k / \sqrt{\tau_k}$ | Measured skill | Tilts toward short horizons, so it *raises* turnover |
| `EQUAL` | $w_k = 1$ | $IC_k$ and $c_k$ | The $1/N$ robustness baseline |

All three are **marginal** criteria: each judges a horizon in isolation. Forecasts over
nested horizons are mechanically correlated — the 5-minute window is contained in the
60-minute window — so none of these is a minimum-variance combination. No
covariance-weighted path is offered because the forecast-error covariance is hard to
estimate well enough for the optimized weights to beat the marginal ones in practice.

There is **no standard requiring weights to decay with horizon length.** Whether short
or long horizons deserve more weight is an empirical question about measured IC and
net-of-cost turnover for a specific model set, not a rule.

## Degenerate weights

When every raw weight is zero — under `IC_WEIGHTED` this means no horizon has
non-negative measured skill, or all confidences are zero — the engine zeroes the
composite and returns `NO_VALID_HORIZON_WEIGHTS` with a WARNING. Falling back to
equal weighting in that state would convert "nothing here predicts" into a
full-strength tradeable signal.

## Sources

Quantitative methodology, not compliance: none of the below is a regulatory
requirement. Bibliographic records were checked against the publishers' listings.

| Claim | Source | Status |
|---|---|---|
| A return forecast decomposes as $\alpha = \sigma \cdot IC \cdot \text{score}$, so dividing a forecast by its horizon's volatility recovers a dimensionless score — the basis for `composite_score` and for comparing forecasts across horizons | Grinold, R. C. (1994), "Alpha is Volatility Times IC Times Score", *Journal of Portfolio Management* 20(4), 9–16. https://doi.org/10.3905/jpm.1994.409482 | Verified; the canonical statement of the identity used here |
| IC is a correlation coefficient between forecast and realized return, hence bounded in $[-1, 1]$ — enforced as input validation on `ic_score` | Grinold, R. C. & Kahn, R. N. (1999), *Active Portfolio Management*, 2nd ed., McGraw-Hill, Ch. 6 | Verified; standard definition |
| $\sigma(\tau) \propto \sqrt{\tau}$ requires zero-mean, homoscedastic, serially uncorrelated returns; under jumps and fat tails it systematically understates risk, and the bias grows with the horizon | Daníelsson, J. & Zigrand, J.-P. (2006), "On time-scaling of risk and the square-root-of-time rule", *Journal of Banking & Finance* 30(10), 2701–2713 | Verified; the documented limitation of `SQRT_TIME`, and the reason `EXPLICIT_VOL` exists |
| Combining forecasts by inverse forecast-error variance, ignoring cross-model correlation, is the standard practical choice because the error covariance is hard to estimate reliably | Bates, J. M. & Granger, C. W. J. (1969), "The Combination of Forecasts", *Journal of the Operational Research Society* 20(4), 451–468 | Verified; supports offering only marginal weighting schemes here |
| A factor's information horizon drives portfolio turnover: a short information horizon means the signal decays quickly and forces rebalancing | Qian, E., Sorensen, E. H. & Hua, R. (2007), "Information Horizon, Portfolio Turnover, and Optimal Alpha Models", *Journal of Portfolio Management* 34(1), 27–40 | Verified; supports the turnover caveat on short-horizon weighting |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

No jurisdiction-specific rule governs how return forecasts are blended across horizons.
Conflict arbitration here **damps signal conviction; it is not a risk control**. Exposure
limits, concentration caps, and kill switches must be enforced independently of the
signal path, as required by regimes such as SEC Rule 15c3-5 and MiFID II RTS 6 — see
`kill-switch-and-drawdown-circuit-breakers` and `correlation-aware-exposure-limits`.
Where the composite feeds a live model, document the horizon set, scaling mode, and
weighting scheme under `model-card-documentation-for-trading-models`.

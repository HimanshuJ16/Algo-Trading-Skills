# Financial ML Standards — feature-engineering-cost-benefit-tracking

## Verdict Table

Evaluated in order; the first matching row wins. $I$ = importance (fraction of model
score), $\sigma$ = importance standard deviation across permutation repeats, $C$ =
monthly cost, $L$ = per-inference latency, $k$ = `prune_confidence_sigma`.

| # | Importance | Cost / Latency | Recommendation | Reason |
|---|---|---|---|---|
| 1 | $I < I_{\min}$ but $I + k\sigma \ge I_{\min}$ | any | `REVIEW` | Estimate not distinguishable from the threshold |
| 2 | $I + k\sigma < I_{\min}$ | $C > C_{\max}$ or $L > L_{\max}$ | `PRUNE` | Confidently low value at prohibitive cost |
| 3 | $I < I_{\min}$ | $C \le C_{\max}$ and $L \le L_{\max}$ | `REVIEW` | Low value but cheap to carry |
| 4 | $I \ge I_{\min}$ | $L > L_{\max}$ | `REVIEW` | Useful but over the latency budget |
| 5 | $I \ge I_{\min}$ | $C > C_{\text{high}}$ | `REVIEW` | Useful but very expensive |
| 6 | $I \ge I_{\min}$ | within all budgets | `KEEP` | Good ROI |

After per-feature evaluation, a `PRUNE` on any member of a correlated `group` whose
**aggregate** importance reaches $I_{\min}$ is downgraded to `REVIEW`.

## Default Thresholds Are Not Standards

| Parameter | Default | What it actually is |
|---|---|---|
| `min_importance_threshold` ($I_{\min}$) | $0.01$ | An illustrative 1-percentage-point score contribution. Calibrate against the *noise floor* of your importance estimator: run permutation importance on a known-random feature and set the threshold above its observed dispersion. |
| `max_acceptable_cost_usd` ($C_{\max}$) | \$50/mo | Illustrative. The defensible value is the point where the feature's marginal P&L contribution stops covering its licence. |
| `high_cost_review_usd` ($C_{\text{high}}$) | \$500/mo | Illustrative renewal-review trigger. Must be $\ge C_{\max}$ or the review band is empty (the constructor rejects this). |
| `max_latency_ms` ($L_{\max}$) | `None` (disabled) | Must be derived from the strategy's own tick-to-trade budget — see `model-inference-latency-budget-for-live-trading`. There is no universal value. |
| `roi_cost_floor_usd` ($F$) | \$1.00/mo | A numerical guard on the ROI denominator, not an economic quantity. |
| `prune_confidence_sigma` ($k$) | $1.0$ | A one-sided normal-approximation margin, not a formal test statistic. |

No regulator, exchange, or standards body publishes thresholds for feature cost-benefit
pruning. Any figure presented as an industry standard here would be fabricated; these
are starting points that must be calibrated per strategy.

## Sources

These are the published results the method choices rest on. All were checked against
the primary record. None is a regulatory requirement — this skill's subject matter is
quantitative methodology, not compliance.

| Claim | Source | Status |
|---|---|---|
| Permutation importance is the drop in model score when one feature's values are shuffled; computing it over `n_repeats` shuffles yields a sample of importances with a reportable standard deviation | scikit-learn User Guide, "Permutation feature importance". https://scikit-learn.org/stable/modules/permutation_importance.html | Verified against the current documentation; the definition and the `n_repeats` dispersion behavior implemented by the confidence gate |
| "When two features are correlated and one of the features is permuted, the model still has access to the latter through its correlated feature. This results in a lower reported importance value for both features, though they might actually be important." Recommended handling: cluster correlated features and keep one per cluster | scikit-learn User Guide, "Permutation feature importance" (§ on misleading values with strongly correlated features) | Verified verbatim; the direct basis for the `group` dilution guard |
| Importance computed on a held-out set, rather than the training set, is what identifies features that contribute to generalization | scikit-learn User Guide, "Permutation feature importance" (§ outline of the algorithm / training vs held-out data) | Verified; the basis for the held-out prerequisite |
| Random-forest permutation importance is biased toward correlated predictors, from both the split-selection process and the unconditional permutation scheme; a conditional permutation scheme reflects the true impact more reliably | Strobl, C., Boulesteix, A.-L., Kneib, T., Augustin, T. & Zeileis, A. (2008), "Conditional variable importance for random forests", *BMC Bioinformatics* 9:307. https://doi.org/10.1186/1471-2105-9-307 | Verified; supports treating unconditional permutation importance on correlated features as unreliable for pruning decisions |
| Permute-and-predict importance measures evaluate the model off the data manifold and can produce misleading diagnostics under strong feature dependence | Hooker, G., Mentch, L. & Zhou, S. (2021), "Unrestricted permutation forces extrapolation: variable importance requires at least one more model, or there is no free variable importance", *Statistics and Computing* 31, 82. https://doi.org/10.1007/s11222-021-10057-z | Verified; the stated limitation on interpreting permutation importance for correlated features |
| Shapley-value attributions satisfy local accuracy: the per-feature attributions for a prediction sum to the model output less the base value, making them additive by construction, unlike marginal permutation importance | Lundberg, S. M. & Lee, S.-I. (2017), "A Unified Approach to Interpreting Model Predictions", *NIPS 2017*, 4768–4777. arXiv:1705.07874 | Verified; the basis for the caution that summing permutation importances across a group is a heuristic guard, not a decomposition |
| Permutation importance originates as the random-forest variable importance measure | Breiman, L. (2001), "Random Forests", *Machine Learning* 45(1), 5–32 | Verified as the origin cited by the scikit-learn documentation above |

## Regulatory & Operational Notes

No jurisdiction-specific rule governs which features an alpha model carries. Where this
skill touches regulated surface it does so indirectly: removing a feature changes the
model, which is a model-change event for documentation and change-control purposes
(`model-card-documentation-for-trading-models`, `model-versioning-and-rollback`), and
regimes such as MiFID II RTS 6 and SEC Rule 15c3-5 require that changed trading logic be
tested before deployment. A pruning decision is therefore not complete at the point the
report recommends it — the reduced feature set must be refitted and re-validated
out-of-sample before it reaches production.

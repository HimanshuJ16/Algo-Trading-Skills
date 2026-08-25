---
name: feature-importance-drift-monitoring
description: Use when a deployed trading model's feature-importance ranking must be
  compared against its training baseline, to detect regime-driven reordering of
  predictive drivers and raise a revalidation/retraining alert before performance
  decay shows up in PnL.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- feature-drift
- spearman-rank-correlation
- model-governance
- concept-drift
- shap
- permutation-importance
brokers_frameworks:
- SHAP
- scikit-learn permutation_importance
- XGBoost
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a live ML trading model has a recorded training-time feature-importance profile and a way to recompute that profile on production data. Feature importance reorders as market regimes turn — macro rate sentiment displacing micro price momentum, realized-vol features displacing trend features across a volatility shock. That reordering is visible in the importance ranking *before* it is visible in a Sharpe ratio computed over a window long enough to be statistically meaningful, which is what makes it a useful leading indicator for scheduling revalidation.

Two detectors run per audit: rank agreement across the whole common feature set (tie-corrected Spearman $\rho_{\text{rank}}$), and a share-loss check on the top-N baseline features. The second exists because a model can lose its single most predictive feature while the other 200 features keep their relative order and $\rho$ stays near 1.0.

## When NOT to Use

- **As a performance monitor.** A perfectly stable importance ranking is entirely compatible with losing money, and a reshuffled ranking is compatible with a profitable model. This measures *what the model is leaning on*, not whether it is right. Pair it with live PnL and accuracy monitoring — see `strategy-performance-decay-detection-vs-market-wide-decay` and `model-staleness-detection`.
- **When baseline and live importances come from different methods.** Gain/impurity importance from a booster and mean $|\text{SHAP}|$ from an explainer measure different things; scikit-learn documents impurity importance as "strongly biased" toward high-cardinality features. Normalising to shares removes the *scale* difference but not the definitional one. Same method, same kind of data, both sides.
- **On a feature set dominated by correlated features.** Permutation importance splits credit between correlated features and the split is unstable across samples, so `rsi_14` and `rsi_21` will swap ranks on noise alone. Cluster and monitor one representative per cluster, or accept a permanently depressed $\rho$ that alerts on nothing real.
- **On a handful of features.** At $M = 3$ the only attainable values of $\rho$ are $\{-1, -0.5, +0.5, +1\}$, so a 0.70 threshold degenerates to "identical ordering or alert". At $M = 4$ a uniformly random reordering still clears 0.70 one time in six. The engine refuses fewer than 3 common features; it cannot make 4 features informative.
- **As an auto-redeploy trigger.** Retraining an ML component is a change to a live trading algorithm. ESMA lists "retraining or modifying machine learning components" among the change types that should prompt retesting, and requires material changes to be timestamped, approved and recorded. Alert → change control → test → controlled deployment; never alert → deploy.

## Prerequisites

- Baseline training feature importance map $\{f_i: I_{\text{base}, i}\}$, recorded in the model registry alongside the model version it belongs to.
- Live production feature importance map $\{f_i: I_{\text{live}, i}\}$ recomputed on a production window, **by the same method** as the baseline (permutation importance on held-out data, or mean $|\text{SHAP}|$ — one or the other, consistently).
- Both maps non-negative and finite. Clip negative permutation importances to $0.0$ first: a negative value means the feature scored no better than noise, which is a floor, not a rank below zero.
- Explicit zero entries for features the model no longer uses. An explainer that silently omits zero-importance features turns a stable model into a feature-set mismatch on every run.
- Calibrated thresholds. $\rho_{\text{min}} = 0.70$ and an 80% share-drop trigger are library defaults with no regulatory or industry backing — see `references/standards.md`.

## Workflow

1. **Validate both importance profiles before ranking anything**:
   - Reject non-finite values, negative values, empty maps, and all-zero maps. A `NaN` sorts unpredictably and an all-zero profile has no ordering at all; either one produces a plausible-looking coefficient computed from nothing.
   - **Decision point — an exception here is a monitoring failure, not a passing grade.** Escalate it exactly as you would an alert. Code that wraps the audit in `try/except` and continues on has silently disabled the control.

2. **Normalise each profile to shares of its own total importance**:
   $$s_i = \frac{I_i}{\sum_j I_j}$$
   - Magnitude comparisons are only meaningful between shares. Comparing a gain-based baseline summing to 100 against a mean $|\text{SHAP}|$ profile summing to 0.01 reports every feature as having lost ~99.99% of its importance.

3. **Reconcile the two feature sets**:
   - Compute the common set, the baseline-only set (dropped from live), and the live-only set (new in live), plus the overlap ratio $|common| / |union|$.
   - **Decision point — a top-N baseline feature absent from the live profile is degradation, not an omission.** Intersecting the maps and correlating the survivors returns a high $\rho$ over the features that remain, which reads as stability precisely when the most severe drift has occurred.
   - **Decision point — a feature appearing only in the live profile means the deployed feature set no longer matches the registered baseline.** That is a deployment/versioning problem to resolve before any drift reading is meaningful.

4. **Assign mid-ranks and compute the tie-corrected rank correlation**:
   - Rank features by share, descending, with $1$ = most important; tied features receive the average of the positions they span.
   - $\rho_{\text{rank}}$ is the Pearson correlation of the two rank vectors. The shortcut $\rho = 1 - \frac{6\sum d_i^2}{M(M^2-1)}$ is valid **only for distinct integer ranks** and is wrong under ties — and importance vectors tie constantly, since every unused feature sits at exactly $0.0$.
   - **Decision point — if either rank vector is constant, $\rho$ is undefined**, not $1.0$. Every feature tied on one side means that side carries no ordering; the engine raises rather than reporting perfect stability.

5. **Run the top-N share-degradation audit**:
   - For each of the top-N baseline features, compute $r_i = s_{\text{live}, i} / s_{\text{base}, i}$ and flag $r_i < 1 - \text{max drop}$ (default: flag below $0.20$, i.e. a drop beyond 80%). The boundary is exclusive — exactly 80% does not trigger.
   - Rank the top-N over the *whole* baseline profile so a feature the live profile dropped is still recognised as top-N.

6. **Decide and record**:
   - Any of $\rho_{\text{rank}} < \rho_{\text{min}}$, a degraded top-N feature, or an overlap breach $\implies$ `FEATURE_DRIFT_ALERT_TRIGGERED`, with every triggering reason recorded, not just the first.
   - Otherwise $\implies$ `FEATURE_STABILITY_NORMAL`.
   - **Decision point — one breached window is a signal to investigate, not a mandate to retrain.** The engine is stateless and has no de-bouncing; if window-to-window noise is material, require K consecutive breaches before opening a change request.

7. **Audit Report Generation**: output a structured `FeatureDriftAuditReport` carrying $\rho$, the per-feature rank/share detail, the degraded set, the dropped/new feature sets, the overlap ratio, top-N churn, and every trigger reason — the evidence an RTS 6 Article 9 validation report needs.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using the untied shortcut formula on tied importances**: $1 - 6\sum d^2 / (M(M^2-1))$ is only correct for distinct integer ranks. Applied to positionally-assigned ranks over tied values, the result depends on the order features happen to sit in the dictionary — the same importance structure returned $-0.5$ under one set of feature names and $-1.0$ under another before this was fixed. A monitoring metric that moves when you rename a column is not measuring drift.
- **Comparing raw importance magnitudes across metrics or scales**: baseline gain importances summing to 100 against live mean $|\text{SHAP}|$ summing to 0.01 flags every top feature as ~100% degraded. Every run alerts, the alert gets muted, the control is gone.
- **Silently intersecting the feature maps**: if the live profile omits the feature that carried 70% of baseline importance, the correlation over what remains is $1.0$ and the status reads stable. Reconcile the sets explicitly and treat a dropped top feature as the most severe degradation there is.
- **Treating a `NaN` importance as a small number**: sorting a list containing `NaN` yields an arbitrary order, and the resulting $\rho$ looks like a normal number. Reject non-finite values at the boundary.
- **Reading "no common features" or "one common feature" as stable**: with $M \le 2$ there is nothing to correlate. Fail closed and escalate.
- **Monitoring distribution drift only (PSI/KS) without importance drift**: input distributions can be perfectly stationary while the model's top predictive feature loses all signal power — and vice versa. They are complementary detectors, not substitutes.
- **Chasing rank churn in the noisy tail**: with 500 features of which 480 sit near zero importance, the tail reshuffles on sampling noise every window and drags $\rho$ down while the top of the ranking is rock solid. Monitor the model's material features and read `top_n_rank_churn` alongside $\rho$.
- **Wiring the alert straight into an automated retrain-and-deploy**: ESMA's supervisory briefing lists retraining an ML component as a change type warranting retesting, and warns that a series of small unchecked recalibrations can accumulate into an untested material change in model output. The alert opens a change request; it does not close one.
- **Citing a regulatory basis for the 0.70 threshold**: there isn't one. No regulator publishes a feature-importance drift metric or threshold.

## Verification

- Instantiate `FeatureImportanceDriftMonitorEngine(min_spearman_rank_threshold=0.70, max_degradation_drop_pct=0.80)` with baseline importances (`rsi_14`: 0.40, `volatility_20d`: 0.30, `trend_50d`: 0.20, `sentiment_score`: 0.10).
  - **Scenario 1 — stable** (live 0.38 / 0.32 / 0.18 / 0.12): ordering is unchanged, so $\rho_{\text{rank}} = 1.0$ exactly and the status is `FEATURE_STABILITY_NORMAL` with `top_n_rank_churn == 0`.
  - **Scenario 2 — regime reversal** (live 0.05 / 0.20 / 0.30 / 0.45): the ranking is exactly reversed. Hand-derived, $d = (3, 1, -1, -3)$, $\sum d^2 = 20$, $M = 4$, so $\rho_{\text{rank}} = 1 - \frac{6 \times 20}{4 \times 15} = -1.0$. Status `FEATURE_DRIFT_ALERT_TRIGGERED`, with `rsi_14` degraded (share $0.40 \to 0.05$, an 87.5% loss).
- Tie correction: baseline $(0.6, 0.3, 0.1)$ against live $(0.25, 0.25, 0.5)$ must return $-\sqrt{3}/2 \approx -0.8660$ regardless of what the features are named. Renaming a feature must not change $\rho$.
- Scale invariance: a gain-based baseline summing to 100 and a mean $|\text{SHAP}|$ live profile summing to 0.01 with identical proportions must report $\rho = 1.0$ and **no** degraded features.
- Negative checks — each must raise: a `NaN`/`inf` importance, a negative importance, an all-zero profile, an empty map, a non-numeric value, fewer than 3 common features, and a constant rank vector passed to `compute_spearman_rank_correlation`.
- Boundary: a top-N feature whose share drops by exactly 80% must **not** be flagged; 82% must.
- Run `python scripts/test_feature_drift_monitor.py` and confirm 100% pass rate.

## Related Skills

- `explainability-for-live-trading-signals`
- `feature-selection-stability-across-folds`
- `model-staleness-detection`
- `concept-drift-vs-staleness-differentiation`
- `model-versioning-and-rollback`
- `model-card-documentation-for-trading-models`

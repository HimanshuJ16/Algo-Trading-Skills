---
name: explainability-for-live-trading-signals
description: Use when deploying ML trading models to reconcile local feature attributions
  (SHAP/contributions) against the score the model actually emitted, rank the drivers,
  and write a human-readable audit record for every live signal
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- explainable-ai
- shap-values
- signal-attribution
- compliance-audit
brokers_frameworks:
- SHAP
- Captum
- scikit-learn
- XGBoost
- Custom Explainers
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a black-box or complex quantitative model emits live trading signals and someone — a risk officer during an incident, a model-governance reviewer, a supervisor requesting records — will later need to know *which inputs produced this specific score*. A raw $+0.82$ Buy with no attribution is unreviewable after the fact.

The skill consumes an attribution vector $\{\phi_i\}$ produced by an upstream explainer (SHAP, Captum, an EBM shape function, or a linear model's $w_i x_i$), **reconciles it against the score the model actually emitted**, ranks the drivers, narrates them, and writes an append-only audit record. Reconciliation is the point: an attribution vector that does not reproduce the model's output is not an explanation of that signal, and a log full of unreconciled explanations is worse than no log because it looks authoritative.

## When NOT to Use

- **As a substitute for computing attributions.** This module does not run SHAP. If you have no $\phi_i$, you have nothing to reconcile.
- **As evidence that a model is correct, safe, or causal.** $\phi_i$ measures a feature's contribution to *this model's* output under the chosen background distribution. It says nothing about whether the feature causes the price move or whether the model is right. Attribution is not causation, and a coherent explanation of a bad signal is still a bad signal.
- **As a pre-trade risk control.** Explanations describe; they do not block. Exposure caps, drawdown limits and kill switches must sit out-of-band — see `kill-switch-and-drawdown-circuit-breakers`.
- **On the latency-critical path without measuring it.** Exact per-signal TreeSHAP costs real time. Where the explanation is for post-hoc audit, compute and log it asynchronously — see `model-inference-latency-budget-for-live-trading`.
- **To detect model decay.** Reconciliation catches a broken *explainer*, not a stale *model*. Use `model-staleness-detection` and `feature-importance-drift-monitoring`.
- **As an immutable ledger.** `log_explainable_signal` appends JSONL to a local file. Retention-grade immutability needs WORM storage, object-lock, or a hash-chained ledger.

## Prerequisites

- The feature vector actually fed to the model for this instance, $\{f_1: v_1, \dots, f_M: v_M\}$.
- The local attributions $\phi_i$ for the same instance.
- The explainer's base value $\phi_0 = E[f(z)]$ — for SHAP, `explainer.expected_value` **from the same explainer instance** that produced the $\phi_i$.
- **The score the model actually emitted**, $\hat{Y}$, in the same output space as $\phi_0$ and $\phi_i$.

## Workflow

1. **Capture the prediction and the features together**:
   - Record $\hat{Y}$ as emitted, plus $X_t$ as fed. Do not re-run the model to obtain $\hat{Y}$ for the log — a re-run can silently use a different model version than the one that traded.

2. **Obtain local attributions in a known output space**:
   - **Decision point — establish the output space before anything else.** `shap.TreeExplainer` on an XGBoost `binary:logistic` model explains the **raw margin (log-odds)** by default, so $\phi_0 + \sum \phi_i$ equals the log-odds, *not* the probability from `predict_proba`. Reconcile in margin space, or construct the explainer with `model_output="probability"` (which requires `feature_perturbation="interventional"`) and reconcile in probability space. Never mix.
   - **Decision point — the base value is not a free parameter.** `interventional` and `tree_path_dependent` perturbation give different `expected_value`s *and* different $\phi_i$. Pass the `expected_value` of the instance that produced these $\phi_i$; a hand-chosen constant will fail the gate, which is the correct outcome.

3. **Run the additivity gate**:
   $$\hat{Y} \stackrel{?}{=} \phi_0 + \sum_{i=1}^M \phi_i \quad\text{within}\quad \text{abs\_tol} + \text{rel\_tol}\cdot|\hat{Y}|$$
   - **Decision point — a gate failure is a model-governance incident, not a warning.** It means the attribution vector belongs to a different model version, a different feature vector, or a different output space. `explain_signal` does not raise (that would destroy the evidence); it returns `reconciled=False` with a signed `reconciliation_error` and an `UNRECONCILED` banner on the summary. **Gate on `explanation.reconciled` before showing anyone the drivers**, and record the failure either way.
   - **Decision point — loosen tolerances deliberately, never disable the gate.** Float32 or GPU-trained tree ensembles genuinely need slack; shap's own TreeExplainer assertion allows ~1% relative. Raise `reconciliation_rel_tol` explicitly and record that you did.

4. **Rank drivers and disclose what was left out**:
   - Sort by $|\phi_i|$; ties break by feature name so the same input always yields the same record.
   - **Decision point — top-$N$ is a summary, not the explanation.** With 200 features, three drivers can be a few percent of total attribution magnitude. Report `attribution_coverage` and `residual_contribution` alongside them, and keep the full $\{\phi_i\}$ vector in the record.

5. **Narrate by alignment with the signal, not by sign**:
   - On a BUY, positive contributions *drove* it and negative ones *offset* it. On a SELL, the negative contributions drove it. Labelling every negative contribution as an "offset" tells an incident reviewer the opposite of what happened.

6. **Write the compliance record (`log_explainable_signal`)**:
   - Append a single JSONL line carrying the UTC timestamp (epoch **and** ISO-8601), symbol, action, the emitted score, the reconstructed score, the base value, the reconciliation verdict, the ranked drivers, and the **complete** contribution vector — a reviewer must be able to re-derive $\phi_0 + \sum \phi_i$ from the record alone.
   - Pass `executed_action` where the strategy's real decision is known, so the record proves the explanation belongs to the trade that happened.

> Full step-by-step procedure: see `references/workflows.md`.
> Attribution methods and regulatory touchpoints: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deriving the score from the attributions.** If the pipeline computes $\hat{Y} := \phi_0 + \sum \phi_i$ instead of comparing against the emitted score, the additivity check can never fail — a broken explainer produces a confident, well-formatted explanation of a signal the model never emitted, and any BUY/SELL label derived from that reconstruction is fiction. Take $\hat{Y}$ from the model.
- **Reconciling across output spaces.** Log-odds $\phi_i$ against a `predict_proba` output is the single most common silent failure; $\sigma(\phi_0 + \sum\phi_i) = p$, but $\phi_0 + \sum\phi_i \neq p$.
- **Mismatched feature names.** An attribution keyed `rsi` against a feature vector keyed `rsi_14` is an explainer/feature-store drift bug. Defaulting the missing value to $0.0$ writes a *false input value* into a compliance record; reject the mismatch instead.
- **Silent NaN.** A single `NaN` contribution makes the sum `NaN`; `NaN >= buy_threshold` and `NaN <= sell_threshold` are both `False`, so a corrupt attribution vector lands in the log as a clean `HOLD`. Reject non-finite values at the boundary.
- **Global importance in place of local attribution.** Gini/split-count importance is a property of the trained model, not of this instance. It cannot answer "why did *this* order fire".
- **A scale-blind materiality cut-off.** A hard-coded $\pm 0.001$ floor drops every driver of a model scored in basis points. Default to keeping all non-zero contributions; set a threshold only when a sampling explainer (KernelSHAP) genuinely produces noise, and set it relative to the score's scale.
- **Presenting top-$N$ as the whole story.** Without coverage and residual, three drivers out of two hundred read as a complete explanation.
- **Naive local timestamps.** A local-time stamp cannot be aligned with order records kept under MiFID II RTS 25 clock-synchronisation rules and shifts silently across a DST boundary. Stamp in UTC.
- **Holding the caller's dict by reference.** Storing `contributions_dict` without copying lets the caller mutate a written audit record after the fact.
- **Calling an append-only file immutable.** Say what it is; a reviewer who believes the log is tamper-evident when it is not draws the wrong conclusion from it.

## Verification

- Feed a linear model's exact attributions ($\phi_i = w_i x_i$, $\phi_0 = b$) with the true $\hat Y$: confirm `reconciled` is `True`, `reconciliation_error` $\approx 0$, and `attribution_coverage` matches the hand-computed $|\phi|$ share of the listed drivers.
- **Negative case (the one that matters):** hold the attributions fixed and pass a different `model_prediction`. Confirm `reconciled` is `False`, the error equals the hand-computed difference, `signal_action` follows the *emitted* score rather than the reconstruction, and the summary starts with `UNRECONCILED`.
- Confirm log-odds contributions reconciled against $\sigma(\cdot)$ of their own sum fail the gate.
- Confirm a SELL signal names its negative contributions under "driven by", not "offset by".
- Confirm NaN/Inf contributions, an empty attribution vector, and a contribution naming an unknown feature each raise `SignalExplainerError`; confirm inverted thresholds (`sell_threshold >= buy_threshold`) raise at construction.
- Confirm the JSONL record contains `all_contributions` and `base_value`, and that `base_value + sum(all_contributions.values())` re-derives `score` from the persisted line alone.
- Run `python -m unittest discover -s skills/explainability-for-live-trading-signals/scripts` and confirm 100% pass rate.

## Related Skills

- `explainable-boosting-machines-for-regulated-signals`
- `model-card-documentation-for-trading-models`
- `feature-importance-drift-monitoring`
- `feature-store-for-live-and-backtest-parity`
- `structured-logging-for-post-incident-forensics`

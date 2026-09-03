# Deep Workflow Reference — explainability-for-live-trading-signals

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Capture the emitted prediction and the features together:**
   - Record $\hat{Y}$ **as the live model emitted it**, alongside the exact feature
     vector $X_t$ that produced it and the model version identifier.
   - Do not re-run the model to obtain $\hat{Y}$ for the log. A re-run can pick up a
     newer artefact than the one that traded, and the log then documents a model that
     never saw the market.

2. **Obtain local attributions $\phi_i$ in a known output space:**
   - Fix the output space first. `shap.TreeExplainer` defaults to the raw margin, which
     for XGBoost `binary:logistic` is log-odds, not probability.
   - Take $\phi_0$ from `explainer.expected_value` **of the same explainer instance**.
     `interventional` and `tree_path_dependent` perturbation produce different base
     values and different $\phi_i$.
   - For Integrated Gradients, $\phi_0$ is the baseline's output $f(x')$, and the
     additivity residual scales with the integration step count.

3. **Run the additivity gate:**
   - Compute $\text{reconstructed} = \phi_0 + \sum_i \phi_i$ with a compensated sum
     (`math.fsum`) so the check is not itself the source of the error.
   - Compare against $\hat{Y}$ within
     $\text{abs\_tol} + \text{rel\_tol}\cdot|\hat{Y}|$.
   - On failure, record `reconciled=False`, the signed error, and the tolerance. Do
     **not** raise, and do not drop the record — an unreconciled explanation is the
     evidence of a governance incident and must survive in the log.
   - Triage a failure in this order: (a) output-space mismatch (log-odds vs
     probability); (b) $\phi_0$ from a different explainer instance or background
     dataset; (c) model version drift between the scoring artefact and the explainer
     artefact; (d) feature-vector drift (different values, or different ordering, fed
     to model and explainer); (e) genuine numerical slack from a float32 model.

4. **Classify the action from the emitted score only:**
   - $\hat{Y} \geq$ buy threshold $\Rightarrow$ BUY; $\hat{Y} \leq$ sell threshold
     $\Rightarrow$ SELL; otherwise HOLD. Thresholds are inclusive at the boundary and
     `sell_threshold` must be strictly below `buy_threshold`, or the BUY branch
     shadows SELL and every score is labelled BUY.
   - Never classify from the reconstruction: that lets a broken explainer relabel a
     trade direction in the audit record.
   - Where the strategy's real decision is known, pass it as `executed_action`. A
     mismatch against the threshold-derived action means the explanation may not
     belong to the trade that happened, and is flagged in the record and the summary.

5. **Rank drivers and disclose the remainder:**
   - Split by sign against `materiality_threshold` (default $0$: keep everything
     non-zero), sort by magnitude, break ties by feature name for determinism.
   - Compute `residual_contribution` (attribution not shown) and
     `attribution_coverage` ($|\phi|$ share of the listed drivers). A top-3 summary of
     a 200-feature model can cover a few percent of total magnitude; the record must
     say so.

6. **Narrate by alignment with the signal direction:**
   - BUY: positive contributions are "driven by", negative are "offset by".
   - SELL: negative contributions are "driven by", positive are "offset by".
   - HOLD: neutral phrasing — "largest positive" / "largest negative" — because
     neither side triggered anything.
   - Unreconciled: prefix an explicit `UNRECONCILED` banner carrying both scores and
     the error, so no reader can mistake the drivers for a valid explanation.

7. **Write the compliance record (`log_explainable_signal`):**
   - One JSONL line per signal, appended, flushed and `fsync`'d so a crash cannot leave
     a torn line.
   - The line carries the UTC timestamp (epoch and ISO-8601), symbol, action,
     `executed_action`, emitted score, reconstructed score, base value, reconciliation
     verdict/error/tolerance, ranked drivers, residual, coverage, unattributed
     features, the natural-language summary, and the **complete** $\{\phi_i\}$ vector.
   - Completeness is the test: a reviewer must be able to re-derive
     $\phi_0 + \sum \phi_i$ from the persisted line alone, without access to the
     running system.

## Known Failure Modes

- **Reconstructing the score instead of reconciling it.** The pipeline computes
  $\hat{Y} := \phi_0 + \sum\phi_i$, so the additivity identity holds by construction and
  the check is vacuous. Every downstream artefact — the action label, the driver
  ranking, the audit trail — then describes a score the model may never have emitted.
- **Log-odds attributions reconciled against a probability.** $\sigma(\phi_0 + \sum\phi_i)
  = p$, but $\phi_0 + \sum\phi_i \neq p$. Passes review by eye, fails the gate.
- **Explainer/feature-store name drift.** `rsi` vs `rsi_14`. Defaulting the missing
  value to $0.0$ writes a false feature value into a compliance record.
- **Silent NaN to HOLD.** A `NaN` contribution makes the sum `NaN`; both threshold
  comparisons return `False`, so the corrupt vector is recorded as a clean HOLD.
- **Global importance substituted for local attribution.** Gini or split-count
  importance is a property of the trained model, not of the instance, and cannot answer
  "why did *this* order fire".
- **Scale-blind materiality floor.** A hard-coded $\pm 0.001$ cut-off silently empties
  the driver list for a model scored in basis points.
- **Naive local timestamps.** Unalignable with order records kept under MiFID II
  RTS 25, and shifted by an hour twice a year.
- **Attribution read as causation.** A coherent explanation of a bad signal is still a
  bad signal; $\phi_i$ describes the model, not the market.

## Production Implementation Reference

- Reference code: `scripts/signal_explainer.py`
  (`LiveSignalExplainer`, `SignalExplanation`, `FeatureContribution`,
  `log_explainable_signal`, `SignalExplainerError`).
- Automated unit tests: `scripts/test_signal_explainer.py`.
- Run with `python -m unittest discover -s skills/explainability-for-live-trading-signals/scripts`.

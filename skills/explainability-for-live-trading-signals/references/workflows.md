# Deep Workflow Reference — explainability-for-live-trading-signals

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Capture Live Model Prediction & Input Features:**
   - Ingest raw prediction score $\hat{Y}$ and feature values $X_t$.

2. **Compute Local Feature Attributions ($\phi_i$):**
   - Decompose score relative to model base value: $\hat{Y} = \text{BaseValue} + \sum \phi_i$.

3. **Classify & Rank Drivers:**
   - Sort features into top bullish ($\phi_i > 0$) and top bearish ($\phi_i < 0$) drivers.

4. **Generate Natural Language Audit String:**
   - Format summary string combining action, prediction score, top positive drivers, and offset negative drivers.

5. **Log Compliance Audit Entry:**
   - Serialize explanation object into JSON audit log (`to_json_audit()`).

## Failure Modes Observed in Production

- **Black-Box Signal Transmission:** Operating live ML signals without feature attribution logging.
- **Global vs Local Attribution Misunderstanding:** Using static global feature importance (e.g. tree depth frequency) instead of local SHAP instance contributions.

## Production Implementation Reference

- Reference code: `scripts/signal_explainer.py` (`LiveSignalExplainer`, `SignalExplanation`, `FeatureContribution`).
- Automated unit tests: `scripts/test_signal_explainer.py`.

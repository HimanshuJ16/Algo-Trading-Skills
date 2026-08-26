# Model Card Pre-Flight Checklist

Sign-off artefact for issuing a model card. A card is **complete** when every
mandatory section has content — which is not the same as the model being approved
to trade. Record both below.

## Scope of this card

- [ ] Purpose of this card is recorded, and it is **not** "so the pipeline has a gate to check".
- [ ] `applicable_frameworks` lists only what this firm is actually subject to (internal MRM policy, RTS 6 Article 9, client mandate). No framework has been inherited from the tool.
- [ ] If SR 26-2 is listed: this entity is a banking organization within its stated audience. If not, it is listed as a structural reference only.
- [ ] The subject is a model as SR 26-2 defines one, not a deterministic rule-based process with no statistical or financial theory behind it.

## 1. Model Details

- [ ] `model_id`, `name`, `version`, `author` populated; `version` matches the artefact actually deployed.
- [ ] `model_type` is correct — `ML_ALPHA` and `EXECUTION_ALGO` reach the order path and require documented kill-switch triggers.
- [ ] `asset_class` recorded.

## 2. Intended Use & Out-of-Scope Uses

- [ ] `intended_use` states what the model is for in one sentence.
- [ ] `out_of_scope_uses` names the regimes, instruments, sessions and horizons never evaluated.
- [ ] Each boundary is actionable ("20-day ADV below $5m"), not vague ("illiquid names").

## 3. Training Data & Feature Lineage

- [ ] Every training data source listed, including vendor and dataset version.
- [ ] Training window recorded as ISO-8601 dates, start before end.
- [ ] Each feature carries its **transformation**, not just its name — a reader can rebuild it from the card.
- [ ] `label_definition` recorded, including horizon and any barrier method.
- [ ] `retraining_cadence` recorded.

## 4. Evaluation & Performance

- [ ] `evaluation_window` recorded.
- [ ] `is_out_of_sample` set deliberately, not left at its `False` default by accident.
- [ ] **Human check the engine cannot do**: the evaluation window follows the training window and does not overlap it.
- [ ] All metrics finite; drawdown and win rate within [0, 100]; capacity non-negative.
- [ ] Metrics come from the model version named in section 1.

## 5. Limitations & Known Failure Modes

- [ ] At least one genuine failure mode documented. "None known" is not an answer.
- [ ] Untested conditions named explicitly (halt-and-reopen, first minutes after the open, regime not present in the training window).

## 6. Governance, Validation & Monitoring

- [ ] `is_validated_by_mrm` reflects a validation that actually happened.
- [ ] `validator` names the individual or function that signed off.
- [ ] `validation_date` is an ISO-8601 date.
- [ ] Independence of the validator meets **this firm's** policy bar. (SR 26-2 no longer imposes a structural independence requirement; it weighs "rigor and effectiveness of the review rather than... organizational structure".)
- [ ] `monitoring_signals` describes what production monitoring actually watches.
- [ ] Kill-switch triggers documented for any order-affecting model, and implemented — the engine checks that they are *written down*, not that they exist in code.

## Findings review

- [ ] `blocking_gaps` is empty, or each gap has a named owner and a date.
- [ ] **Every** entry in `advisory_findings` has been read, including on a card reporting `MODEL_CARD_COMPLETE`.
- [ ] Any breach of firm review thresholds has been escalated per risk policy — the thresholds are firm policy and carry no regulatory weight.
- [ ] If figures are not out-of-sample, that has been accepted explicitly by the reviewer.

## Issue and retain

- [ ] Markdown card and `to_json()` payload both stored against this model version.
- [ ] Two identical runs produce identical output (determinism confirmed).
- [ ] Re-issue trigger agreed: any change that "may alter the behaviour, risk profile, or compliance posture" of the model means a version bump and a new card, not an edit in place.
- [ ] Accumulated recalibrations since the last card have been reviewed for whether they now amount to a material change.

## Sign-off

| Role | Name | Date | Card status recorded |
|---|---|---|---|
| Model owner / developer | | | |
| Validator | | | |
| Risk / MRM | | | |

> Documentation completeness is not a deployment authorisation. Deployment approval is recorded separately.

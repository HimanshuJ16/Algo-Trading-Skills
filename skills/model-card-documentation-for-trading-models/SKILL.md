---
name: model-card-documentation-for-trading-models
description: >-
  Use when a trading model is promoted, revalidated or handed to someone who did not
  build it; a structured record of identity, intended and out-of-scope uses, training
  data, evaluation, known failure modes and owner.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: model-card, model-governance, model-risk, sr-26-2, mifid-ii-rts-6, feature-lineage, documentation-generator
  brokers_frameworks: "SR 26-2 interagency model risk management guidance; MiFID II RTS 6 (EU) 2017/589; Mitchell et al. Model Cards (FAT* 2019); Python standard library (dataclasses, json, datetime)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when a quantitative trading model is about to be promoted to production, re-validated, or handed to someone who did not build it, and you need a **structured record of what the model is, what it may not be used for, what it was trained on, how it was evaluated, where it breaks, and who signed it off** — plus an explicit list of which of those are still blank.

The engine answers exactly one question: *does this card have an empty mandatory section?* That is a documentation check. It is deliberately not a compliance verdict, because no static generator can establish one.

## When NOT to Use

- **As evidence of regulatory compliance.** SR 26-2 — the interagency model risk management guidance issued 17 April 2026 by the Federal Reserve, FDIC and OCC, superseding SR 11-7 and SR 21-8 — states it "does not set forth enforceable standards or prescriptive requirements; accordingly, non-compliance with this guidance will not result in supervisory criticism against a banking organization," and that it "is expected to be most relevant to banking organizations with over $30 billion in total assets." A hedge fund, prop shop or asset manager is not a banking organization; it cannot be "SR 26-2 compliant" and cannot be found non-compliant with it. This skill borrows SR 26-2's *structure*, not its authority.

- **As a deployment gate on its own.** `MODEL_CARD_COMPLETE` means every mandatory section has content. It says nothing about whether the content is true, whether the validation described actually happened, or whether the model should trade. Wiring a promotion pipeline to gate solely on this status converts a documentation check into a fabricated approval.

- **As a substitute for the RTS 6 annual self-assessment.** An EU investment firm engaged in algorithmic trading must, under RTS 6 (Commission Delegated Regulation (EU) 2017/589) Article 9, perform an annual self-assessment and validation and issue a validation report covering its algorithmic trading systems, its governance and approval framework, its business continuity arrangements, and its overall compliance with MiFID II Article 17. ESMA's supervisory briefing of 26 February 2026 states that firms "shall conduct and document the required self-assessment on an article-by-article basis, covering all relevant articles of RTS 6." A model card is one input to that; it is not that.

- **For a model that is not a model.** SR 26-2 defines a model as "a complex quantitative method, system, or approach that applies statistical, economic, or financial theories to process input data into quantitative estimates," and expressly excludes "simple arithmetic calculations" and "deterministic rule-based processes and software where there are no statistical, economic, or financial theories underpinning their design or use." A hard-coded TWAP slicer may fall outside that definition; a card for it is still useful internally, but do not present it as an MRM artefact.

- **To evaluate a generative or agentic AI system.** SR 26-2 footnote 3 places generative and agentic AI outside its scope as "novel and rapidly evolving," while confirming its principles apply to "traditional statistical and quantitative models and non-generative, non-agentic AI models."

## Prerequisites

- **Identity** (`ModelIdentity`): `model_id`, `name`, `version`, `author`, `model_type` (`'ML_ALPHA'`, `'EXECUTION_ALGO'`, `'RISK_MODEL'`), `asset_class`, `intended_use`, `out_of_scope_uses`.
- **Feature lineage** (`ModelTrainingProvenance`): training data sources, ISO-8601 training window, per-feature transformations, label definition, retraining cadence. Optional in the signature so pre-2.0 callers still run — but its absence is a blocking gap, because a card without lineage cannot be reproduced.
- **Metrics** (`ModelPerformanceMetrics`): `sharpe_ratio`, `sortino_ratio`, `max_drawdown_pct` (0–100), `annual_return_pct`, `win_rate_pct` (0–100), `capacity_usd` (≥ 0), plus `evaluation_window` and `is_out_of_sample`. Every value must be finite.
- **Limitations** (`ModelLimitations`): known failure modes and the signals ongoing monitoring actually watches.
- **Governance** (`ModelGovernanceConfig`): `is_validated_by_mrm`, `validation_date` (ISO-8601), `validator`, `kill_switch_triggers`, `applicable_frameworks`. **All default to unasserted.**
- **Review policy** (`ReviewThresholds`, optional): your firm's Sharpe floor, drawdown ceiling and revalidation cadence. Defaults are illustrative and carry no regulatory weight.

## Workflow

1. **Decide what the model card is for before generating one.** If the answer is "so the pipeline has something to gate on," stop. The status field is a completeness check; treating it as an approval is the failure this skill is built to prevent, and the card itself carries a disclaimer saying so.

2. **Declare the frameworks that actually apply to your firm, and only those.**
   - **Decision point — do not inherit a framework from the tool.** Pre-2.0 this engine carried a `sr_compliant` boolean that the caller asserted, the engine never checked, and the card printed as `COMPLIANT`. It is gone. `applicable_frameworks` is a factual declaration of what your firm is subject to (internal MRM policy, RTS 6 Article 9, a client mandate), rendered verbatim and never adjudicated.

3. **Fill in lineage before metrics.** A Sharpe ratio with no feature definitions behind it is unauditable. `feature_definitions` should carry the transformation, not just the name — `ret_5m: log(close_t / close_t-5), shifted one bar`, not `ret_5m`. If a reader cannot rebuild the feature from the card, the lineage section is decorative.

4. **State the evaluation basis honestly.**
   - **Decision point — `is_out_of_sample` defaults to `False`, and that default is loud.** A card that omits it earns an advisory finding saying the figures may be in-sample. That is the correct default: silence about the evaluation basis should read as in-sample, not as out-of-sample.

5. **Record governance as fact, not intent.** `is_validated_by_mrm=True` obliges you to name a `validator` and supply an ISO-8601 `validation_date`; either missing is a blocking gap. Note that SR 26-2 softened its predecessor here — "The quality of validation process depends on the rigor and effectiveness of the review rather than on organizational structure" — so "independent" is your firm's policy bar, not a universal rule.

6. **Pass `as_of_date` when you want staleness checked.** The engine never reads the system clock, so an identical call always produces an identical card. Omitting `as_of_date` skips the staleness check entirely rather than silently comparing against today.

7. **Read both result lists, and do not let one hide the other.**
   - `blocking_gaps` — mandatory sections with no content. These make the card incomplete.
   - `advisory_findings` — the documented model breaching firm policy, in-sample figures, a stale validation. These never make a card incomplete, and they are surfaced on *every* card, including complete ones.
   - **Decision point — a complete card with advisory findings is the most dangerous output to skim.** It reads `MODEL_CARD_COMPLETE` at the top and may still be saying the Sharpe ratio is 0.10 and the drawdown is 40%.

8. **Regenerate on material change.** ESMA's February 2026 briefing defines a material change as "any modification that may alter the behaviour, risk profile, or compliance posture" of an algorithm, requires firms to "timestamp, approve, and record all material changes," and warns that "a series of minor or small changes due to recalibrations could accumulate over time... into a material change in the model output without it being tested." Bump `version` and regenerate; do not edit a published card in place.

> Full procedure: see `references/workflows.md`.
> Standards, citations, and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading `MODEL_CARD_COMPLETE` as approval to trade.** It means no section is empty. It does not mean the contents are true, that the validation happened, or that anyone authorised deployment.
- **Letting a tool issue a compliance verdict.** Pre-2.0 this engine emitted `MODEL_CARD_GENERATED_COMPLIANT` with the note "SR 26-2 compliant" for any firm, against thresholds SR 26-2 does not contain. A model card that overstates its own authority is worse than no model card: it launders an unexamined model as a reviewed one.
- **Governance defaults that fail open.** Pre-2.0 `ModelGovernanceConfig()` defaulted to `is_validated_by_mrm=True` with a hard-coded `validation_date`, so a caller who passed no governance information at all received a card asserting a validation that never occurred. Defaults in a governance tool must assert nothing.
- **Advisory findings computed and then discarded.** Pre-2.0 a sub-threshold Sharpe ratio was recorded internally and dropped whenever the card was otherwise compliant — the reader never saw it. A finding that does not reach the report was never made.
- **NaN passing every threshold.** `nan > 25.0` and `nan < 1.0` are both `False`, so a card with `max_drawdown_pct=nan` was previously reported fully compliant. Non-finite metrics now raise `ModelCardError` rather than sliding through comparisons.
- **Treating a firm risk threshold as a regulatory limit.** SR 26-2 prescribes no Sharpe floor, no drawdown ceiling and no capacity minimum. Presenting `min_sharpe_ratio=1.0` as a regulatory requirement is fabricated regulatory content.
- **Omitting out-of-scope uses.** SR 26-2 §IV: "Using a model beyond its intended purpose introduces additional uncertainty and risk." A card listing only intended uses tells a future reader nothing about the boundary.
- **Feature lineage as a list of column names.** Without the transformation, the card documents that features existed, not what they were.
- **Trusting caller text in an audit artefact.** A model card is evidence; unescaped input could forge headings and table rows. All caller text is escaped and newlines are collapsed.
- **Publishing a card whose evaluation window predates the last retrain.** The engine cannot detect this — it does not cross-check the training window against the evaluation window. That remains a human review step.

## Verification

- **Section contract.** A fully documented card renders all six `REQUIRED_SECTIONS` exactly once each, numbered 1–6, plus an Advisory Findings block — seven `## ` headings in total. (Pre-2.0 the docs claimed "all 6 required MRM sections" while the code emitted three.)
- **No verdict is issued.** Confirm neither the markdown nor the audit note contains "SR 26-2 compliant", "non_compliant" or "non-compliant under", and that `ModelCardReport` exposes no `is_mrm_compliant` attribute.
- **Findings are never swallowed (regression).** With Sharpe 0.10 on an otherwise fully documented card, confirm the status is still `MODEL_CARD_COMPLETE`, that exactly one advisory finding is returned, and that the finding text appears in the rendered markdown.
- **Governance fails closed (regression).** Confirm `ModelGovernanceConfig()` yields `is_validated_by_mrm=False`, `validation_date=None`, empty validator/triggers/frameworks, and that a card built with it is `MODEL_CARD_INCOMPLETE` and renders `NOT VALIDATED`.
- **Non-finite metrics raise (regression).** NaN Sharpe, NaN drawdown, infinite Sharpe, negative drawdown, drawdown > 100, win rate > 100 and negative capacity must each raise `ModelCardError`. Confirm 0.0 drawdown, 100.0 win rate and 0.0 capacity are accepted, and a negative annual return renders.
- **Staleness geometry.** With `validation_date="2026-05-01"` and the 365-day default: `as_of_date="2027-05-01"` (exactly 365 days) is clean, `"2027-05-02"` reports "366 days old", a validation date after the as-of date is flagged, and an unparseable `as_of_date` skips the check without raising.
- **Kill-switch gate is type-aware.** An `ML_ALPHA` with no kill-switch trigger is incomplete; a `RISK_MODEL` with none is complete; whitespace-only triggers do not count.
- **Markdown cannot be forged.** A `name` containing a newline and `## 6. Governance, Validation & Monitoring` must not produce a second heading at line start; a pipe in `author` must not forge a metrics row; a newline in an out-of-scope entry must not split into a second bullet.
- **Agent misuse.** A bare string where a list is required (`out_of_scope_uses="Crypto"`, `kill_switch_triggers="Drawdown > 20%"`) must raise `ModelCardError` telling the caller to wrap it in a list — never be iterated into one bullet per character, which would render as a populated section that is not one.
- **Determinism.** Two identical calls return byte-identical markdown and `to_json()` output.
- Run `python -m unittest discover -s skills/model-card-documentation-for-trading-models/scripts` and confirm a 100% pass rate.

## Related Skills

- `model-versioning-and-rollback`
- `model-staleness-detection`
- `feature-engineering-without-leakage`
- `point-in-time-database-for-ml-training-data`
- `explainability-for-live-trading-signals`
- `backtest-audit-trail-for-regulatory-review`
- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-research-to-production-pipeline-governance`
- `factor-research-multiple-testing-correction`
- `research-idea-pipeline-tracking-and-prioritization`

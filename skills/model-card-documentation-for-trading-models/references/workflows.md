# Workflows for Model Card Generation

## 0. Establish what the card is for

A model card is a record for a future reader — a validator, an auditor, a successor
quant, a regulator's examiner. It is not a gate. Before generating one, confirm
nobody downstream is planning to promote a model on the strength of
`status == "MODEL_CARD_COMPLETE"`. That status means no mandatory section is empty;
it establishes nothing about the truth of the contents.

## 1. Identity and applicable frameworks

Collect `model_id`, `name`, `version`, `author`, `model_type`, `asset_class`.

`model_type` is load-bearing, not decorative: `ML_ALPHA` and `EXECUTION_ALGO` are
listed in `ORDER_AFFECTING_MODEL_TYPES`, and a card for either is incomplete while
its kill-switch conditions are undocumented. A `RISK_MODEL` is exempt from that
particular gate because its output does not itself reach the order path.

Populate `applicable_frameworks` with what your firm is genuinely subject to —
internal MRM policy, RTS 6 Article 9, a client mandate, an exchange membership
condition. The engine renders the list verbatim and adjudicates nothing. There is no
longer a `sr_compliant` flag: a caller-asserted, never-checked boolean that printed
`COMPLIANT` into an audit artefact was a misinformation vector, not a control.

## 2. Intended use and out-of-scope use

`intended_use` is one sentence describing what the model is for. `out_of_scope_uses`
is the boundary — the regimes, instruments, sessions and horizons the model was never
evaluated on.

Both are blocking. SR 26-2 section IV: "Using a model beyond its intended purpose
introduces additional uncertainty and risk." Mitchell et al. §4.2 describes the
out-of-scope section as "inspired by warning labels on food and toys" — the point is
to stop a future reader from a reasonable-looking misuse.

Write boundaries a reader can act on. "Not for illiquid names" is not actionable;
"Not for names with 20-day ADV below $5m" is.

## 3. Feature lineage

`ModelTrainingProvenance` carries the training data sources, the ISO-8601 training
window, the per-feature transformations, the label definition and the retraining
cadence. All are blocking.

Write `feature_definitions` so a reader can rebuild the feature:

    ret_5m: log(close_t / close_t-5), shifted one bar
    rvol_30m: realised vol of 5m returns over trailing 30m

not `ret_5m`, `rvol_30m`. A list of column names documents that features existed.

The engine validates that both window bounds parse as ISO-8601 dates and that the
window does not end before it starts. It does **not** check that the window is
consistent with the evaluation window — see step 4.

## 4. Evaluation basis, then metrics

Record `evaluation_window` and set `is_out_of_sample` deliberately.

`is_out_of_sample` defaults to `False`, and a card that leaves it there earns an
advisory finding saying the figures may be in-sample. That default is intentional:
silence about the evaluation basis should read as in-sample, because that is the more
common and more damaging case.

Every metric must be finite. `max_drawdown_pct` and `win_rate_pct` must lie in
[0, 100]; `capacity_usd` must be non-negative. Violations raise `ModelCardError`
rather than flowing into comparisons — a NaN silently satisfies every threshold test,
which is exactly how a card with `max_drawdown_pct=nan` was previously reported
compliant.

Cross-checking that the evaluation window follows the training window is a human
step. The engine does not do it.

## 5. Limitations and monitoring signals

`known_failure_modes` records where the model is known to degrade — a session, a
regime, a corporate action, an untested path. `monitoring_signals` records what
ongoing monitoring actually watches in production.

Both are blocking, and the monitoring gap is reported against the Governance section
because SR 26-2 section V treats ongoing monitoring as a component of validation
rather than an optional extra.

A card asserting no known limitations is the claim least likely to be true.

## 6. Governance and validation

Setting `is_validated_by_mrm=True` obliges you to name a `validator` and supply an
ISO-8601 `validation_date`. Either missing is a blocking gap, because a bare boolean
is not evidence.

Note what changed in the source material: SR 26-2 dropped its predecessor's structural
independence requirement, stating that "the quality of validation process depends on
the rigor and effectiveness of the review rather than on organizational structure."
Independence remains sound practice and is what most firms' policies require — but
present it as your firm's bar, not as a universal supervisory rule.

Every default in `ModelGovernanceConfig` asserts nothing: `is_validated_by_mrm=False`,
`validation_date=None`, empty validator, triggers and frameworks. Pre-2.0 the defaults
were `True` with a hard-coded date, so a caller who passed no governance information
received a card asserting a validation that had never happened.

## 7. Staleness, if you want it

Pass `as_of_date` to age the validation date against
`ReviewThresholds.max_validation_age_days` (365 by default, anchored to the RTS 6
Article 9 annual cadence for in-scope EU investment firms).

Omit it and the check is skipped. The engine never reads the system clock: an
identical call must yield an identical card, so an audit artefact is reproducible and
two runs a month apart do not silently diverge.

A validation date after the as-of date is flagged rather than treated as fresh. An
unparseable `as_of_date` skips the check without raising, since staleness is an
optional convenience and not a reason to refuse to render a card.

## 8. Read both result lists

    report.blocking_gaps       # empty mandatory sections -> card incomplete
    report.advisory_findings   # policy observations -> never affect completeness

They are separate on purpose, and neither suppresses the other. Pre-2.0 a
below-threshold Sharpe ratio was appended to an internal deficit list that was
discarded whenever the card was otherwise compliant, so the reader saw
"APPROVED ... SR 26-2 compliant" on a model with a Sharpe of 0.10.

The output that most needs care is a **complete card with advisory findings**: it
reads `MODEL_CARD_COMPLETE` at the top and may still report a 0.10 Sharpe and a 40%
drawdown. Completeness and quality are different questions, and this engine only
answers the first.

## 9. Persist and re-issue

`report.markdown_content` is the human artefact; `report.to_json()` is the canonical
sorted-key JSON for audit storage. Store both against the model version.

Regenerate on material change rather than editing a published card. ESMA's February
2026 supervisory briefing defines a material change as "any modification that may
alter the behaviour, risk profile, or compliance posture" of an algorithm, requires
firms to "timestamp, approve, and record all material changes," and warns that small
recalibrations can "accumulate over time... into a material change in the model
output without it being tested." Bump `identity.version` and issue a new card.

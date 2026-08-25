# Pre-Flight / Sign-off Checklist — explainability-for-live-trading-signals

Use this before considering the skill's implementation complete.

## Attribution inputs

- [ ] **Emitted score, not reconstructed:** $\hat{Y}$ is captured from the live model as it fired, not recomputed for the log and not derived from $\sum \phi_i$.
- [ ] **Output space fixed and documented:** raw margin (log-odds) vs probability is stated explicitly, and $\phi_0$, $\phi_i$ and $\hat{Y}$ are all in that same space.
- [ ] **Base value provenance:** $\phi_0$ is `expected_value` from the *same* explainer instance (same `feature_perturbation`, same background dataset) that produced the $\phi_i$.
- [ ] **Model version pinned:** the scoring artefact and the explainer artefact are the same version.

## Reconciliation gate

- [ ] **Gate runs on every signal:** $\phi_0 + \sum \phi_i$ is compared to $\hat{Y}$ within `abs_tol + rel_tol * |Y|`.
- [ ] **Gate can actually fail:** a deliberately mismatched `model_prediction` produces `reconciled=False` with the expected error (if it cannot fail, the check is vacuous).
- [ ] **Tolerances justified:** any loosening beyond the defaults is deliberate, documented, and tied to a float32/GPU model — the gate is never disabled.
- [ ] **Consumers gate on `reconciled`:** no dashboard, alert, or report presents drivers from an unreconciled explanation as valid.
- [ ] **Failures are recorded, not dropped:** unreconciled explanations still reach the log, at `ERROR`, and are routed to model governance.

## Explanation quality

- [ ] **Action follows the emitted score** — never the reconstruction; `executed_action` is supplied where the strategy's real decision is known, and mismatches are flagged.
- [ ] **Direction semantics correct:** on a SELL, negative contributions appear under "driven by", not "offset by".
- [ ] **Truncation disclosed:** `attribution_coverage` and `residual_contribution` accompany every top-$N$ driver list.
- [ ] **Materiality threshold matched to score scale** (default $0$ keeps all non-zero contributions).
- [ ] **Determinism:** the same input produces byte-identical drivers and summary across runs.

## Audit record

- [ ] **Completeness:** `base_value + sum(all_contributions.values())` re-derives `score` from a persisted JSONL line alone, with no access to the running system.
- [ ] **UTC timestamps:** both epoch and ISO-8601 `...Z`; no naive local time anywhere in the record.
- [ ] **No post-hoc mutation:** the stored contribution vector is a copy; mutating the caller's dict does not change a written record.
- [ ] **Storage claim honest:** the JSONL append is described as append-only, not immutable, unless it is actually backed by WORM / object-lock / a hash-chained ledger.
- [ ] **Retention:** the log's retention period matches the obligation that actually binds the entity (e.g. MiFID II Art. 17(2) records for EU investment firms) — confirmed, not assumed.

## Validation

- [ ] **Negative cases raise:** NaN/Inf contribution, NaN feature value, NaN prediction, empty attribution vector, contribution naming an unknown feature, inverted thresholds, `top_n_drivers < 1`.
- [ ] **Automated Testing:** run `python -m unittest discover -s skills/explainability-for-live-trading-signals/scripts` and confirm 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Jurisdiction(s) whose recordkeeping obligations were confirmed: ___________________________

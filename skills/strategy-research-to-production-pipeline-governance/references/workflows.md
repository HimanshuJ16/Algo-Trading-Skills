# Workflows — strategy-research-to-production-pipeline-governance

Deep procedure for one promotion decision. The short form is in `SKILL.md`;
the evidence for every regulatory claim is in `standards.md`.

## 0. Establish the transition being requested

A promotion request is a triple: `(strategy_id, current_stage, target_stage)` plus the
artifact bundle backing it. Resolve `current_stage` from your pipeline's stage store, not
from the submitter — the engine takes the caller's word for it and has no way to detect a
strategy that claims to be in `STAGING_CANARY` while having never left the notebook.

## 1. Stage sequencing

    RESEARCH_BACKTEST → INDEPENDENT_VALIDATION → PAPER_TRADING_SHADOW
                      → STAGING_CANARY → LIVE_PRODUCTION

Compute `target_index - current_index` over `PIPELINE_ORDER`:

| Step | Outcome |
|---|---|
| `+1` | `STAGE_SEQUENCE_GATE` passes |
| `> +1` | Rejected. Name every skipped stage in the failure message. |
| `≤ 0` | Rejected as "not a forward promotion". |

Evaluate this gate **first and independently of the metrics**, and do not let it
short-circuit the remaining gates. Two reasons:

1. A gate that a sufficiently good Sharpe can argue past is not a gate. The whole point is
   that `RESEARCH_BACKTEST → LIVE_PRODUCTION` fails on sequencing at a Sharpe of 4.0.
2. A submitter who gets one complete list of defects fixes them in one cycle. A
   short-circuiting evaluator produces four sequential rejections and trains people to
   treat the gate as an obstacle rather than a review.

Backward transitions are refused deliberately. A rollback has different evidentiary
requirements (open positions, unwind schedule, client notification) and belongs to
`strategy-decommissioning-and-position-unwind-procedure`.

## 2. Reproducibility

Two artifacts must pin the thing being promoted:

- `git_commit_hash` — hexadecimal, 7–64 characters, not all zeros. Reject on any of the
  three. A length-only check (`len >= 7`) accepts `"notahash"` and `"0000000"`, which are
  exactly the placeholders a CI job emits when it cannot resolve a revision — the failure
  case the gate exists to catch.
- `dataset_checksum` — non-blank after trimming. The engine cannot recompute it; it can
  only insist that a value was recorded. Compute it upstream over the exact bytes the
  backtest consumed (see `backtest-determinism-and-reproducibility`).

A promotion that clears this gate is reproducible only in the sense that *someone recorded
what to reproduce*. Actually re-running the backtest at that commit against that dataset is
a separate control.

## 3. Backtest quantitative gates

Sharpe floor and drawdown cap, both compared unrounded, both inclusive at the limit.

**Sign convention.** `backtest_max_drawdown_pct` is a positive magnitude: a 12% drawdown is
`12.0`. Validate `0 ≤ value ≤ 100` at input and raise on breach. Deferring the sign to a
`value <= cap` comparison means a 40% drawdown submitted as `-40.0` passes a 15% cap and is
reported as `DRAWDOWN_GATE` *passed* — the gate actively certifies the worst backtests.

**Non-finite handling.** `NaN` compares `False` against every threshold, so its effect
depends only on which direction a given comparison runs: it fails a `>=` gate and fails a
`<=` gate, but flip either comparison during a refactor and a corrupt metric starts
approving promotions. Raise on non-finite input rather than relying on comparison
direction.

**Raise vs. reject.** Structurally invalid input raises `ValueError`; it does not produce a
rejected decision. A malformed submission is a process error to be fixed by the submitter;
a rejection is a judgement about a strategy. Recording the first as the second pollutes the
strategy's governance history with defects that were never about the strategy.

## 4. Independence

Applies from `PAPER_TRADING_SHADOW` onward — i.e. to every promotion *out of*
`INDEPENDENT_VALIDATION` and to everything after it.

    validator = validator_id.strip()
    author    = author_id.strip()
    reject if not validator            → not independently validated
    reject if validator == author      → self-validation

Trim before comparing, or `"  quant_01  "` defeats the check.

Without this gate the stage named `INDEPENDENT_VALIDATION` imposes no requirement at all:
a strategy can pass through it with no validator recorded, and the stage becomes a box on a
diagram. It is also the cheapest available implementation of segregation of duties — see
`segregation-of-duties-for-custody-operations` for the same principle applied to custody.

The engine compares strings. It cannot verify the validator exists, is independent in fact,
or holds the authority senior management designated. That binding is an organisational
control, not a code control.

## 5. Shadow execution evidence

Required only for entry to `STAGING_CANARY` or `LIVE_PRODUCTION`:

- `paper_trading_days >= min_paper_trading_days`
- `shadow_tracking_error_pct <= max_shadow_tracking_error_pct`

Earlier stages are exempt on purpose: a strategy entering `INDEPENDENT_VALIDATION` has no
paper-trading history, and a gate that cannot be satisfied teaches submitters to fabricate
the field.

**Define the divergence metric before you gate on it.** "Tracking error" canonically means
the annualized standard deviation of active returns. What this gate compares is the
divergence between shadow fills and simulated fills — a different quantity. `5.0` under an
annualized-standard-deviation definition and `5.0` under a mean-absolute-per-fill
definition are not comparable bars. Pick one, write it down, apply it to every strategy, and
record it with the decision. See `backtest-vs-live-performance-divergence-tracking` and
`execution-realistic-simulation`.

## 6. Designated-person sign-off

Entry to `LIVE_PRODUCTION` only. Require **both**:

- `has_risk_committee_signoff is True` — and validate the type. `"pending"` is truthy in
  Python and would grant live-deployment approval on a field whose value says the opposite.
- a non-blank `validator_id` — RTS 6 Art. 5(2) requires that a *person designated by senior
  management* authorise the deployment. A boolean with nobody's name attached does not
  discharge an obligation phrased in terms of a person.

Note the mapping: the regulation names a senior-management-designated person, not a risk
committee. Which committee or role that person sits in is your firm's governance decision.

## 7. Audit record

Record, then hash, then chain.

**Record** an explicit `decided_at_utc` (ISO-8601, timezone-aware). Validate it parses and
carries an offset. A naive "approved at 09:30" cannot be reconciled against exchange session
times or another jurisdiction's records, and is worst around DST transitions. ESMA states
firms "are required to timestamp, approve, and record all material changes" (¶31), so the
timestamp is part of the record, not an implementation detail of the hash.

**Hash** the entire decision into a full 64-character SHA-256 over a canonical JSON
serialisation (`sort_keys=True`, fixed separators): both stages, every artifact value, every
configured threshold, the gate outcomes, the recorded timestamp, the previous digest and
the ledger index.

Two properties this buys, both absent from a hash seeded with an unrecorded clock read:

- **Reproducible.** `verify_audit_hash(decision, artifacts, thresholds)` recomputes it. A
  digest nobody can recompute is decoration, not an audit trail. And on a platform with
  ~15 ms clock granularity, two distinct decisions in the same tick collide to one digest —
  two different promotions appear in the ledger as the same event.
- **Content-binding.** Editing the Sharpe ratio, the validator's name, the sign-off flag or
  the recorded outcome changes the digest. Include the **thresholds** as well: without them,
  the same artifacts judged against a quietly loosened Sharpe floor produce an
  indistinguishable record, and the loosening is precisely what an auditor is hunting for.

**Chain** each entry to its predecessor (`previous_audit_hash`, `GENESIS_HASH` at the head)
and keep `ledger_index` contiguous, so an entry cannot be deleted or reordered undetected.

Record refusals alongside approvals. A trail containing only approvals cannot demonstrate
that anything was ever refused — which is the entire evidentiary value of a gate.

## 8. Persist the ledger outside the strategy owner's control

`verify_ledger()` detects an edit or deletion made without recomputing the chain. It does
not defend against someone who rewrites the whole ledger: with write access and this module,
every downstream digest can simply be recalculated. A hash chain is only as strong as its
anchor.

To get a claim worth making to a supervisor, persist each `audit_trail_hash` where the
strategy owner cannot rewrite it — append-only WORM storage, an audit database with no
`UPDATE` grant, or a periodic digest countersigned by a separate function. See
`audit-logging-for-configuration-changes` and `record-retention-periods-by-jurisdiction`.

## 9. After approval — what this engine does not cover

- **RTS 6 Art. 8 controlled-deployment limits**: predefined caps on instruments traded,
  order price/value/count, strategy positions and venue count. Enforce in the execution
  layer before the first order.
- **Canary sizing and rollback triggers**: `canary-releases-for-strategy-code-changes`,
  `automated-rollback-triggers-on-anomaly-detection`.
- **Post-promotion divergence monitoring**: an approval is a snapshot.
  `backtest-vs-live-performance-divergence-tracking` and `model-staleness-detection` cover
  what happens next.
- **Re-audit on threshold changes**: under the ESMA retesting triggers (¶31), changing a
  risk-control threshold is a material change. Loosening a gate to admit a strategy is a
  governance event that warrants its own review, not a config edit.

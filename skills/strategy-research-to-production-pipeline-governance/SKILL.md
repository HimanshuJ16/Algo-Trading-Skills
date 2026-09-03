---
name: strategy-research-to-production-pipeline-governance
description: >-
  Use when a strategy asks to move one step closer to live capital and someone must say
  yes on the record, enforcing single-step stage sequencing and reproducibility of the
  code and data behind each promotion.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: pipeline-governance, research-to-production, model-validation, reproducibility, shadow-trading, risk-signoff, audit-trail, segregation-of-duties
  brokers_frameworks: "Policy-as-Code Governance; MiFID II RTS 6 (EU 2017/589); FINRA Regulatory Notice 15-09; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a strategy is asking to move one step closer to live capital, and someone has to say yes or no on the record. Strategies promoted straight out of a Jupyter notebook fail for reasons the notebook cannot show: the backtest was tuned on the same data it was scored on, the deployed commit was not the tested commit, the fills the simulator assumed never materialise, or nobody outside the author's own head ever reviewed it.

The engine evaluates one transition at a time against a fixed gate set:

`RESEARCH_BACKTEST` → `INDEPENDENT_VALIDATION` → `PAPER_TRADING_SHADOW` → `STAGING_CANARY` → `LIVE_PRODUCTION`

and appends every decision — approvals *and* refusals — to a hash-chained ledger that a later reader can verify.

Gates by target stage:

| Target stage | Gates evaluated |
|---|---|
| `INDEPENDENT_VALIDATION` | sequencing, reproducibility, Sharpe, drawdown |
| `PAPER_TRADING_SHADOW` | + independent validator recorded (≠ author) |
| `STAGING_CANARY` | + paper-trading duration, shadow divergence |
| `LIVE_PRODUCTION` | + designated-person sign-off |

## When NOT to Use

- **As proof that a strategy is any good.** Every artifact is a number the *caller asserts*. The engine cannot tell an honest out-of-sample Sharpe from an in-sample one, and cannot detect look-ahead bias. Passing means "the paperwork is in order and the claimed metrics clear the bar", never "the edge is real." Pair with `lookahead-bias-elimination`, `walk-forward-validation-setup`, and `multi-year-regime-coverage-requirement`.
- **As the record of which stage a strategy is actually in.** The engine is stateless about strategy identity: it checks that the *requested* transition is a single forward step, not that the earlier stages truly completed. Your pipeline's stage store remains the source of truth.
- **As the rollback or shutdown path.** Backward and same-stage transitions are refused by design. Pulling a live strategy is `strategy-decommissioning-and-position-unwind-procedure`; halting one mid-session is `kill-switch-and-drawdown-circuit-breakers`.
- **As RTS 6 Art. 8 deployment limits.** Art. 8 requires predefined limits on instruments traded, order price/value/count, strategy positions, and venue count before deployment. This engine gates the *decision* and holds none of those limits — enforce them in the execution layer.
- **As a supervisor-proof immutable ledger on its own.** The hash chain detects an edit or deletion made without recomputing the chain. Anyone who can rewrite the whole ledger can recompute every downstream digest. Anchor `audit_trail_hash` in storage the strategy owner cannot rewrite — see `audit-logging-for-configuration-changes`.
- **Where the numbers are the point.** The four thresholds are house defaults, not standards. See `references/standards.md` before quoting any of them externally.

## Prerequisites

- `StagePromotionArtifacts` for the transition: `git_commit_hash` (7–64 hex characters, non-zero), `dataset_checksum` (non-blank), `backtest_sharpe` (finite), `backtest_max_drawdown_pct` (**positive magnitude in [0, 100]** — a 12% drawdown is `12.0`, never `-12.0`), `shadow_tracking_error_pct` (non-negative), `paper_trading_days` (non-negative int), `has_risk_committee_signoff` (a real `bool`), `author_id`, `validator_id`.
- A settled, documented definition of `shadow_tracking_error_pct`. Canonically, *tracking error* is the annualized standard deviation of active returns; what this gate compares is a divergence between shadow fills and simulated fills. An annualized standard deviation of return differences and a mean absolute per-fill price divergence are different quantities and `5.0` means something very different under each. Fix one definition, apply it to every strategy you compare.
- Thresholds you are prepared to defend in writing. The defaults (Sharpe ≥ 1.50, drawdown ≤ 15%, divergence ≤ 5%, ≥ 14 paper days) are heuristics with no regulatory basis.
- An identity scheme where `author_id` and `validator_id` are distinct people. The engine enforces that the strings differ; it cannot authenticate that either person exists or holds the designated authority.

## Workflow

1. **Stage Sequencing Gate** — evaluated first, and independently of the metrics.
   - Reject unless the transition advances **exactly one** position in the pipeline order.
   - **Decision point — a stellar backtest does not buy a shortcut.** `RESEARCH_BACKTEST → LIVE_PRODUCTION` fails on sequencing alone with a Sharpe of 4.0, and the failure message names each stage that was bypassed. Skipping stages is the failure mode this engine exists to prevent; if it can be argued away with good metrics it prevents nothing.
   - **Decision point — a refusal still evaluates every other gate.** A sequencing failure does not short-circuit the rest. The submitter gets one complete list of what is wrong, not a one-at-a-time drip of rejections across four resubmissions.

2. **Reproducibility Gate** — verify the commit and dataset are actually pinned.
   - **Decision point — a plausible-looking string is not a commit id.** Validate hexadecimal, length 7–64, and reject an all-zero digest. `"notahash"` and `"0000000"` are precisely what a CI job emits when it cannot resolve a revision, and a length check alone waves both through.

3. **Backtest Quantitative Gates** — Sharpe floor and drawdown cap.
   - **Decision point — enforce the drawdown sign convention, do not assume it.** `-40.0` satisfies `value <= 15.0`. Reject any negative drawdown at input rather than certifying the worst backtests in the book.
   - **Decision point — non-finite metrics raise, they do not fail.** `NaN` compares `False` against every threshold, so a corrupt metric passes or fails depending only on which way the comparison happens to run. A malformed submission is a process error, distinct from a strategy that was evaluated and refused; the ledger must not blur the two.

4. **Independence Gate** — applies from `PAPER_TRADING_SHADOW` onward.
   - Require a `validator_id` that is non-blank and differs from `author_id` after trimming.
   - **Decision point — this gate is what makes `INDEPENDENT_VALIDATION` mean anything.** Without it, promoting *out of* the independent-validation stage requires no validator at all, and the stage is a label on a diagram rather than a control.

5. **Shadow Execution Evidence Gates** — entry to `STAGING_CANARY` or `LIVE_PRODUCTION`.
   - Require paper-trading duration ≥ minimum and shadow divergence ≤ cap.
   - **Decision point — earlier stages are exempt on purpose.** A strategy entering `INDEPENDENT_VALIDATION` has no paper-trading history yet; demanding 14 days there makes the gate unsatisfiable and teaches submitters to fabricate the field.

6. **Designated-Person Sign-Off Gate** — entry to `LIVE_PRODUCTION` only.
   - Require both the sign-off flag and a named approver. RTS 6 Art. 5(2) requires that "a person designated by the senior management of the investment firm shall authorise the deployment" — the obligation is a *named person*, so a boolean with nobody's name attached does not discharge it.

7. **Audit Record and Ledger** — record the decision so it can be checked later.
   - Record an explicit, timezone-aware `decided_at_utc`; hash the **entire** decision (both stages, every artifact, every configured threshold, the gate outcomes, the timestamp, and the previous entry's digest) into a full 64-character SHA-256; chain it to the preceding entry.
   - **Decision point — bind the thresholds, not just the artifacts.** Otherwise the same artifacts judged against a quietly loosened Sharpe floor produce an indistinguishable record, and the loosening is the thing an auditor is looking for.
   - **Decision point — record refusals too.** A trail containing only approvals cannot demonstrate that anything was ever refused, which is the entire evidentiary value of a gate.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Documenting sequential gatekeeping without implementing it**: the pipeline diagram says five stages; the code checks the artifacts and approves whatever transition it is handed. `RESEARCH_BACKTEST → LIVE_PRODUCTION` then passes with good numbers, skipping independent validation, shadow trading and canary in one hop — the exact deployment this skill exists to block.
- **Accepting a negatively-signed drawdown**: a 40% drawdown submitted as `-40.0` satisfies `<= 15.0`. The gate approves the worst backtest in the book and reports `DRAWDOWN_GATE` as passed.
- **Letting the author be the validator**: `author_id == validator_id` is self-certification. The stage is called independent validation and the sign-off obligation names a *designated* person; a string comparison is the cheapest control that makes either true.
- **An audit hash nobody can recompute**: seeding a digest with an unrecorded `time.time()` produces a value that looks cryptographic and proves nothing — no auditor can reproduce it. Worse, on a platform with ~15 ms clock resolution two distinct decisions in the same tick collide to one digest, so two different promotions appear in the ledger as the same event.
- **An audit hash that omits the evidence**: hashing only the strategy id, stage names, commit hash and approval boolean leaves the Sharpe ratio, the tracking error, the paper-trading days and the validator's name unprotected. Every number that justified the approval can be edited afterwards without disturbing the digest.
- **A status code with the failure count baked in**: emitting `"REJECTED_GATES_FAILED (3)"` means no caller can match on the string, and any documented constant like `REJECTED_LOW_SHARPE` is never actually produced. Branch on a stable enum; read `failed_gates` for the detail.
- **A truthy non-boolean sign-off**: `has_risk_committee_signoff = "pending"` is truthy in Python and grants live deployment approval. Validate the type, not just the value.
- **Naive timestamps in a promotion record**: "approved at 09:30" without an offset cannot be reconciled against exchange session times or another jurisdiction's records, and is worst around DST transitions — exactly when it matters.
- **Quoting the thresholds as industry standards**: no regulator publishes a minimum backtest Sharpe, a maximum shadow divergence, or a minimum paper-trading duration. ESMA states the "scope, frequency, and intensity of testing vary significantly across the industry" and recognises "the need for proportionality." A number presented to a supervisor as a standard is a claim you will be asked to source.
- **Treating a passing audit as evidence the strategy works**: every artifact is asserted by the submitter. The engine validates paperwork, not edge.
- **Forgetting that changing a threshold is itself a material change**: under the ESMA briefing's retesting triggers, changing risk-control thresholds warrants re-testing. Loosening the Sharpe floor to get a strategy through is a governance event, not a config tweak.

## Verification

- Walk the full pipeline one stage at a time with a passing bundle (Sharpe 1.82, drawdown 11.4%, divergence 3.1%, 18 paper days, distinct author/validator, sign-off present) and confirm all four transitions approve, with gate counts rising 4 → 5 → 7 → 8 as the later gate sets engage.
- Submit `RESEARCH_BACKTEST → LIVE_PRODUCTION` with the same passing bundle ⟹ `is_approved` is `False`, `STAGE_SEQUENCE_GATE` fails, and the message names all three skipped stages. Repeat with a backward (`LIVE_PRODUCTION → RESEARCH_BACKTEST`) and a same-stage transition ⟹ both refused.
- Boundary checks, each pair passing then failing: Sharpe `1.50` / `1.4999`; drawdown `15.0` / `15.0001`; divergence `5.0` / `5.0001`; paper days `14` / `13`. A drawdown of `15.004` renders as `15.0%` in the message and must still fail.
- Set `author_id == validator_id` (including with whitespace padding) ⟹ `INDEPENDENCE_GATE` fails. Promote out of `INDEPENDENT_VALIDATION` with a blank `validator_id` ⟹ refused.
- Reproducibility: `"notahash"`, `"0000000"`, a 6-character hash and a 65-character hash are all refused; a 7-character, a 40-character and an uppercase hex hash pass; a whitespace-only `dataset_checksum` is refused.
- Negative checks that must raise `ValueError`: a negative or >100 drawdown, a negative divergence, `NaN`/`inf` in any metric, negative or non-integer paper days, `has_risk_committee_signoff="pending"`, a blank `author_id` or `strategy_id`, a stage passed as a plain string, and a naive or malformed `decided_at_utc`. Constructor: a drawdown cap of `0.0` or `1000.0`, a negative divergence cap, negative or non-integer minimum paper days.
- Audit integrity: the hash is 64 characters and `verify_audit_hash` returns `True`; a second engine given identical inputs and the same timestamp produces an identical digest; altering any of the Sharpe, divergence, paper days, sign-off flag, validator id, dataset checksum, recorded outcome, or the thresholds makes verification return `False`; two distinct decisions never share a digest.
- Ledger integrity: entry 0 chains from `GENESIS_HASH`, indices are contiguous, refusals are recorded alongside approvals, and `verify_ledger` returns `False` after an entry is edited, deleted, or reordered.
- Run `python -m unittest discover -s skills/strategy-research-to-production-pipeline-governance/scripts` and confirm 100% pass.

## Related Skills

- `strategy-committee-governance-for-capital-allocation-decisions`
- `new-strategy-onboarding-checklist`
- `paper-to-live-promotion-checklist`
- `canary-releases-for-strategy-code-changes`
- `backtest-determinism-and-reproducibility`
- `research-environment-vs-production-environment-parity`
- `audit-logging-for-configuration-changes`
- `risk-control-configuration-change-approval-workflow`
- `strategy-decommissioning-and-position-unwind-procedure`
- `model-versioning-and-rollback`

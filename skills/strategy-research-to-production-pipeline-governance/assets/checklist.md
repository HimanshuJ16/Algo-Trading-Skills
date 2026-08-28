# Promotion Sign-Off Checklist

One transition, one checklist. Attach the completed copy to the ledger entry.

**Strategy:** ________________  **From:** ________________ → **To:** ________________
**Author:** ________________  **Validator / designated approver:** ________________
**Decision timestamp (UTC, with offset):** ________________
**Audit hash (64 hex):** ________________

## Sequencing

- [ ] The transition advances **exactly one** stage. (Skipping stages is refused regardless of how good the metrics are; a rollback is a different workflow.)
- [ ] `current_stage` was read from the pipeline's stage store, not asserted by the submitter.

## Reproducibility

- [ ] `git_commit_hash` is hexadecimal, 7–64 characters, and not all zeros — not a `"notahash"` / `"0000000"` placeholder from a CI job that could not resolve a revision.
- [ ] The commit is reachable and tagged in the repository, not a dangling local revision.
- [ ] `dataset_checksum` was computed over the exact bytes the backtest consumed, and the dataset at that checksum is still retrievable.

## Backtest evidence

- [ ] `backtest_sharpe` is **out-of-sample**, and the out-of-sample window was never used for parameter selection.
- [ ] `backtest_max_drawdown_pct` is a **positive magnitude** (a 12% drawdown is `12.0`, never `-12.0`).
- [ ] No metric is `NaN` or infinite.
- [ ] The thresholds applied are recorded with the decision, and are the ones the committee agreed — not loosened for this submission.

## Independence

- [ ] `validator_id` is non-blank and is a **different person** from `author_id`.
- [ ] That person actually reviewed the work, and holds authority designated by senior management (the engine compares strings; it cannot check either).

## Shadow execution *(entry to `STAGING_CANARY` or `LIVE_PRODUCTION`)*

- [ ] Shadow paper trading ran for at least the required consecutive days, on live market data.
- [ ] The definition of `shadow_tracking_error_pct` is written down, is the same one used for every other strategy compared against this bar, and is recorded with the decision.
- [ ] The shadow run used the production code path, not a research re-implementation of it.

## Sign-off *(entry to `LIVE_PRODUCTION` only)*

- [ ] `has_risk_committee_signoff` is a real boolean `True` — not a truthy string such as `"pending"`.
- [ ] The approver is named in the record. A boolean with nobody's name attached does not discharge an obligation phrased in terms of a person (RTS 6 Art. 5(2)).

## Audit record

- [ ] `decided_at_utc` is ISO-8601 **with a UTC offset** — no naive timestamps.
- [ ] The audit hash is the full 64 characters and `verify_audit_hash` returns `True`.
- [ ] The hash covers the artifacts **and** the thresholds, so a later loosening is detectable.
- [ ] The entry chains to its predecessor and `verify_ledger()` passes.
- [ ] The digest is persisted somewhere the strategy owner cannot rewrite (append-only / WORM / no `UPDATE` grant).
- [ ] Refusals are being recorded too, not only approvals.

## Outside this engine — confirm separately before the first live order

- [ ] RTS 6 Art. 8 controlled-deployment limits are declared and enforced in the execution layer: number of instruments, order price/value/count, strategy positions, number of venues.
- [ ] Canary size, rollback trigger and kill-switch integration are configured.
- [ ] Post-promotion divergence monitoring is live, with an owner.
- [ ] Any jurisdiction-specific external gate is cleared (e.g. exchange approval / empanelment in India under the SEBI 4 Feb 2025 circular).

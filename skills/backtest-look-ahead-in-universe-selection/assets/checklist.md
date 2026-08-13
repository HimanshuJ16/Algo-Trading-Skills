# Pre-Flight / Sign-off Checklist — backtest-look-ahead-in-universe-selection

Use this before considering the skill's implementation complete.

## Data Contract

- [ ] **Selection Rules Documented:** All universe selection rules and their data inputs written down.
- [ ] **Point-in-Time Sources Verified:** Membership history comes from a point-in-time source, not a current-constituents table applied backwards.
- [ ] **Publication Dates Independently Sourced:** `data_publication_date` is the later of the index announcement instant and the ranking data's as-of stamp — never copied from `added_date`. Confirm no `Vacuous Publication Dates` warning.
- [ ] **Removal Date Convention Converted:** Vendor end dates normalised to the half-open `[added_date, removed_date)` convention; inclusive last-day-of-membership dates shifted by one session.
- [ ] **Overlapping Intervals Merged:** Zero `Duplicate Symbol` violations; no ticker double-weighted.
- [ ] **Ticker Reuse Resolved:** Recycled symbols disambiguated upstream; the auditor cannot detect them.

## Timestamps

- [ ] **Decision Instant Set:** `snapshot_date` carries the actual rebalance decision instant, not a bare date.
- [ ] **Single Timezone Convention:** Every timestamp naive-or-aware consistently; audit does not raise `UniverseAuditError`.
- [ ] **Same-Day Rule Chosen Explicitly:** `date_granular_publication_is_end_of_day` left enabled, or its override justified in writing.

## Audit Execution

- [ ] **Full Sweep, Not One Date:** Audit run at every rebalance instant in the backtest window, not a single sample snapshot.
- [ ] **CI Gated on Violations:** Automation fails on `result.has_violations`, and switches on `finding.finding_type` rather than substring-matching messages.
- [ ] **Warnings Triaged:** Every `Survivorship Bias` warning either explained (snapshot near the database build date) or investigated.
- [ ] **Negative Control Passes:** A deliberately future-dated `data_publication_date` produces a `Lookahead Leak`, proving the audit can actually fail.
- [ ] **Evidence Retained:** Vendor identifier, membership file version, snapshot instants, auditor settings, and findings persisted with the backtest results.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtest-look-ahead-in-universe-selection/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

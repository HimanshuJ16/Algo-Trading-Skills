# Deep Workflow Reference — backtest-look-ahead-in-universe-selection

This file holds the full technical procedure referenced by `SKILL.md`. Load this when actually
implementing the skill, not just when deciding whether it applies. Timestamp conventions and
their sourcing live in `references/standards.md`.

## Full Procedure

1. **Fix the decision instant for every rebalance.**
   - Decide when the universe is frozen (for example the 09:30 ET open of the rebalance
     session) and pass that `datetime` as `snapshot_date`. A bare `date` is rejected: it
     carries no decision instant, and the whole audit depends on one.
   - Normalise every timestamp to a single timezone convention, UTC recommended. Mixed naive
     and aware values raise `UniverseAuditError`.

2. **Build one `ConstituentRecord` per membership interval.**
   - `added_date` / `removed_date` come from the index provider's effective dates. Membership
     is half-open `[added_date, removed_date)`; convert an inclusive vendor end date by adding
     one session first.
   - `data_publication_date` is the **later** of the membership announcement instant and the
     as-of stamp of the ranking data used to justify inclusion. This is the field that makes
     the audit meaningful; copying `added_date` into it makes every check pass by construction,
     and the auditor emits a `Vacuous Publication Dates` warning when every record does so.
   - Merge overlapping intervals for the same ticker before auditing. Two rows for one symbol
     double-weight the name and are reported as a `Duplicate Symbol` violation.

3. **Configure the auditor.**
   ```python
   auditor = UniverseLookaheadAuditor(
       survivorship_warning_threshold=50,             # heuristic tripwire, tune per index
       date_granular_publication_is_end_of_day=True,  # fail-closed on date-only stamps
   )
   ```
   - Leave the end-of-day rule enabled unless a stored midnight genuinely means 00:00. With it
     enabled, a stamp of midnight on the snapshot date is read as end of that day and flagged;
     a stamp of midnight on any strictly earlier date still passes.

4. **Audit each snapshot and classify the result.**
   ```python
   result = auditor.audit_universe_snapshot(snapshot_date, constituents)
   if result.has_violations:
       raise SystemExit("\n".join(result.lookahead_violations))
   ```
   - `has_violations` covers only the deterministic classes (`Lookahead Leak`,
     `Future Addition`, `Zombie Asset`, `Duplicate Symbol`). Gate CI on it.
   - `is_clean` additionally requires zero heuristic warnings. Use it for research sign-off,
     and expect the survivorship warning to fire legitimately on snapshots near the database
     build date.
   - Iterate `result.findings` and switch on `finding.finding_type` rather than substring
     matching the rendered messages.

5. **Sweep the whole backtest, not one date.**
   - Run the audit at every rebalance instant in the study window. A single clean snapshot says
     nothing about the rest of the history, and survivorship only becomes visible across a
     window that contains removals.

6. **Retain the evidence.**
   - Persist the vendor identifier, the membership file version, the snapshot decision instant,
     the auditor settings, and the findings alongside the backtest results, so a reviewer can
     replay the universe exactly. See `backtest-audit-trail-for-regulatory-review`.

## Known Failure Modes

- **Current-membership table applied backwards.** The most severe case: today's constituent
  list joined to every historical date. Every bankruptcy and delisting silently disappears.
- **Publication date sourced from the effective date column.** The audit turns green and proves
  nothing. Detected as `Vacuous Publication Dates`.
- **Rank date treated as a knowledge date.** Eligibility for the 2026 Russell reconstitution is
  measured at the 2026-04-30 close, but preliminary lists are not communicated until
  2026-05-22. A backtest that rebalances into the new membership in early May is trading a list
  nobody had.
- **Announcement/effective conflation in the other direction.** Holding a name from its
  announcement date rather than its effective date. S&P DJI announced Tesla's addition on
  2020-11-16 for an effective date of 2020-12-21 — five weeks of exposure the index did not
  have.
- **Midnight publication stamps.** A date-only stamp read as 00:00 authorises a full extra
  session of trading on data that may not have appeared until that evening.
- **Inclusive vendor removal dates.** Feeding a last-day-of-membership end date into the
  half-open convention produces a false `Zombie Asset` on every name's final session, which
  trains reviewers to ignore the finding.
- **Mixed timezone awareness.** Naive snapshot against aware records raises `TypeError` deep in
  a comparison; the auditor rejects it up front with a clear message instead.
- **Ticker reuse.** A recycled symbol reassigned to a different issuer after a delisting joins
  two unrelated price histories. Timestamps alone cannot detect this; resolve identity upstream
  with `reference-data-symbol-mapping-across-vendors`.

## Production Implementation Reference

- Reference code: `scripts/universe_lookahead_auditor.py` (`UniverseLookaheadAuditor`,
  `ConstituentRecord`, `AuditResult`, `AuditFinding`, `UniverseFindingType`,
  `UniverseAuditError`).
- Automated unit tests: `scripts/test_universe_lookahead_auditor.py`.

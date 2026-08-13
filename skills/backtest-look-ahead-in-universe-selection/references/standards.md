# Standards for Point-in-Time Universe Selection

## Time Axes

Universe membership is governed by two independent axes. Collapsing them into one column is
the root cause of most universe-selection look-ahead.

| Axis | Field | Meaning | Read at |
|---|---|---|---|
| Knowledge | `data_publication_date` | Latest instant among every input used to justify membership: the index provider's announcement **and** the as-of stamp of the ranking data (market cap, float, ADV). | End of day when date-granular |
| Effective | `added_date` / `removed_date` | When membership actually began and ended. | Start of day |

This mirrors the knowledge-time / valid-time split in
`backtest-database-schema-for-point-in-time-queries`; the terminology is deliberately shared.

## Membership Interval Convention

The auditor uses one explicit convention:

- Membership is the **half-open interval `[added_date, removed_date)`**.
- A name whose `added_date` equals the snapshot instant **is** a member. S&P Dow Jones Indices
  states constituent changes are "effective prior to the opening of trading" on the effective
  date, so the name trades in the index for that whole session.
- A name whose `removed_date` equals the snapshot instant is **not** a member; it is reported
  as a `Zombie Asset`.
- `removed_date is None` means the record knows of no removal.
- If a vendor stores `removed_date` as the *last day of membership* (inclusive), add one
  session before auditing. Feeding an inclusive end date into a half-open auditor produces a
  false `Zombie Asset` on every name's final day.

## Announcement Versus Effective Date

The gap between announcement and effect is days to weeks, and everything in it is knowable but
not yet tradable as index membership:

| Event | Announced / determined | Effective |
|---|---|---|
| Tesla added to the S&P 500 | 2020-11-16 press release | "prior to the open of trading on Monday, December 21" |
| 2026 Russell US reconstitution | Rank day 2026-04-30 (eligibility from market cap at that close); preliminary lists communicated from 2026-05-22, updated 05-29, 06-05, 06-12, 06-18 | Takes effect after the US market close on 2026-06-26 |

Consequences for a backtest:

- A rebalance dated between announcement and effect **knows** the change but must not yet hold
  the new constituent. The auditor separates these as `Lookahead Leak` (did not know) versus
  `Future Addition` (knew, not yet effective).
- Rank day is **not** a knowledge date. Nothing about 2026 Russell membership is knowable on
  2026-04-30 even though eligibility is measured from that close.
- Unscheduled removals (bankruptcy, merger completion, delisting) can be announced with little
  or no lead time, so announcement and effective date legitimately coincide there.

## Timestamp Granularity

- The snapshot argument must be a `datetime` carrying the **decision instant** of the rebalance
  (for example 09:30 ET on the rebalance date), not a bare date. A `date` is rejected.
- A `data_publication_date` of exactly midnight is treated as date-granular and read as end of
  that day. A date-only stamp cannot establish that the data existed before the session's
  decision instant, so the conservative reading is the only safe default.
- Effective dates are *not* normalised to end of day; start of day is their correct reading
  under the "effective prior to the open" convention above.
- Every timestamp must share one timezone convention with the snapshot. Mixed naive and
  timezone-aware values raise `UniverseAuditError` rather than producing a partial report.

## Finding Classification

| Finding | Class | Meaning |
|---|---|---|
| `Lookahead Leak` | Violation | Selection data became available after the decision instant. |
| `Future Addition` | Violation | Membership became effective after the snapshot. |
| `Zombie Asset` | Violation | Membership had already ended at the snapshot. |
| `Duplicate Symbol` | Violation | Symbol appears more than once; the name is double-weighted. |
| `Survivorship Bias` | Heuristic warning | Large universe with zero closed membership intervals. |
| `Vacuous Publication Dates` | Heuristic warning | Every `data_publication_date` equals its `added_date`, so the leak check cannot fail. |

- Gate automation on `AuditResult.has_violations`. It covers only the deterministic classes.
- `AuditResult.is_clean` additionally requires zero heuristic warnings; use it for research
  sign-off, not as a hard CI gate.

## Heuristic Limits

- The survivorship tripwire (`survivorship_warning_threshold`, default 50) is an operational
  default with no empirical derivation. Tune it per index.
- It produces expected false positives for a snapshot near the database build date, where no
  constituent has been removed *yet*. It is only informative for snapshots well in the past.

## Scope Boundary

This auditor checks membership timestamps for internal consistency against a stated decision
instant. It does **not** recompute rankings, verify that the market-cap or ADV values used were
the as-of-date values, prove a vendor's data is genuinely point-in-time, resolve ticker reuse
across issuers, or model delisting settlement. Those require the vendor's as-of snapshots and
the sibling skills listed in `SKILL.md`.

## Sources

- S&P Dow Jones Indices press release, "Tesla Set to Join S&P 500", 2020-11-16 —
  <https://press.spglobal.com/2020-11-16-Tesla-Set-to-Join-S-P-500>
- FTSE Russell / LSEG, "Russell Reconstitution" 2026 schedule —
  <https://www.lseg.com/en/ftse-russell/russell-reconstitution>
- LSEG, "More key facts ahead of the 2026 Russell US Indexes reconstitution" —
  <https://www.lseg.com/en/insights/ftse-russell/more-key-facts-ahead-of-the-2026-russell-us-indexes-reconstitution>

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls
up across the full skill library.

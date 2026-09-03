# Standards — backtest-database-schema-for-point-in-time-queries

## Schema contract

| Requirement | Specification | Enforcement |
|---|---|---|
| Knowledge-time column | `known_at` — instant the value became externally known | Mandatory on all PIT tables |
| Valid-time column | `valid_from` — instant the value came into effect | Mandatory on all PIT tables |
| Revision counter | `revision` (0 = as originally reported, 1+ = restatements) | Breaks ties between same-day corrections deterministically |
| Temporal filter | `known_at <= as_of` AND `valid_from <= as_of` | Enforced at database view level |
| Restatement handling | Preserve prior rows; insert a new row with a later `known_at` | Never overwrite historical records |
| Timestamp format | Zero-padded, fixed-width ISO 8601, normalised to UTC | Validated on write; malformed values rejected, not stored |
| Index | Composite `(symbol, field, known_at)` | Equality columns lead, range column trails |

## Temporal model

The two axes correspond to the temporal model standardised in **SQL:2011**
(ISO/IEC 9075:2011), which defines *application-time period tables* (valid time
— "the history of the thing out there"), *system-versioned tables* (system time
— "the history of when you changed the database"), and *bitemporal tables*
combining both.

One deliberate deviation: `known_at` is **not** SQL:2011 system time. System
time records when a row was written to the database, which for a backfilled
research store is meaningless — it is exactly the `created_at` trap this skill
warns against. `known_at` is a domain-supplied knowledge-time axis carrying the
vendor publication or filing-release instant, so it must be stored as ordinary
application data rather than delegated to a `SYSTEM_VERSIONING` clause.

Support for SQL:2011 temporal features is partial and uneven across engines —
MariaDB and DB2 implement both axes, SQL Server implements system time only,
Oracle application time only, and PostgreSQL neither natively. Treat the
predicates above as the portable contract and implement them explicitly rather
than assuming engine support.

## Timestamp comparison

**RFC 3339 §5.1 (Ordering)** states that timestamps may be sorted as strings and
yield a time-ordered sequence only when "the time zones of the dates and times
are the same (e.g., all in UTC), expressed using the same string (e.g., all 'Z'
or all '+00:00'), and all times have the same number of fractional second
digits."

A PIT store ingesting several vendor feeds cannot assume any of those three
conditions hold. Parse to an absolute instant and compare instants. Two
concrete failures of raw string comparison:

- `2023-02-01T09:00:00-05:00` string-compares *before* `2023-02-01T12:00:00Z`,
  but is 14:00Z — two hours later. A record published in the future is admitted.
- `2023-9-01` string-compares *after* `2023-10-01`, so it is excluded from every
  as-of query on or after `2023-09-15` and the figure silently disappears.

## Date-granularity widening rule

Where a value carries a date but no clock time, widen in the direction that
excludes rather than admits:

| Value | Widened to | Rationale |
|---|---|---|
| Record `known_at`, `valid_from` | Last instant of that day | Publication time within the day is unknown; most releases land after the close |
| Query `as_of`, `valid_as_of` | First instant of that day | Assume the earliest moment the query could be issued |

Consequence: at date granularity, `known_at == as_of` returns **no** match.
Store real intraday timestamps where same-session availability matters.

## Known limitations

- No `valid_to` / tombstone column, so end-of-validity and hard deletes are not
  expressible; the reference store models open-ended validity only.
- `scripts/pit_schema.py` is an in-memory reference implementation with a linear
  scan per query. It demonstrates the predicates; it is not a storage engine.

## Sources

| Claim | Source |
|---|---|
| String sorting of timestamps requires identical offset and fractional precision | RFC 3339 §5.1, *Date and Time on the Internet: Timestamps* (July 2002) — https://www.rfc-editor.org/rfc/rfc3339.html |
| Application-time / system-time / bitemporal table definitions | SQL:2011 (ISO/IEC 9075:2011); survey of engine support — https://illuminatedcomputing.com/posts/2019/08/sql2011-survey/ |
| Equality-leading composite index bounds the scanned range | PostgreSQL Documentation, *Multicolumn Indexes* — https://www.postgresql.org/docs/current/indexes-multicolumn.html |

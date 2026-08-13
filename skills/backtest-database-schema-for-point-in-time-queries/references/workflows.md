# Deep Workflow Reference — backtest-database-schema-for-point-in-time-queries

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Design PIT Schema**
   - `known_at` — knowledge time: when the value became externally known.
   - `valid_from` — valid (application) time: when the value came into effect.
   - `revision` — 0 for as-originally-reported, incrementing per restatement.
   - Table is append-only. A correction is a new row, never an `UPDATE`.

2. **Normalize Timestamps On Ingest**
   Convert every vendor timestamp to a single canonical UTC ISO 8601 form and
   reject malformed values at the write boundary. Deferring this to query time
   means a bad row is already in the table, and the resulting bias is silent —
   see `references/standards.md` for the two concrete failure modes.

3. **Query with As-Of Semantics**
   Restrict retrieval to `known_at <= as_of_date AND valid_from <= valid_as_of`,
   where `valid_as_of` defaults to `as_of_date`. Keep the two axes separately
   addressable: "what did we know on date K about the period ending on date V"
   is a legitimate and distinct question from the default current-state query.

   Resolve multiple surviving candidates by `(known_at, valid_from, revision)`,
   with a final deterministic tiebreak on ingest order. Without the `revision`
   term, two corrections published the same day resolve by whichever row the
   engine happened to return first.

4. **Audit Lookahead Leakage**
   Run the naive "latest row wins" query alongside the PIT query for the same
   key and date. Where the two disagree, the naive path would have traded on a
   number that did not exist yet. Report the naive value, not just a boolean —
   an auditor needs to know which figure was wrong and by how much.

5. **Index for Performance**
   `(symbol, field, known_at)`. Per the PostgreSQL multicolumn index rule,
   equality constraints on leading columns plus the inequality on the first
   non-equality column bound the portion of the index scanned. A plain
   ascending index already serves `ORDER BY known_at DESC` via a backward scan,
   so an explicit `DESC` modifier is unnecessary here — it matters only for
   mixed-direction multi-column sorts.

## Worked Example

```python
from pit_schema import PITRecord, PointInTimeStore

store = PointInTimeStore()
store.insert(PITRecord("AAPL", "eps", 1.50, known_at="2023-02-15", valid_from="2022-12-31"))
store.insert(PITRecord("AAPL", "eps", 1.20, known_at="2023-08-10", valid_from="2022-12-31", revision=1))

store.query_as_of("AAPL", "eps", "2023-03-01").value   # 1.50 — restatement not yet known
store.query_as_of("AAPL", "eps", "2023-09-01").value   # 1.20 — restatement now known

audit = store.audit_leakage("AAPL", "eps", "2023-03-01")
audit.has_future_leakage   # True
audit.value                # 1.50 — correct PIT answer
audit.naive_value          # 1.20 — what a naive latest-row query would have used
```

## Production Implementation Reference

- Reference code: `scripts/pit_schema.py` (`PointInTimeStore`, `PITRecord`,
  `PITQueryResult`, `TemporalFormatError`).
- Automated unit tests: `scripts/test_pit_schema.py`.
- Schema contract, temporal model, and limitations: `references/standards.md`.

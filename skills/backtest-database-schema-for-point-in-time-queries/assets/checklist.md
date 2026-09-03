# Pre-Flight / Sign-off Checklist — backtest-database-schema-for-point-in-time-queries

## Schema

- [ ] `known_at` (knowledge time) column included in all fundamental / reference tables.
- [ ] `valid_from` (valid time) column included and distinct from `known_at`.
- [ ] `revision` counter present so same-day corrections resolve deterministically.
- [ ] `known_at` is sourced from vendor publication / filing release time — **not** `created_at` or any DB insert timestamp.
- [ ] Tables are append-only; no `UPDATE` path can overwrite a historical row.

## Timestamp hygiene

- [ ] One canonical timestamp format documented and applied across every vendor feed.
- [ ] All timestamps normalised to UTC before storage or comparison.
- [ ] Malformed / unpadded / mixed-format temporal values rejected on write.
- [ ] No code path compares timestamps by raw string comparison (RFC 3339 §5.1).
- [ ] Same-day rule decided and documented: a date-only `known_at` is treated as end of day, so it is not tradable that session.

## Query layer

- [ ] As-of filtering enforces **both** `known_at <= as_of` and `valid_from <= valid_as_of`.
- [ ] Restated metrics insert new rows with a later `known_at`, preserving the original.
- [ ] Composite index `(symbol, field, known_at)` created, equality columns leading.

## Verification

- [ ] Naive "latest row wins" query compared against the PIT query; every divergence explained.
- [ ] Leakage audit reports the naive value, not just a boolean flag.
- [ ] Restatement round-trip tested: as-of before the restatement returns the original figure; as-of after returns the restated one.
- [ ] UTC-offset regression tested: a record whose offset-bearing `known_at` resolves after the cutoff is excluded.
- [ ] Automated Testing: Run `python -m unittest discover -s skills/backtest-database-schema-for-point-in-time-queries/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

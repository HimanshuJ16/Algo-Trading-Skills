# Deep Workflow Reference — backtest-database-schema-for-point-in-time-queries

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Design PIT Schema**: Add `known_at` (publication date) and `valid_from` (effective period) columns to all fact tables.
2. **Query with As-Of Semantics**: Restrict data retrieval using `known_at <= as_of_date`.
3. **Audit Lookahead Leakage**: Compare naive queries against PIT queries to identify historical lookahead bugs.

## Production Implementation Reference

- Reference code: `scripts/pit_schema.py` (`PointInTimeStore`, `PITRecord`, `PITQueryResult`).
- Automated unit tests: `scripts/test_pit_schema.py`.

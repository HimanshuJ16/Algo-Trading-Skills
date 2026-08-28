# Workflows for Point-in-Time Index Constituent Tracking

## 1. Normalise the constituent event log

- Establish which axis each vendor column is on. `effective_date` is the date the change
  took effect; an `announcement_date` column, if present, belongs to the knowledge axis and
  is not used by this engine.
- Convert every date to zero-padded ISO-8601 `YYYY-MM-DD` (or `datetime.date`). Non-padded
  and locale-formatted dates are rejected at ingest — see `references/standards.md` for why
  accepting them silently corrupts ordering.
- Reconcile the deletion-date convention. If the vendor stores `del_date` as the last day of
  membership, add one session before ingesting.
- Attach a stable `security_id` (CUSIP, SEDOL, PERMNO, FIGI) to every event. Without it,
  membership is keyed by ticker and reused tickers merge.
- Attach `sequence` if the feed publishes an ordinal for changes sharing an effective date.

## 2. Ingest

```python
engine = PointInTimeIndexConstituentTrackingEngine()
engine.insert_events([
    IndexConstituentEvent("SP500", "TSLA", "ADDITION", "2020-12-21", security_id=TSLA_PERMANENT_ID),
])
```

`insert_events` validates the whole batch before storing any of it, so a bad record leaves
the engine untouched rather than half-loaded. It raises `IndexConstituentError` on an empty
index name or symbol, an unrecognised `event_type`, a non-ISO `effective_date`, or a
non-numeric `weight`. Events are snapshotted at ingest; mutating an event object afterwards
does not change an already-ingested timeline.

## 3. Resolve the point-in-time universe

```python
report = engine.query_pit_universe(
    PITIndexQuery("SP500", "2015-06-30"),
    current_static_universe=todays_members,   # optional; enables the ghost audit
)
```

A security is a member when its latest event at or before the as-of date is an `ADDITION`
— that is, `add_date <= T < del_date`.

## 4. Gate on the report before using it

| Check | Why |
|---|---|
| `report.status == 'UNIVERSE_RESOLVED_PIT'` | `INDEX_NOT_FOUND` means the index name matched no ingested events. An empty universe from a typo must not reach a backtest. |
| `report.data_quality_warnings` is empty | Non-empty means an addition and a deletion shared an effective date for one security, or `sequence` coverage was partial. Resolve the feed, do not suppress the warning. |
| `report.survivorship_bias_ghost_count is not None` | `None` means the ghost audit did not run. It is not a finding of zero. |

## 5. Survivorship-bias ghost audit

`ghost_symbols` lists the point-in-time members that are absent from today's membership —
the names a naive current-constituents backtest would have dropped. Two readings are useful:

- **Zero ghosts on a historical date** is suspicious for a broad index. Over any multi-year
  gap some names are always removed; zero usually means the event log was reverse-engineered
  from current membership and carries the bias it was supposed to remove.
- **Ghost count relative to universe size** gives the share of the historical universe the
  biased backtest would have silently omitted. Report it with the backtest, and measure the
  return impact by running the same strategy both ways rather than quoting a literature
  figure.

## 6. Hand off what this engine does not cover

- Announcement timing and rebalance look-ahead → `backtest-look-ahead-in-universe-selection`.
- Terminal settlement of delisted and acquired names → `survivorship-bias-free-universe-construction`.
- Ticker-to-identifier resolution across vendors → `reference-data-symbol-mapping-across-vendors`.

# Workflows for Universe Change Detection

## 0. Establish the comparison contract

Both snapshots must come from the same vendor, the same extract type, the same identifier
scheme and the same FIGI granularity level. Composite, exchange-level and share-class
FIGIs are different identifiers for the same company — Apple is `BBG000B9XRY4` (US
composite), `BBG000B9Y5X2` (Nasdaq venue level) and `BBG001S5N8V8` (share class) — so a
change of extract configuration silently delete-and-re-adds the entire universe. Record
which level you consume and treat a change to it as a migration, not a daily diff.

## 1. Snapshot ingestion and keying

1. Load $U_{t-1}$ and $U_t$ into `InstrumentRecord` rows.
2. Construct the engine with an explicit `id_scheme`:
   - `"FIGI"` — enforces the 12-character structure from the FIGI allocation rules.
   - `"ISIN"` — enforces ISO 6166 structure and the Double-Add-Double check digit.
   - `"OPAQUE"` — in-house permanent keys; no format enforcement, so a ticker-keyed file
     will be accepted and every rename will be misread as a delete plus an add.
3. Supply `previous_as_of` / `current_as_of` where the vendor stamps them. Reversed
   snapshots invert additions and deletions; the engine raises instead.
4. Duplicate identifiers raise. The usual cause is an ISIN-keyed universe spanning venues
   (one ISIN covers every fungible listing — ANNA Guidelines Sec. 1.1). Fix it upstream by
   keying on exchange-level FIGIs or a composite `(ISIN, MIC)`.

## 2. Churn guard — decide whether the snapshot is believable

Run **before** any alert is trusted:

- `deletion_ratio = |U_{t-1} \ U_t| / |U_{t-1}|`.
- Suspect if `deletion_ratio > max_deletion_ratio`, or if $U_t$ is empty while $U_{t-1}$
  was not (suspect regardless of threshold — an empty file is never a valid universe).
- When suspect: report status `UNIVERSE_SNAPSHOT_SUSPECT`; every `recommended_action`
  becomes `HOLD_FOR_MANUAL_REVIEW`; the intended action is preserved in
  `suppressed_action`; `requires_manual_review` is set on every alert; a warning is logged.

Operationally, a suspect report is a page, not a queue entry. Holding protects against the
irreversible error (liquidating the book on a broken file) but delays the reversible one
(a late exit on a genuine mass delisting).

Calibrate `max_deletion_ratio` from your own history, including index-rebalance days,
quarterly futures rolls and any day the vendor changes coverage. The 10% default assumes a
universe of hundreds of names.

## 3. Delta cross-matching

| Condition | Change type | Action |
|---|---|---|
| id in $U_t$ only, status `ACTIVE` | `ADDITION` | `INITIATE_COVERAGE` |
| id in $U_t$ only, status not `ACTIVE` | `ADDITION` | `REVIEW_STATUS_CHANGE` |
| id in $U_{t-1}$ only | `DELETION` | `LIQUIDATE_POSITION_AND_UNSUBSCRIBE` |
| id in both, ticker differs | `TICKER_RENAME` | `UPDATE_SYMBOL_MAPPER` |
| id in both, exchange differs | `EXCHANGE_MIGRATION` | `UPDATE_ROUTING_TABLE` |
| id in both, status $\to$ `DELISTED` | `STATUS_CHANGE` | `LIQUIDATE_POSITION_AND_UNSUBSCRIBE` |
| id in both, status $\to$ `HALTED` / `SUSPENDED` | `STATUS_CHANGE` | `FREEZE_TRADING_ALERTS` |
| id in both, non-tradable $\to$ `ACTIVE` | `STATUS_CHANGE` | `RESUME_TRADING_ELIGIBILITY` |
| id in both, any other status transition | `STATUS_CHANGE` | `REVIEW_STATUS_CHANGE` (logged) |
| id in both, only `asset_name` differs | — | none (a name change alters neither the FIGI nor tradability) |

Ticker, exchange and status comparisons are case- and whitespace-insensitive; the original
vendor strings are preserved in the alert.

## 4. Interpreting each class before acting

- **Addition** — a new listing, an index addition, a vendor coverage change, or a spin-off
  (a newly created entity receives a new FIGI). Confirm which before allocating capital.
- **Deletion** — absence, not evidence. Index removal, a vendor scope change and a
  truncated file are indistinguishable at this layer. A delisted instrument usually
  *stays* in the master with its FIGI intact, so a deletion is often a coverage change
  rather than a corporate event.
- **Delisting status transition** — close the position, but reconcile with the
  corporate-action feed first: if the delisting is a merger completion the holding may
  already have converted to cash or acquirer shares, and an order into the dead symbol
  will be rejected.
- **Ticker rename** — update the symbol mapper and every live subscription in one
  operation. The retired ticker may be reassigned to an unrelated instrument.
- **Venue migration** — update routing and market-data entitlements before the next order.

## 5. Alert dispatch

Alerts are emitted risk-reducing first: deletions, status changes, venue migrations,
renames, additions — each block sorted by permanent identifier for determinism. A consumer
that fails part-way through has therefore already applied the protective actions. One
instrument can produce several alerts in the same run (a rename *and* a halt); apply all
of them.

## 6. Audit reporting

Persist the full `UniverseChangeReport`: counts per class, `deletion_ratio`,
`snapshot_is_suspect`, and each alert's `audit_notes` and `suppressed_action`. Two
questions must be answerable after an incident — "what did the engine see?" and "what did
it decline to do automatically, and why?"

## 7. Bootstrap runs

The first run has an empty $U_{t-1}$: every instrument is an addition and the churn guard
cannot fire (`deletion_ratio` is 0.0 by definition). Detect `total_previous_count == 0`
and treat that report as a baseline rather than as a set of trading signals.

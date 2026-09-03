# Pre-Flight / Sign-off Checklist — instrument-universe-change-detection-and-alerting

## Snapshot contract
- [ ] Both snapshots come from the same vendor, extract type and identifier scheme.
- [ ] Both use the **same FIGI granularity level** (share class / country composite / exchange level) — mixing levels delete-and-re-adds the whole universe.
- [ ] Universe records are keyed on a permanent identifier (FIGI / ISIN), never on a ticker.
- [ ] `id_scheme` is set explicitly to `"FIGI"` or `"ISIN"` in production; `"OPAQUE"` is used only for in-house permanent keys and its lack of format enforcement is understood.
- [ ] Identifiers are unique within each snapshot; for a multi-venue ISIN universe the key is `(ISIN, MIC)` or an exchange-level FIGI.
- [ ] `previous_as_of` / `current_as_of` are supplied where available, so reversed snapshots fail loudly.

## Churn guard
- [ ] `max_deletion_ratio` is calibrated against observed churn for **this** universe (the 0.10 default assumes hundreds of names) and the rationale is recorded.
- [ ] Index-rebalance days, futures rolls and vendor coverage changes have been considered when picking the threshold.
- [ ] An empty current snapshot is known to be suspect regardless of the threshold.
- [ ] `UNIVERSE_SNAPSHOT_SUSPECT` pages a human — it does not sit in a queue.
- [ ] Downstream consumers read `recommended_action` (which is downgraded to `HOLD_FOR_MANUAL_REVIEW` when suspect), not `suppressed_action`.

## Change classification
- [ ] Additions in an `ACTIVE` state map to `INITIATE_COVERAGE`; additions in a non-tradable state do not.
- [ ] Deletions map to `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`, and it is understood that absence from a file is not proof of a delisting.
- [ ] Ticker renames map to `UPDATE_SYMBOL_MAPPER`, applied to the mapper **and** live subscriptions in one operation.
- [ ] Venue migrations map to `UPDATE_ROUTING_TABLE` and update market-data entitlements.
- [ ] A transition to `DELISTED` liquidates; a transition to `HALTED`/`SUSPENDED` freezes; a return to `ACTIVE` resumes; anything unrecognised goes to manual review.
- [ ] Delisting alerts are reconciled against the corporate-action feed before an exit order is sent (a merger completion may already have converted the holding).
- [ ] Multiple alerts for the same instrument in one run are all applied, not just the first.

## Operations
- [ ] Bootstrap runs (`total_previous_count == 0`) are treated as a baseline, not as signals.
- [ ] The full report — counts, `deletion_ratio`, `snapshot_is_suspect`, `audit_notes`, `suppressed_action` — is persisted for post-incident review.
- [ ] The vendor is known to maintain the `status` field; if it does not, it is documented that delistings are invisible to this engine.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/instrument-universe-change-detection-and-alerting/scripts` — 100% pass rate.
- [ ] A truncated-file drill has been run against the live consumer, confirming that no liquidation is emitted on a suspect snapshot.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

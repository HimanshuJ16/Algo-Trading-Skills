---
name: instrument-universe-change-detection-and-alerting
description: >-
  Reference data engine that diffs daily tradable-universe snapshots keyed on permanent identifiers (FIGI / ISIN) to detect additions, delistings, ticker renames, venue migrations and trading-status transitions, with a churn guard that stops a truncated vendor file being actioned as a mass liquidation.
domain: Data Management Global
subdomain: Reference Data Universe Tracking & Alerting
tags: ["universe-detection", "security-master", "openfigi", "isin", "ticker-renames", "delistings", "reference-data"]
brokers_frameworks: ["OpenFIGI API", "Security Master Database", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in reference data management, index constituent tracking, and trading bot universe maintenance. Trading algorithms relying solely on ephemeral ticker symbols fail during ticker renames (`FB` $\to$ `META`), corporate delistings, or index additions. Keying on an immutable permanent identifier — "A FIGI is never reused and remains with the instrument in perpetuity. A FIGI does not change as a result of any corporate action" (FIGI Allocation Rules, Sec. 1.2.1) — the engine cross-matches snapshots ($U_{t-1}$ vs $U_t$) into Additions, Deletions, Ticker Renames, Venue Migrations and Trading-Status transitions, and maps each to a downstream action.

The ticker case is not hypothetical: Meta's `FB` was retired on 9 June 2022, and `FB` today resolves to the ProShares S&P Dynamic Buffer ETF (FIGI `BBG01VRMNFB1`). A ticker-keyed universe would see `FB` "still present" and keep trading an unrelated instrument.

## When NOT to Use

- **As a delisting feed.** A diff sees only what two files disagree about. A delisted instrument keeps its FIGI and normally stays in the security master (FIGI Allocation Rules, Sec. 3.2.4), so a delisting usually appears as a `status` transition, not as a set difference — and only if your vendor maintains the status field. Corporate-action confirmation belongs to `corporate-action-event-calendar-integration`.
- **As an execution or liquidation component.** The engine emits recommendations. Position exit, order cancellation, and unsubscription are the caller's, under its own risk controls.
- **On snapshots keyed at different granularity levels.** FIGIs are allocated at share class, country composite and trading-venue level. Comparing a composite-keyed file against an exchange-level-keyed file makes every identifier differ — Apple is `BBG000B9XRY4` composite but `BBG000B9Y5X2` on Nasdaq — and produces a full-universe delete-and-re-add. Same for mixing FIGI with ISIN.
- **On an ISIN-keyed multi-venue universe without a composite key.** An ISIN "represents the identification of a financial instrument rather than the market a financial instrument trades on" (ANNA ISIN Uniform Guidelines 2025, Sec. 1.1): Meta's `US30303M1027` maps to 258 FIGI rows across venues. Key on `(ISIN, MIC)` or on exchange-level FIGIs; the engine rejects duplicate keys rather than silently keeping one listing.
- **For point-in-time backtest universes.** Reconstructing what was investable on a past date is `point-in-time-index-constituent-tracking` and `survivorship-bias-free-universe-construction`.

## Prerequisites

- Previous snapshot ($U_{t-1}$) and current snapshot ($U_t$), from the **same vendor, same extract, same identifier scheme and same granularity level**.
- One permanent identifier per row, unique within each snapshot.
- A `status` field maintained by the vendor if delistings/halts are to be detected at all.
- A calibrated `max_deletion_ratio` — the fraction of the universe allowed to disappear in one comparison before the snapshot is treated as broken rather than believed.

## Workflow

1. **Ingest and key by permanent identifier**:
   - Set `id_scheme="FIGI"` or `"ISIN"` so malformed identifiers are rejected at the door. `"OPAQUE"` (the default, for in-house keys) checks only non-blankness and uniqueness — under it, a snapshot accidentally keyed on tickers is accepted and every rename becomes a delete-plus-add.
   - **Decision point — duplicate identifier.** The engine raises rather than last-wins-collapsing. A duplicate is a broken extract or an ISIN spanning venues; both need fixing upstream, not silently reducing the row count.
   - Pass `previous_as_of` / `current_as_of` when you have them. Swapping the two snapshots inverts additions and deletions, turning every new listing into a liquidation instruction; supplying the dates makes that fail loudly.

2. **Sanity-check the snapshot before believing the delta** (churn guard):
   - **Decision point — before any action is emitted.** If deletions exceed `max_deletion_ratio` of the previous universe, or the current snapshot is empty while the previous was not, the file is treated as truncated: `status` becomes `UNIVERSE_SNAPSHOT_SUSPECT`, every `recommended_action` is downgraded to `HOLD_FOR_MANUAL_REVIEW`, and the intended action is preserved in `suppressed_action` for the audit trail. Holding is the fail-safe direction — a bad file's liquidations are irreversible, a delayed exit is not — but it is not free: a genuine mass-delisting event is also held, so a suspect report must page a human, not sit in a queue.
   - The default 10% is a **library default, not an industry standard**, and is calibrated for a universe of hundreds of names. On a 3-name universe one deletion is 33% and trips it every time.

3. **Classify the delta**:
   - **Addition** (id in $U_t$ only): `INITIATE_COVERAGE` when the new row is `ACTIVE`; a row added in a non-tradable status gets `REVIEW_STATUS_CHANGE` instead. A new FIGI is also how a spin-off arrives (Allocation Rules, Sec. 3.2.7) — confirm the reason before sizing.
   - **Deletion** (id in $U_{t-1}$ only): `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`. Absence from a file is not proof of a delisting — index removal, a vendor scope change and a truncated file look identical here.
   - **Ticker rename** (id in both, ticker differs): `UPDATE_SYMBOL_MAPPER`. Comparison is case- and whitespace-insensitive so vendor re-casing is not alerted as a rename.
   - **Venue migration** (id in both, exchange differs): `UPDATE_ROUTING_TABLE`. Exchange codes attached to composite-level FIGIs do change (Sec. 3.2.2 / 3.2.3) and orders routed to the old venue fail.
   - **Status transition** (id in both, status differs) — **decision point, the transition target decides the action, not the fact of a change**: $\to$ `DELISTED` is `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`; $\to$ `HALTED`/`SUSPENDED` is `FREEZE_TRADING_ALERTS`; non-tradable $\to$ `ACTIVE` is `RESUME_TRADING_ELIGIBILITY`; anything outside the recognised vocabulary is `REVIEW_STATUS_CHANGE` and logged, never guessed at.
   - A change in `asset_name` alone raises nothing: a company renaming itself changes neither the FIGI nor tradability (Sec. 3.2.5).

4. **Dispatch alerts in the emitted order**: deletions, status changes, venue migrations, renames, additions — risk-reducing first, so a consumer that dies part-way through has already applied the protective actions. One instrument can emit several alerts (rename *and* halt); apply all of them.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Keying the diff on ticker symbols**: a rename becomes a deletion of the old ticker plus an addition of the new one — liquidating a position that never changed and losing every historical join. Worse, retired tickers get reassigned: `FB` now belongs to an unrelated ETF, so the diff sees no change at all while the bot trades the wrong instrument.
- **Actioning a truncated vendor file**: a half-written or failed FTP transfer looks exactly like a mass delisting, and a naive diff answers with `LIQUIDATE` for the entire book. Never let a diff liquidate more than a calibrated fraction of the universe without a human.
- **Treating every status change as a freeze**: `ACTIVE` $\to$ `DELISTED` needs the position closed out, not a frozen alert; `HALTED` $\to$ `ACTIVE` must not freeze anything. A single blanket action for all transitions is wrong in both directions.
- **Assuming a delisting shows up as a deletion**: FIGIs persist through delisting and vendors keep the row (Sec. 3.2.4). If the vendor maintains `status`, the delisting is a status transition; if it does not, this engine cannot see the delisting at all.
- **Liquidating on a merger completion**: when the acquired company delists, its holding may already have converted to cash or acquirer shares (Sec. 3.2.6 — the FIGI does not change for either party). An exit order into a dead symbol will be rejected; reconcile against the corporate-action feed.
- **Mixing FIGI granularity levels or identifier schemes between snapshots**: composite vs exchange-level vs share class FIGIs are different identifiers for the same company, so mixing them delete-and-re-adds the whole universe. Apple: `BBG000B9XRY4` (US composite), `BBG000B9Y5X2` (Nasdaq), `BBG001S5N8V8` (share class).
- **Trusting an ISIN as a per-listing key**: one ISIN covers every fungible listing, and ISINs are not universally permanent — a merger by amalgamation retires both ISINs and allocates a new one (ANNA Guidelines, Sec. 4.2), while a pure name change keeps it (Sec. 4.9). FIGI has no such exception.
- **Updating the symbol mapper without notifying live execution daemons**: the mapper and the running subscriptions must be updated in the same operation, or orders route on a symbol the venue has retired.
- **Bootstrapping against an empty previous snapshot**: every instrument becomes an `INITIATE_COVERAGE` addition. Check `total_previous_count == 0` and treat that run as a baseline, not as a signal.

## Verification

- Instantiate `UniverseChangeDetectionEngine(id_scheme="FIGI")`. Compare $U_{t-1}$ (`BBG000MM2P62`: `FB`, `BBG000B9XRY4`: `AAPL`, `BBG000H6HNW3`: `TWTR`, plus 20 unchanged names) with $U_t$ (`BBG000MM2P62`: `META`, `BBG000B9XRY4`: `AAPL`, `BBG000N7QR55`: `PLTR`, same 20) $\implies$ exactly 3 alerts: 1 addition (`PLTR`, `INITIATE_COVERAGE`), 1 deletion (`TWTR`, `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`), 1 rename (`FB` $\to$ `META`, `UPDATE_SYMBOL_MAPPER`) — never a deletion of `FB` plus an addition of `META`.
- Churn guard: compare a 50-name universe against an empty snapshot $\implies$ `status == "UNIVERSE_SNAPSHOT_SUSPECT"`, all 50 alerts carry `HOLD_FOR_MANUAL_REVIEW` with `suppressed_action == "LIQUIDATE_POSITION_AND_UNSUBSCRIBE"`, and no alert recommends liquidation. At `max_deletion_ratio=0.10`, 1 deletion out of 10 must *not* be suspect and 2 out of 10 must be.
- Status transitions: `ACTIVE` $\to$ `DELISTED` $\implies$ `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`; `ACTIVE` $\to$ `HALTED` $\implies$ `FREEZE_TRADING_ALERTS`; `HALTED` $\to$ `ACTIVE` $\implies$ `RESUME_TRADING_ELIGIBILITY`; an unrecognised status $\implies$ `REVIEW_STATUS_CHANGE`.
- Negative checks: a blank `permanent_id`, a duplicate identifier within one snapshot, a ticker passed as a FIGI under `id_scheme="FIGI"`, an ISIN with a broken check digit under `id_scheme="ISIN"`, and reversed `as_of` dates must each raise.
- Run `python scripts/test_universe_change_detection.py` and confirm a 100% pass rate.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `isin-cusip-sedol-cross-reference-service`
- `corporate-action-event-calendar-integration`
- `point-in-time-index-constituent-tracking`
- `survivorship-bias-free-universe-construction`
- `cold-start-handling-for-newly-listed-instruments`

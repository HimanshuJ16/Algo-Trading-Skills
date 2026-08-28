# Pre-Flight / Sign-off Checklist — regulatory-change-monitoring-service-integration

Use before wiring a regulatory change feed into production, and at each
supervisory review of the monitoring control.

## Coverage

- [ ] **Exposure map current:** `monitored_regulators` covers every jurisdiction the firm trades in, through every legal entity and venue. Re-checked after the most recent venue or entity change.
- [ ] **No empty filters:** `monitored_regulators` is `None` or an explicit list — never `[]` (the engine raises, and that is the intended outcome).
- [ ] **Subject-area filter justified:** if `monitored_subdomains` is set, someone owns the decision about what it excludes, and the excluded categories are written down.
- [ ] **Unclassified items reviewed:** WARNING-logged updates with no `impacted_subdomains` are triaged, not left to accumulate.

## Input quality

- [ ] **`compliance_date` populated wherever the source sets one.** For SEC rulemakings this is *not* in the Federal Register structured feed — confirm the extraction path (release body or analyst entry) exists and is exercised.
- [ ] **Phased rules split:** each compliance phase is its own `RegulatoryUpdate` with its own `update_id`.
- [ ] **Stable identifiers:** `update_id` comes from the publisher (document number, notice number, circular reference, CELEX), not a generated per-fetch key.
- [ ] **Revisions replace:** the adapter overwrites the prior record when a deadline moves; it does not append a second obligation.
- [ ] **Severity taxonomy mapped:** every vendor/analyst label maps to exactly one of `CRITICAL/HIGH/MEDIUM/LOW`. Unmapped labels raise — confirm that surfaces as an ingestion alert, not a swallowed exception.

## Assessment configuration

- [ ] **Assessment date passed explicitly** for any report that will be retained as evidence (the UTC-today default is logged, but is not reproducible).
- [ ] **`urgent_action_window_days` is a documented firm policy**, not presented as a regulatory threshold.
- [ ] **Calendar-day semantics accepted:** no business-day or holiday adjustment is applied; deadlines that turn on trading sessions are handled elsewhere.

## Routing

- [ ] **Immediate-action owner named** (compliance + engineering) with an escalation path that does not depend on one person reading a dashboard.
- [ ] **Overdue owner is different and more senior** — an `OVERDUE` item is a live breach, not a planning item.
- [ ] **Compliance date treated as a completion date:** the change-management, testing, and approval time sits *before* it, not after.
- [ ] **Filter counts monitored:** a rising `filtered_regulator_count` is reviewed as a possible exposure change, not ignored as noise.

## Evidence

- [ ] **Full `RegulatoryChangeReport` persisted** each cycle — assessment date, all counts, filtered authority names — not just the alerts.
- [ ] **Raw feed payloads retained** alongside the assessments, for the firm's applicable retention period.
- [ ] **Closure is auditable:** `remediation_complete` is set by a named approver against evidence of the work, and `COMPLIANT` items remain visible in the report.
- [ ] **Claims checked:** no policy or client-facing document states that a regulator requires this monitoring cadence or alert window. The mandatory anchors are FINRA Rule 3110(a)/(b)(1) and RTS 6 Art. 9 — see `references/standards.md`.

## Automated Testing

- [ ] Run `python -m unittest test_regulatory_change_monitoring_service_integration` from the `scripts/` directory — 100% pass rate (42 tests).

## Sign-off

| Role | Name | Date |
|---|---|---|
| Compliance owner | | |
| Engineering owner | | |

# Pre-Flight / Sign-off Checklist — finra-algo-trading-registration-requirements

Jurisdiction: **United States, FINRA member broker-dealers only.** If the firm is
not a FINRA member, none of this applies — record that determination and stop.

## Firm and scope determination
- [ ] Firm's FINRA membership status confirmed and reflected in `is_finra_member`.
- [ ] Every instrument code the pipeline can emit is mapped to a `security_type` token — an unmapped code raises `ValueError` by design.
- [ ] Covered set is exactly equity, equity options, preferred and convertible debt; no asset class has been added "to be safe".
- [ ] Each automated system is classified as generating/routing orders, solely routing entire orders, or idea-generation only — and re-classified whenever that changes.
- [ ] Multi-asset strategies are audited on their covered leg, not averaged.

## Activity and responsibility
- [ ] "Significant modification" is defined in writing, anchored to FINRA's formulation (a change to the code that impacts the logic and functioning of the strategy) and mapped to this repository's change types.
- [ ] Initial **design** and **development** of a new algorithm are gated, not only modifications.
- [ ] Directing a third-party vendor to build or significantly modify an algorithm is gated.
- [ ] The person monitoring or reviewing an off-the-shelf algorithm's performance is registered.
- [ ] The person *primarily* responsible is identified per algorithm and documented; junior contributors are marked `author_primarily_responsible=False` rather than blocked.
- [ ] Infrastructure integration and linkage testing are classified as non-registrable, not swept in.

## Registration data quality
- [ ] Personnel snapshots are sourced from CRD / FINRA Gateway and refreshed on a documented cadence.
- [ ] `is_series_57_active` means the registration is currently effective, not that the exam was once passed.
- [ ] CE status is populated — a CE-inactive person is prohibited from functioning in a registered capacity (Rule 1240(a)).
- [ ] Securities Traders registered before 1 Oct 2018 are flagged `is_sie_grandfathered`; the gate is not blocking them for a missing SIE record.
- [ ] Series 24 holders are flagged so Securities Trader Principals are recorded as such (Rule 1220(a)(7)).
- [ ] Lapsed registrations (two or more years) are recorded as inactive, not active.

## Supervision
- [ ] The Rule 3110(a)(5) supervisory assignment for each registered developer exists and is documented — including where the developer's business-line manager is not the assigned supervisor.
- [ ] The supervisor recorded in the commit is registered as a Securities Trader or a Securities Trader Principal, or `require_supervisor_registration=False` is set with the assignment tracked elsewhere.
- [ ] Self-approval policy is a conscious firm decision; it is a supervisory control, not a FINRA mandate.

## Gate wiring
- [ ] CI blocks on `report.blocks_deployment`, never on inequality with `COMPLIANCE_APPROVED`.
- [ ] `OUT_OF_SCOPE_RULE_1220B4` is surfaced to reviewers as "this rule does not reach the change", not as a compliance clearance.
- [ ] `requires_change_management_review` feeds the Notice 15-09 change-management process for every algorithmic strategy change, significant or not.
- [ ] Gate bypasses are themselves logged, attributed and reviewed.

## Evidence and retention
- [ ] Every decision — approved, blocked **and** out of scope — is persisted, not just violations.
- [ ] Reports go to an append-only sink meeting Rule 4511(c) / SEA Rule 17a-4; the in-memory `audit_trail` is not the system of record.
- [ ] Retention meets the Rule 4511(b) six-year default absent a more specific period.
- [ ] Commit-to-algorithm-to-person mapping is reconstructable for a FINRA examination, including the registration status relied on at the time of the decision.

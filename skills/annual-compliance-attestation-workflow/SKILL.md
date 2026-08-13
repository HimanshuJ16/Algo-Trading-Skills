---
name: annual-compliance-attestation-workflow
description: Automated compliance verification engine for SEC Rule 206(4)-7, FINRA
  Rule 3130, FINRA Rule 3120, and SEC Rule 15c3-5 annual attestations in quantitative
  hedge funds and broker-dealers.
domain: regulatory-compliance
subdomain: institutional-reporting
tags:
- compliance
- sec-20647
- finra-3130
- finra-3120
- sec-15c3-5
- regulatory
- attestation
brokers_frameworks:
- generic
version: "2.0.0"
author: System
license: MIT
---

## When to Use

Use this skill to automate the annual compliance readiness gate for a quantitative hedge fund or broker-dealer. It programmatically verifies that all mandatory regulatory obligations have been met before the Chief Executive Officer (CEO) and Chief Compliance Officer (CCO) can sign the final annual attestation. It models SEC Rule 206(4)-7 (RIA annual policy/procedure review), FINRA Rule 3130 (BD CEO annual compliance certification), FINRA Rule 3120 (supervisory-controls annual report, a 3130 prerequisite), and SEC Rule 15c3-5 (annual review and CEO certification of pre-trade risk controls for BDs with market access).

One `AnnualComplianceChecklist` instance corresponds to ONE legal entity. A typical quant fund structure (RIA + BD affiliate + offshore feeder) requires one checklist per entity, each with its own certification anniversary and obligations.

## When NOT to Use

This skill is US-only and an annual governance gate. Do NOT invoke it when:

1. **Non-US obligations apply.** It does not model EU MiFID II RTS 6 Art. 9, UK FCA SYSC 27.8 / SM&CR, or CFTC Part 3 CCO reports. Use `mifid-ii-algo-trading-compliance-eu` or `uk-fca-algorithmic-trading-systems-controls` for those regimes.
2. **Multi-entity consolidation is required.** One checklist = one legal entity. Do not collapse a RIA and its BD affiliate into a single instance; a BD affiliate's 3130 deadline can pass silently while a consolidated checklist shows "in progress."
3. **Intra-year remediation tracking or live-trade risk control is needed.** This is a periodic annual gate, not a live-trade risk control or remediation-tracker. Use `kill-switch-and-drawdown-circuit-breakers` or `risk-control-configuration-change-approval-workflow` for live controls.
4. **A deadline has already been missed.** This skill is not a substitute for FINRA Rule 4530 self-reporting once a certification deadline is missed; see the missed-attestation recovery path in `references/workflows.md`.

## Prerequisites

- Python 3.9+
- **Tamper-evident, attributable, reproducible audit-log evidence.** Every date supplied to the checklist must be sourced from a primary recordkeeping system that produces time-stamped records per SEC Rule 17a-4(f): attributable to a named actor, reproducible from a source-system record ID, and protected against post-hoc modification. The agent MUST REFUSE unprovenanced dates (a date typed by hand with no source-system record ID) rather than populate the field — the engine cannot otherwise distinguish fabricated timestamps from genuine audit entries.
- Audit logs of the CEO/CCO annual meeting (FINRA 3130), with the prior certification anniversary date.
- Audit logs of quantitative code integrity reviews and trade surveillance testing.
- For BDs: the Rule 3120 supervisory-controls report date, the Rule 15c3-5 annual review and CEO certification dates, and the board/audit-committee submission date.

## Workflow

1. **Identify the legal entity.** Confirm `legal_entity_id`. One checklist per legal entity.
2. **Collect Attestation Evidence.** Gather tamper-evident, source-system-derived dates for all mandatory reviews: SEC 206(4)-7 annual policy review and its documentation, algorithmic code risk assessments, trade surveillance tests, and (for BDs) the CEO-CCO meeting, prior certification anniversary, certification signing date, Rule 3120 report, Rule 15c3-5 review/certification, and board/audit-committee submission.
3. **Evaluate SEC Rule 206(4)-7.** The engine verifies the annual review of written policies/procedures was completed and documented this year.
4. **Evaluate Quant-Specific Controls.** The engine flags missing reviews of algorithmic trading code integrity and trade surveillance (high-priority SEC exam targets for quant funds).
5. **Evaluate FINRA Rule 3130 (BD only).** The engine checks the CEO-CCO meeting occurred within the rolling 12-month window preceding `certification_signing_date` (FINRA 3130(c)(2)), no later than the anniversary of the prior certification, and that the meeting precedes the CEO certification signing (rubber-stamping guard). It also checks the FINRA 3130.04 board/audit-committee 45-day submission deadline.
6. **Evaluate FINRA Rule 3120 (BD only).** The engine verifies the supervisory-controls annual report was completed within the rolling window — a substantive prerequisite to a defensible 3130 cert.
7. **Evaluate SEC Rule 15c3-5 (BD only).** The engine verifies the annual review of pre-trade risk controls and the separate CEO certification of those controls.
8. **Issue Sealed Report.** Returns a tamper-evident `AttestationReport` with `content_hash` logged at INFO.
9. **Signature & Archiving.** On a True verdict, CEO/CCO sign and archive in 17a-4-compliant storage (WORM OR the 2023 audit-trail alternative per Release 34-96034). On a False verdict, escalate `missing_requirements` to department heads — do not sign, do not auto-remediate, do not fabricate dates.
10. **Board/audit-committee distribution (BD).** Submit the signed certification to the board/audit-committee no later than 45 days after signing (FINRA 3130.04).

### Output Contract

`AnnualComplianceAttestationEngine.evaluate()` returns `AttestationReport(is_ready_for_attestation: bool, missing_requirements: List[str], missing_requirement_codes: List[str], generated_at: datetime, content_hash: str)`.

- **`is_ready_for_attestation == True`** is a HARD-GO: CEO/CCO may sign and archive in 17a-4-compliant WORM/audit-trail storage. `missing_requirements` and `missing_requirement_codes` are empty.
- **`is_ready_for_attestation == False`** is a HARD BLOCK: must NOT sign, must escalate `missing_requirements` (and their codes) to department heads, must NOT auto-remediate or fabricate dates. `missing_requirement_codes` enables downstream routing (e.g., `REQ_FINRA_3130_CEO_CCO_MEETING` vs `REQ_SEC_15C3_5_CEO_CERT`).
- **Success** = empty `missing_requirements` + CEO/CCO signature + archived sealed report with `content_hash` logged.

## Common Pitfalls

- **Rubber-Stamping Rule 3130**: The CEO signing the FINRA 3130 certification without the legally required CEO-CCO meeting in the preceding 12 months, or signing before the meeting occurred. The engine explicitly rejects a meeting that post-dates the signature.
- **Calendar-year proxy for 3130**: FINRA 3130(c)(2) is a rolling 12-month window, not a calendar-year check. A December meeting can satisfy a March anniversary; a January meeting can be stale relative to a prior-March certification. The engine uses the rolling window plus the prior-certification anniversary constraint.
- **Misattributing 206(4)-7**: SEC Rule 206(4)-7 mandates an annual REVIEW of written policies/procedures and CCO designation — not a standalone "CCO annual report." A CCO-report concept, where wanted, belongs to FINRA 3130 / CFTC Part 3.
- **Generic Manuals**: Using off-the-shelf compliance manuals that fail to explicitly require testing of algorithmic code integrity, exposing the firm during an SEC sweep.
- **Consolidating entities**: Collapsing a RIA and its BD affiliate into one checklist can mask a missed BD-only deadline (3130, 3120, 15c3-5).
- **Unprovenanced dates**: Populating a date field from a hand-typed value with no source-system record ID makes the sealed verdict meaningless — the engine cannot detect fabrication.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/annual-compliance-attestation-workflow/scripts -v
```

What they assert:

- Valid RIA and valid BD checklists return `is_ready_for_attestation=True` with empty `missing_requirements` and `missing_requirement_codes`.
- Missing SEC 206(4)-7 policy review / documentation, quant code-integrity review, or trade-surveillance test each block the verdict with the matching `REQ_*` code.
- BD missing CEO-CCO meeting, missing CEO certification signing date, stale rolling-window meeting (>12 months before signing), meeting stale relative to the prior-certification anniversary, and rubber-stamp ordering (meeting post-dates signature) each block with the corresponding `REQ_FINRA_3130_*` code.
- A December meeting satisfying a March anniversary passes; a January meeting stale relative to a prior-March anniversary blocks (rolling-window + anniversary rule).
- Late (>45 days) and missing board/audit-committee submission both block with `REQ_FINRA_3130_04_BOARD_SUBMISSION`.
- Missing Rule 3120 report, missing 15c3-5 annual review, and missing 15c3-5 CEO certification each block with their respective codes.
- RIA checklist with all BD-only fields `None` is NOT gated on BD rules (still ready).
- `reporting_year` rejects `None`, non-int (string), out-of-range (`<2000` / `>2100`), and `bool` values with `ValueError`.
- `AnnualComplianceChecklist` and `AttestationReport` are frozen dataclasses (mutation raises `FrozenInstanceError`).

Confirm the implementation against `assets/checklist.md` before production run.

## Related Skills

- `algorithmic-trading-firm-licensing-thresholds`
- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `uk-fca-algorithmic-trading-systems-controls`

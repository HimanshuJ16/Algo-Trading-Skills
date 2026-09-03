---
name: annual-compliance-attestation-workflow
description: Annual compliance attestation gate for US advisers and broker-dealers
  — SEC Rule 206(4)-7(b), the Rule 204-2(a)(17)(ii) review record, FINRA Rule 3130
  and 3120, and SEC Rule 15c3-5(e), with every block traced to the provision that
  requires it.
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
version: "3.0.0"
author: System
license: MIT
---

## When to Use

Use this skill to automate the annual compliance readiness gate for a quantitative hedge fund or broker-dealer. It programmatically verifies that the mandatory annual obligations have been met before the Chief Executive Officer (CEO) and Chief Compliance Officer (CCO) sign the final attestation, and maps every block onto the provision that requires it:

- **SEC Rule 206(4)-7(b)** — the adviser's annual review of the adequacy of its written policies and procedures and the effectiveness of their implementation.
- **SEC Rule 204-2(a)(17)(ii)** — the books-and-records obligation covering "any records documenting the investment adviser's annual review of those policies and procedures conducted pursuant to § 275.206(4)-7(b)".
- **FINRA Rule 3130** — the BD CEO's annual certification: the CEO-CCO meeting "in the preceding 12 months" (3130(c)(2)), the certification effected no later than the anniversary of the prior year's certification (footnote 1 to 3130(b)), and submission of the final report to the board and audit committee (3130(c)(3)).
- **FINRA Rule 3120(a)** — the supervisory-controls report to senior management, "no less than annually".
- **SEC Rule 15c3-5(e)(1)/(e)(2)** — the annual review of market-access risk controls and the separate CEO certification of them, for a BD **with market access**.

One `AnnualComplianceChecklist` instance corresponds to ONE legal entity. A typical quant fund structure (RIA + BD affiliate + offshore feeder) requires one checklist per entity, each with its own certification anniversary and obligations.

## When NOT to Use

This skill is US-only and an annual governance gate. Do NOT invoke it when:

1. **Non-US obligations apply.** It does not model EU MiFID II RTS 6 Art. 9, UK FCA SYSC 27.8 / SM&CR, or CFTC Part 3 CCO reports. Use `mifid-ii-algo-trading-compliance-eu` or `uk-fca-algorithmic-trading-systems-controls` for those regimes.
2. **Multi-entity consolidation is required.** One checklist = one legal entity. Do not collapse a RIA and its BD affiliate into a single instance; a BD affiliate's 3130 deadline can pass silently while a consolidated checklist shows "in progress."
3. **Intra-year remediation tracking or live-trade risk control is needed.** This is a periodic annual gate, not a live-trade risk control or remediation tracker. Use `kill-switch-and-drawdown-circuit-breakers` or `risk-control-configuration-change-approval-workflow` for live controls.
4. **A deadline has already been missed.** This skill is not a substitute for considering FINRA Rule 4530(b) self-reporting once a certification deadline is missed; see the missed-attestation recovery path in `references/workflows.md`.
5. **As the whole 15c3-5 programme.** The engine records that the `(e)(1)` review and the `(e)(2)` CEO certification happened. It says nothing about whether the controls themselves are adequate — that is `sec-rule-15c3-5-risk-controls-us`.

## Prerequisites

- Python 3.9+ (stdlib only).
- **Tamper-evident, attributable, reproducible audit-log evidence.** Every date supplied to the checklist must be sourced from a primary recordkeeping system that produces time-stamped records per SEC Rule 17a-4(f): attributable to a named actor, reproducible from a source-system record ID, and protected against post-hoc modification. The agent MUST REFUSE unprovenanced dates (a date typed by hand with no source-system record ID) rather than populate the field — the engine cannot otherwise distinguish fabricated timestamps from genuine audit entries.
- **Every date must be a `datetime.datetime`**, and all supplied datetimes must agree on timezone awareness (all naive or all tz-aware). A `datetime.date`, a string, or a naive/aware mix is rejected at construction with `ValueError`.
- Audit logs of the CEO/CCO annual meeting (FINRA 3130(c)(2)) and the prior certification date, which fixes this year's anniversary deadline.
- Audit logs of quantitative code integrity reviews and trade surveillance testing.
- For BDs: the Rule 3120 report date, the certification execution date, the board/audit-committee submission date, and — if the firm has market access — the Rule 15c3-5(e)(1) review and (e)(2) CEO certification dates.

## Workflow

1. **Identify the legal entity.** Confirm `legal_entity_id` and, for a BD, whether it has market access (`has_market_access`). Rule 15c3-5(b) binds a broker-dealer *with market access*; a BD without it must not be blocked on a 15c3-5 obligation it does not have.
2. **Collect attestation evidence.** Gather tamper-evident, source-system-derived dates for every mandatory review. Set `certification_signing_date` to the date of **execution** of the certification — it anchors every rolling window below.
3. **Evaluate SEC Rule 206(4)-7(b) and the 204-2 record.** The engine checks the annual review was completed this year and that a record documenting it exists. These emit *different* codes: 206(4)-7(b) mandates the review, while the record is a 204-2(a)(17)(ii) books-and-records obligation. (The 2023 amendment that would have required the review itself to be documented in writing under 206(4)-7(b) was vacated — see `references/standards.md`.)
4. **Evaluate quant-specific controls.** The engine flags missing reviews of algorithmic trading code integrity and trade surveillance (high-priority SEC/FINRA exam topics for quant firms; exam expectations, not a named rule).
5. **Evaluate FINRA Rule 3130 (BD only).** Four separate gates: the CEO-CCO meeting inside the 12 calendar months preceding execution (3130(c)(2)); the certification effected no later than the anniversary of the prior certification (3130(b) fn.1); the certification signed in the reporting year; and the meeting preceding the signature (rubber-stamping guard). Board/audit-committee submission within 45 days of execution is checked separately under 3130(c)(3).
6. **Evaluate FINRA Rule 3120 (BD only).** The engine verifies a supervisory-controls report reached senior management within the 12 months preceding execution. Rule 3120(a) itself only says "no less than annually" — tying it to the 3130 cycle is this engine's tightening, so the certification rests on testing no older than a year.
7. **Evaluate SEC Rule 15c3-5 (BD with market access only).** The `(e)(1)` annual review and the `(e)(2)` CEO certification are separate acts and separate gates.
8. **Issue the sealed report.** `evaluate()` returns an `AttestationReport` whose `content_hash` binds the full evidence set, the verdict and `generated_at`.
9. **Signature and archiving.** On a True verdict, CEO/CCO sign and archive in 17a-4-compliant storage (WORM **or** the 2023 audit-trail alternative per Release 34-96034). On a False verdict, escalate `missing_requirements` to department heads — do not sign, do not auto-remediate, do not fabricate dates.
10. **Board/audit-committee distribution (BD).** Submit the final report evidencing the processes to the board and audit committee at the **earlier** of their next scheduled meetings or 45 days after execution (FINRA 3130(c)(3)). The engine checks only the 45-day limb; if the next scheduled meeting is sooner, that is the operative deadline and the engine will not catch a miss.
11. **Re-verify before relying on an archived report.** `AnnualComplianceAttestationEngine.verify_report(checklist, report)` recomputes the seal and returns False if any evidence date, the verdict, either findings list, or `generated_at` has been altered since issue.

### Output Contract

`AnnualComplianceAttestationEngine.evaluate(checklist, as_of=None)` returns `AttestationReport(is_ready_for_attestation: bool, missing_requirements: List[str], missing_requirement_codes: List[str], generated_at: datetime, content_hash: str)`.

- **`is_ready_for_attestation == True`** is a HARD-GO: CEO/CCO may sign and archive. `missing_requirements` and `missing_requirement_codes` are empty.
- **`is_ready_for_attestation == False`** is a HARD BLOCK: must NOT sign, must escalate `missing_requirements` (and their codes) to department heads, must NOT auto-remediate or fabricate dates. `missing_requirement_codes` enables downstream routing — `REQ_FINRA_3130_CEO_CCO_MEETING` (no timely meeting) is a different escalation from `REQ_FINRA_3130_CERT_ANNIVERSARY` (the certification itself is late) and from `REQ_SEC_15C3_5_CEO_CERT`.
- **Determinism.** The verdict never reads the wall clock. Every rolling window is anchored on `certification_signing_date`, else `ceo_certification_signed_date`, else the explicit `as_of` argument. With no anchor available for a BD the engine blocks rather than guessing, so an archived verdict is reproducible years later.
- **Success** = empty `missing_requirements` + CEO/CCO signature + archived sealed report whose `verify_report()` still returns True.

## Common Pitfalls

- **Applying the anniversary rule to the meeting instead of the certification**: footnote 1 to FINRA Rule 3130(b) requires that "each ensuing annual certification is effected no later than on the anniversary date of the previous year's certification." That constrains the **certification**, not the CEO-CCO meeting. A gate that tests the meeting against the anniversary blocks a compliant meeting while letting a certification executed months past its anniversary — an actual violation — pass unnoticed. The engine separates the two (`REQ_FINRA_3130_CEO_CCO_MEETING` vs `REQ_FINRA_3130_CERT_ANNIVERSARY`).
- **Citing the 45-day board deadline to "FINRA Rule 3130.04"**: Supplementary Material .04 is "Content of Meetings Between Chief Executive Officer and Chief Compliance Officer". The board/audit-committee deadline lives in the certification text at **3130(c)(3)**, and it is the *earlier* of the next scheduled meetings or 45 days — so a 45-day-only check is an upper bound, never proof of compliance.
- **Rubber-stamping Rule 3130**: signing the certification without the 3130(c)(2) meeting in the preceding 12 months, or signing before the meeting occurred. The engine rejects a meeting that post-dates the signature.
- **Calendar-year proxy for the 3130 window**: 3130(c)(2) is a rolling 12-month window measured back from the execution date, not a calendar-year check. A December meeting can support a March execution.
- **`timedelta(days=365)` for a 12-month window or an anniversary**: 2023-03-01 plus 365 days is 2024-02-29, not the 2024-03-01 anniversary, because 2024 carries a leap day. A one-day error on a regulatory deadline is the difference between a pass and a violation. Use calendar arithmetic.
- **Treating written documentation as a 206(4)-7 mandate**: the 2023 amendment requiring the annual review to be documented in writing was vacated with the rest of the Private Fund Adviser Rules (5th Cir., 5 Jun 2024). Current 206(4)-7(b) contains no writing requirement. The documentation gate here rests on Rule 204-2(a)(17)(ii) and examiner expectations — a defensible firm control, but do not tell a client the SEC rule text requires it.
- **Citing 15c3-5(d)(2) for the annual review**: (d)(2) is the allocation-non-relief clause. The annual review is (e)(1) and the CEO certification is (e)(2).
- **Applying 15c3-5 to every broker-dealer**: the rule binds a BD *with market access*. Set `has_market_access=False` for one without it rather than blocking on an obligation it does not have.
- **Anchoring a compliance window on the wall clock**: a verdict that depends on when it was computed cannot be reproduced at examination time. Supply the execution date, or an explicit `as_of`.
- **Consolidating entities**: collapsing a RIA and its BD affiliate into one checklist can mask a missed BD-only deadline (3130, 3120, 15c3-5).
- **Unprovenanced dates**: populating a date field from a hand-typed value with no source-system record ID makes the sealed verdict meaningless — the engine cannot detect fabrication, only later alteration.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/annual-compliance-attestation-workflow/scripts -v
```

What they assert:

- Valid RIA and valid BD checklists return `is_ready_for_attestation=True` with empty `missing_requirements` and `missing_requirement_codes`; repeated evaluation of the same checklist is deterministic.
- Missing 206(4)-7(b) review, missing 204-2(a)(17)(ii) review record, missing quant code-integrity review, and missing trade-surveillance test each block with their own distinct `REQ_*` code — the review and the record no longer collapse into one duplicated code.
- BD missing CEO-CCO meeting, missing certification signature, a meeting outside the 12-month window, and rubber-stamp ordering each block with the corresponding `REQ_FINRA_3130_*` code; a meeting exactly 12 calendar months before execution passes.
- A certification executed after the prior anniversary blocks with `REQ_FINRA_3130_CERT_ANNIVERSARY` while leaving a timely meeting unflagged; one executed exactly on the anniversary passes.
- Calendar-year arithmetic: a certification on a true 2024-03-01 anniversary of a 2023-03-01 prior certification passes (365-day arithmetic would wrongly block it), and 29 February maps to 28 February in a non-leap year.
- Late (>45 days) and missing board submissions block with `REQ_FINRA_3130_C3_BOARD_SUBMISSION`; exactly 45 days passes, and a submission made *before* execution passes (3130(c)(3) expressly permits it).
- Missing Rule 3120 report, missing 15c3-5(e)(1) review, and missing 15c3-5(e)(2) CEO certification each block with their respective codes; a BD with `has_market_access=False` is not gated on 15c3-5 at all.
- A BD checklist with no execution anchor and no `as_of` blocks the meeting and 3120 windows rather than falling back to the wall clock; supplying `as_of` makes them evaluable.
- RIA checklist with all BD-only fields `None` is NOT gated on BD rules (still ready).
- Construction rejects `reporting_year` that is `None`, a string, out of `[2000, 2100]`, or a `bool`; an empty or whitespace-only `legal_entity_id`; non-`bool` `is_broker_dealer` / `has_market_access`; a `datetime.date` or string in a date field; a naive/aware datetime mix; and a `prior_certification_date` that does not precede this cycle's certification.
- `generated_at` is timezone-aware UTC.
- The seal binds the evidence: two checklists differing only in one meeting date hash differently, and `verify_report()` returns False after a findings list is mutated or the evidence is swapped.
- `AnnualComplianceChecklist` and `AttestationReport` are frozen dataclasses (mutation raises `FrozenInstanceError`).

Confirm the implementation against `assets/checklist.md` before production run.

## Related Skills

- `algorithmic-trading-firm-licensing-thresholds`
- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `uk-fca-algorithmic-trading-systems-controls`

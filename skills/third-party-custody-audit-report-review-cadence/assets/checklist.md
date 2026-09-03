# Institutional Third-Party Custody Audit Review Checklist

Vendor: ______________________  Review as-of date: ____________  Reviewer: ____________

## 1. Evidence collection
- [ ] **SOC 1 Type II** obtained (AT-C 320 / ISAE 3402) covering internal control over
      financial reporting for the custody service.
- [ ] **SOC 2 Type II** obtained covering Security, Availability and key-custody
      controls.
- [ ] **Scope confirmed**: the system description covers the *entity that holds the
      assets* and the custody service actually used — not a group affiliate or a
      different product line.
- [ ] **Proof of Reserves** attestation date recorded, if the vendor publishes one.
      Recorded as a freshness signal only — a PoR engagement is not an audit
      (PCAOB Investor Advisory, 2023-03-08).
- [ ] **Audited financials / ISO 27001 certificate** collected where applicable; ISO
      certificate expiry and latest surveillance audit noted (3-year cycle).

## 2. Opinion and control assessment
- [ ] **Opinion recorded**: unqualified / qualified / adverse / disclaimer. Anything
      other than unqualified goes to the Risk Committee.
- [ ] **Section IV exceptions counted** into `deficiencies_found`, with management's
      responses read. A clean opinion does not mean zero exceptions.
- [ ] **Observation period checked** against the firm's `min_type2_coverage_days`
      floor. The AICPA sets no minimum; a 30–60 day "Type II" carries little more
      weight than a Type I.
- [ ] **Staleness measured from the coverage END date**, not the report issue date.

## 3. Subservice organisations
- [ ] **Carve-out or inclusive method identified** in the system description.
- [ ] For each carved-out subservice organisation (cloud host, HSM vendor, MPC
      co-signer, sub-custodian): its **own SOC report obtained** for an overlapping
      period, and checked against the CSOCs the custodian's report names.

## 4. Bridge / gap letter
- [ ] Required where more than `max_unbridged_gap_days` (default 90) have passed
      since coverage ended and the next report is not yet issued.
- [ ] Letter **names the SOC report it bridges**.
- [ ] **Signed** by service organisation management, dated on or after its period end.
- [ ] **Contiguous**: starts no later than the day after coverage ended.
- [ ] **Not forward-looking**: period end is not in the future.
- [ ] **Leaves no residual gap**: the letter reaches within `max_unbridged_gap_days`
      (default 90 — industry practice, not an AICPA rule) of today.
- [ ] Recorded that the letter carries **no audit assurance**; the vendor is capped
      at MEDIUM risk for as long as coverage rests on it.
- [ ] **Not used to paper over an expired report**: if coverage ended more than
      `review_cadence_days` ago, escalate for a fresh report — a long-span bridge
      letter is not coverage.

## 5. Complementary User Entity Controls
- [ ] **Every CUEC transcribed** from the report into `cuecs_required`. An empty list
      means the section was not transcribed, not that none are required.
- [ ] Each CUEC verified internally with **named evidence** (ticket, config export,
      screenshot, policy reference). "Implemented" without evidence does not count.
- [ ] Dual-control withdrawal authorisation, beneficiary address whitelisting, API
      key scoping / IP restriction, and hardware MFA specifically confirmed.

## 6. Sign-off
- [ ] `evaluate_vendor_compliance(vendor_id, as_of)` executed with an **explicit
      as-of date**; `ReviewResult.findings` and `audit_trail` archived to the annual
      custody memorandum.
- [ ] `record_review(vendor_id, as_of)` called once the review is genuinely complete.
- [ ] Escalations raised: `get_vendors_requiring_escalation()` for CRITICAL /
      ESCALATED (freeze new allocation, 24h committee review);
      `get_overdue_vendors()` for OVERDUE / NON_COMPLIANT.
- [ ] Next due date recorded and diarised.

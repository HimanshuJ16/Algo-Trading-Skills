# Workflows for Alternative Data Vendor Due Diligence

## Onboarding Pipeline

1. **Vendor Sourcing**: Quantitative researchers identify a new alternative dataset (e.g., supply chain mapping).
2. **DDQ Issuance**: Compliance/Legal sends the proprietary Due Diligence Questionnaire (DDQ) to the vendor. The DDQ must be dated; an undated or stale DDQ fails closed at evaluation.
3. **Automated Triage**: Vendor responses are fed into `VendorDueDiligenceEvaluator.evaluate()`, which returns a versioned `DiligenceRecord` carrying the `Decision`, `rule_version`, `evaluated_at` (UTC), `risk_tier`, `next_review_date`, and flag/warning codes.
4. **Hard Rejection**: If the record's `decision` is `REJECTED`, the vendor is immediately disqualified. Researchers are barred from accessing even the trial data. The record is persisted regardless of verdict for audit completeness.
5. **Manual Legal Review (warnings branch)**: If the engine returns `APPROVED_WITH_WARNINGS`, the legal team follows the warning-branch rubric below to produce a terminating decision.
6. **Integration**: Once fully cleared, the `DiligenceRecord` is handed to the `alternative-data-feature-integration` pipeline, whose Step 0 requires a current, signed due-diligence record.

## Warning-Branch Decision Rubric

An `APPROVED_WITH_WARNINGS` verdict is **not** a final clearance. It requires a recorded, terminating manual legal review:

| Warning | What clears it | What escalates to rejection | Owner | Recorded in |
|---|---|---|---|---|
| `TOS_NONCOMPLIANT` — CAPTCHA bypass on public data | Target-site ToS explicitly permits automated access | ToS prohibits automated access / scraping | Legal | `audit_notes` on the `DiligenceRecord` |
| `LOGIN_SCRAPE_AUTHORIZED` — authenticated collection under a written instrument | The instrument is on file, is from the source operator, and covers both this collection method and the firm's intended downstream use | No instrument, an instrument from someone other than the source operator, or one that does not reach the firm's use | Legal | `audit_notes` on the `DiligenceRecord` |

The decision (clear or reject) and the reasoning must be appended to `audit_notes` on the persisted record. A warning may not remain open-ended — the onboarding task is incomplete until the warning is cleared or the vendor is rejected.

## Re-Diligence

Approval is **not** permanent. The `DiligenceRecord` carries a `risk_tier` and a derived `next_review_date` governing re-diligence cadence:

| Risk tier | Profile | Cadence |
|---|---|---|
| Tier-1 | Approved with warnings (the manual legal review path) | Annual |
| Tier-2 | PII-bearing but fully anonymized/compliant, or scraped public data | Biennial |
| Tier-3 | Clean, non-PII, non-scraped dataset | Triennial |

Re-run the gate before `next_review_date` and whenever a vendor changes data-collection methodology. The SEC Division of Examinations Risk Alert of April 26 2022 (*Investment Adviser MNPI Compliance Issues*) names, among observed deficiencies, advisers that "[n]eglected to develop processes for determining when due diligence needed to be re-performed based on the passage of time or changes in data collection practices." The same alert flags advisers that had no policies for "assessing the terms, conditions, or legal obligations related to the collection or provision of alternative data." A static, one-time approval is precisely the gap the alert calls out.

## Recovery & Ongoing Monitoring

When any of the following triggers fire, the agent must re-issue the DDQ, force-expire the prior `DiligenceRecord`, and **block downstream data** until the re-evaluation produces a fresh `APPROVED` (or `APPROVED_WITH_WARNINGS`-then-cleared) record:

- `next_review_date` has passed.
- The prior record was produced from a DDQ that later proves undated, stale, or future-dated (`STALE_DDQ` / `FUTURE_DATED_DDQ`).
- The vendor reports a change in data-collection methodology.
- A red flag arises:
  - Enforcement action or regulatory sanction against the vendor.
  - Sanctions / watchlist match on the vendor or its ultimate beneficial owners.
  - Change of vendor ownership or control.
  - Lapsed SOC 2 / ISO 27001 / equivalent attestation.
  - New subprocessors or new processing jurisdictions (cross-border transfer impact).
  - Privacy-policy drift (weaker anonymization commitments).
  - Terms-of-Service / robots.txt drift (newly prohibitive terms on scraped sources).

This is the highest-risk intervention window — it is precisely when compliance must act and there is no current workflow to follow. Treat an expired or red-flagged record as `REJECTED` for gating purposes until a fresh record supersedes it.

## Category
`regulatory-compliance`

# Deep Workflow Reference — custody-solution-vendor-due-diligence-checklist

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Collect the underlying artefacts, not the vendor's summary

Every boolean in `CustodyVendorProfile` is an assertion the reviewer must be able
to support with a document. The engine scores what it is told; it cannot verify a
claim. Collect: the charter or registration itself, the SOC 2 Type II report (not
a summary or a logo on a webpage), the insurance binder, the **executed** custody
agreement, the audited financial statements, and the penetration test attestation.

### 2. Classify the charter — do not accept "we are a qualified custodian"

There is no SEC-granted "Qualified Custodian" designation to hold, so a vendor
asserting one is telling you nothing verifiable. Map the entity to a Rule
206(4)-2(d)(6) category instead:

- `FEDERAL_BANK`, `SEC_BROKER_DEALER`, `REGISTERED_FCM`, `FOREIGN_FINANCIAL_INSTITUTION`
  map directly to a category.
- `STATE_CHARTERED_TRUST` is the **conditional** route. For crypto assets it rests
  on the SEC staff no-action letter of 2025-09-30, which is fact-specific,
  revocable, and expressly did *not* hold that state trust companies satisfy the
  Advisers Act "bank" definition. The engine scores the letter's substantive
  conditions and raises a red flag for any that is unmet.
- An unrecognised `charter_type` **raises** rather than scoring as non-qualifying,
  so a typo cannot be mistaken for a substantive rejection.

The final legal conclusion belongs to counsel. The engine produces evidence for
that conversation, not a substitute for it.

### 3. Score the five pillars

| Pillar | Weight | Components |
|---|---|---|
| `REGULATORY_LEGAL` | 25% | Qualifying basis (60) + bankruptcy-remote segregation (40) |
| `CYBERSECURITY` | 25% | SOC 2 Type II (40) + FIPS level ≥ 3 (35) + annual pen testing (25) |
| `INSURANCE_COVERAGE` | 20% | Coverage ratio against assets under custody |
| `OPERATIONAL_RESILIENCE` | 15% | Uptime SLA (60) + RTO (40) |
| `GOVERNANCE_CONTROLS` | 15% | Segregation of duties (50) + annual pen testing (50) |

Weights are configurable and validated to sum to 1.0, so the composite always
lands on 0–100.

**Segregation carries score, not just a flag.** A custodian that co-mingles client
assets forfeits 40 points of the regulatory pillar in addition to raising a red
flag — it can never present a perfect composite beside a rejection.

**Insurance is scored against assets at risk.** A $100M limit is strong against
$200M of custodied assets and close to irrelevant against $10B. Supply
`assets_under_custody_usd`; when it is absent the engine falls back to the
absolute limit and states in the findings that the ratio was **not** assessed,
rather than implying it passed.

**FIPS 140-2 raises a sunset item.** All remaining FIPS 140-2 certificates move to
the NIST CMVP Historical List on 2026-09-21. The engine flags this and asks for a
FIPS 140-3 roadmap. FIPS 140-3 defines the same Levels 1–4, so a Level 3
requirement carries across unchanged.

### 4. Decide

- Any **red flag** → `REJECTED`. Red flags are structural: no qualifying charter
  category, unmet no-action conditions for a state trust company, or assets that
  are not bankruptcy-remote.
- No red flags, composite ≥ `min_passing_score`, **and no open action items** →
  `APPROVED`.
- Otherwise → `CONDITIONAL_APPROVAL`.

Every scored deficiency produces an action item, so a vendor cannot be approved
while carrying an unremediated finding in any pillar.

### 5. Re-run on a cadence

The no-action letter's conditions include **annual** reassessment of the trust
company's state authorisation and refreshed financial statements and control
reports. Pass `assessment_date` explicitly on every run so the output is
reproducible and the audit trail shows what was known when.

## Production Implementation Reference

- Reference code: `scripts/custody_solution_vendor_due_diligence_checklist.py`
  (`CustodyVendorDueDiligenceEngine`, `CustodyVendorProfile`, `PillarScore`,
  `CustodyVendorDueDiligenceReport`, `CustodyDueDiligenceError`).
- Automated unit tests: `scripts/test_custody_solution_vendor_due_diligence_checklist.py`.

## Known Limitations

- **US only.** UK FCA CASS, EU MiCA, MAS and VARA custody regimes are not modelled.
- **Not a legal determination.** Qualified custodian status is a legal conclusion.
- **Adviser-side obligations are out of scope.** The no-action letter also requires
  client/board risk disclosure and a documented best-interest determination. Those
  are obligations of the adviser, not attributes of the vendor, so they are not
  scored here — see `third-party-custody-audit-report-review-cadence`.
- **Self-asserted inputs.** The engine cannot detect a vendor misrepresenting its
  own SOC scope or insurance perils. Score only what you have read.
- **Insurance perils are not modelled**, only the limit. A large limit covering the
  wrong perils scores identically to a well-matched one.

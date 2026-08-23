---
name: custody-solution-vendor-due-diligence-checklist
description: Institutional due diligence framework for auditing digital asset custodians
  across Advisers Act qualified custodian categories, SOC 2 Type II scope, bankruptcy
  remoteness, insurance coverage relative to assets under custody, and FIPS-validated
  key management.
domain: Crypto Custody & Security
subdomain: Vendor Risk Management
tags:
- custody-due-diligence
- qualified-custodian
- soc2-type2
- bankruptcy-remoteness
- crime-insurance
- vendor-risk
brokers_frameworks:
- SEC Rule 206(4)-2
- SOC 2 Type II
- FIPS 140-3
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating, onboarding, or conducting periodic reviews of third-party digital asset custodians (e.g. Coinbase Custody Trust, BitGo Trust, Anchorage Digital Bank, Fireblocks, Komainu). It turns a documented vendor profile into an auditable weighted score across five risk pillars, with explicit red flags and remediation items, so a custody decision leaves an evidence trail rather than a gut call.

It is most valuable where the regulatory footing is genuinely contested: for **state-chartered trust companies custodying crypto**, qualification rests on a conditional, revocable SEC staff no-action letter dated 2025-09-30 — not on settled law — and this skill scores that letter's substantive conditions explicitly.

## When NOT to Use

- **As a legal determination.** Qualified custodian status is a legal conclusion for counsel. This engine produces evidence for that conversation, not a substitute for it.
- **Outside the US.** UK FCA CASS, EU MiCA, MAS and VARA custody regimes impose materially different requirements and are not modelled.
- **For adviser-side obligations.** Client risk disclosure and documented best-interest determinations are obligations of the adviser, not attributes of the vendor.
- **For self-custody or MPC-in-house designs.** See `crypto-wallet-key-custody-security` and `multi-party-computation-mpc-custody-solutions`.

## Prerequisites

- The underlying artefacts, not the vendor's summary: charter/registration, the full SOC 2 Type II report, insurance binder, **executed** custody agreement, audited GAAP financial statements, penetration test attestation.
- `assets_under_custody_usd` — without it the insurance pillar can only check an absolute limit, and will say so.
- A calibrated threshold policy. The engine defaults (80.0 passing score, 10% coverage ratio, 99.9% uptime, 4h RTO) are **engineering defaults with no regulatory basis**.

## Workflow

1. **Collect Artefacts, Not Assertions**: Every boolean in the profile must be supported by a document you have read. The engine scores what it is told and cannot detect a vendor misrepresenting its own SOC scope or insurance perils.
2. **Classify the Charter — Never Accept "We Are a Qualified Custodian"**: There is no SEC-granted designation to hold, so that claim is unverifiable by construction. Map the entity to a Rule 206(4)-2(d)(6) category. `FEDERAL_BANK`, `SEC_BROKER_DEALER`, `REGISTERED_FCM` and `FOREIGN_FINANCIAL_INSTITUTION` map directly; `STATE_CHARTERED_TRUST` qualifies only through the conditional no-action route and is scored against that letter's conditions. An unrecognised charter string **raises** rather than scoring as non-qualifying, so a typo is never mistaken for a substantive rejection.
3. **Score the Five Pillars**: Regulatory 25% (qualifying basis 60 + bankruptcy-remote segregation 40), Cybersecurity 25% (SOC 2 Type II 40 + FIPS level ≥ 3 35 + pen testing 25), Insurance 20% (ratio against assets under custody), Operations 15% (uptime 60 + RTO 40), Governance 15% (segregation of duties 50 + pen testing 50). Weights are configurable and validated to sum to 1.0.
4. **Decide**: Any red flag → `REJECTED`. No red flags, composite ≥ passing score, **and no open action items** → `APPROVED`. Otherwise → `CONDITIONAL_APPROVAL`. Every scored deficiency produces an action item, so a vendor cannot be approved carrying an unremediated finding.
5. **Re-run on Cadence with an Explicit `assessment_date`**: The no-action conditions require annual re-verification of state authorisation and refreshed financials and control reports. Passing the date explicitly keeps output reproducible and makes the audit trail show what was known when.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a State Trust Charter as Settled Qualification**: The SEC staff no-action letter of 2025-09-30 is conditional, fact-specific and revocable, and it pointedly did **not** hold that state trust companies satisfy the Advisers Act "bank" definition. If the custody agreement permits rehypothecation, or state authorisation was never verified, the relief's conditions are unmet — the charter alone buys nothing.
- **Accepting SOC 2 Type I Instead of Type II**: Type I describes control *design* at a point in time; only Type II tests operating effectiveness over a period. And a Type II is not self-sufficient — check which Trust Services Criteria are in scope, and obtain a bridge letter for any gap between the report period end and today.
- **Judging Insurance by the Headline Limit**: A \$100M policy covers 50% of a \$200M book and 1% of a \$10B one. Score the limit against assets actually at risk. Then read the perils: crime/specie policies cover theft, insider fraud, and physical loss of key material — not market loss, protocol failure, or the custodian's insolvency.
- **Ignoring Balance Sheet Co-mingling**: Assets that are not legally bankruptcy-remote rank alongside the custodian's general creditors on insolvency (Celsius/Voyager precedent). This is a red flag *and* a 40-point regulatory penalty — a co-mingling custodian can never present a perfect score beside a rejection.
- **Requiring FIPS 140-2 in 2026**: All remaining FIPS 140-2 certificates move to the NIST CMVP Historical List on **2026-09-21**. Historical is not revocation, but the certificate should not anchor new procurement — ask for a FIPS 140-3 roadmap. FIPS 140-3 defines the same Levels 1–4, so a Level 3 requirement carries across unchanged.
- **Scoring an Unvalidated Profile**: A 150% uptime SLA or a negative insurance limit is a data-entry error. Scoring it anyway yields an authoritative-looking number built on garbage, so the engine raises `CustodyDueDiligenceError` instead.

## Verification

- Audit a fully compliant `FEDERAL_BANK` profile and confirm `APPROVED` at 100.0 with no red flags and no action items.
- Flip `is_asset_bankruptcy_remote` to `False` and confirm the regulatory pillar drops to 60.0 and the composite to 90.0 — not 100.0 — alongside `REJECTED`.
- Audit a `STATE_CHARTERED_TRUST` with `custody_agreement_prohibits_rehypothecation=False` and confirm a red flag naming rehypothecation and a `REJECTED` decision, while the same flaw leaves a `FEDERAL_BANK` approved.
- Audit a \$100M limit against \$10B under custody and confirm `insurance_coverage_ratio == 0.01` and an insurance pillar score of 10.0.
- Submit `fips_level=99` or `uptime_sla_pct=150.0` and confirm `CustodyDueDiligenceError` rather than an `APPROVED` score of 100.0.
- Run `python -m unittest discover -s skills/custody-solution-vendor-due-diligence-checklist/scripts` and confirm a 100% pass rate.

## Related Skills

- `third-party-custody-audit-report-review-cadence`
- `insurance-coverage-assessment-for-custodied-crypto`
- `regulatory-custody-requirements-by-jurisdiction`
- `multi-party-computation-mpc-custody-solutions`

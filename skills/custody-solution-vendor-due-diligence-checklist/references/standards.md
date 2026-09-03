# Standards — custody-solution-vendor-due-diligence-checklist

## Jurisdiction and scope

Everything below is **US**, and specifically concerns SEC-registered investment
advisers and registered funds under the Investment Advisers Act of 1940. Non-US
custody regimes (UK FCA CASS, EU MiCA, MAS, VARA) impose materially different
requirements and are out of scope. Nothing here is legal advice; qualified
custodian status is a legal conclusion for counsel, not a scoring output.

## The "qualified custodian" concept — what it is and is not

There is **no SEC-granted designation called "Qualified Custodian"**. No entity
applies for it, holds it, or has it revoked. Rule 206(4)-2(d)(6) defines
*categories* of institution that qualify, and an entity either falls into one or
does not:

| Category | Notes |
|---|---|
| Banks and savings associations | Includes OCC-chartered national trust banks |
| Registered broker-dealers | Holding client assets in a customer account |
| Registered futures commission merchants | For the assets they may custody |
| Certain foreign financial institutions | Subject to the rule's conditions |

Treating "is a qualified custodian" as a verified boolean supplied by the vendor
is a category error. The engine takes a `charter_type` and reports which
category, if any, it maps to — leaving the legal conclusion to counsel.

Source: [SEC — Custody of Funds or Securities of Clients by Investment Advisers](https://www.sec.gov/rules-regulations/2002/07/custody-funds-or-securities-clients-investment-advisers)
(Rule 206(4)-2 adopting release and rule text).

## State-chartered trust companies custodying crypto — conditional staff relief

This is the single most important nuance for digital asset custody, and it is not
what a naive reading of "state trust charter = qualified custodian" assumes.

- The SEC's proposed **Safeguarding Rule** (proposed 2023-02-15), which would have
  extended the custody rule expressly to non-security crypto assets, was
  **formally withdrawn on 2025-06-12** among fourteen withdrawn rule proposals.
  Rule 206(4)-2 therefore remains the operative custody rule.
- On **2025-09-30** the SEC's **Division of Investment Management** issued a
  **staff no-action letter** stating it would not recommend enforcement action
  against advisers and registered funds using a **state trust company** as
  qualified custodian for crypto assets and related cash, **provided** conditions
  are met.
- The letter **does not hold that state trust companies satisfy the Advisers Act
  "bank" definition.** It grants conditional enforcement forbearance, not
  definitional clarity.
- Staff no-action positions are **not rules**, are fact-specific, and are
  revocable.

Conditions relevant to vendor diligence (the engine checks the substantive ones):

| Condition | Modelled as |
|---|---|
| Reasonable basis, after inquiry, that the state regulator authorises the trust company to provide crypto custody — reassessed **annually** | `state_authorization_verified` |
| Written policies reasonably designed to safeguard crypto assets | covered by SOC report + key management |
| Audited GAAP financial statements | `provides_audited_gaap_financials` |
| Recent independent internal control report ("e.g., SOC-1 report or SOC-2 report") | `has_soc2_type2_unqualified` |
| Custody agreement prohibits lending, pledging, rehypothecation or transfer without written consent, and requires segregation from proprietary assets | `custody_agreement_prohibits_rehypothecation`, `is_asset_bankruptcy_remote` |
| Disclosure of material risks to clients / fund board, plus a documented best-interest determination | **not modelled** — an adviser-side obligation, not a vendor attribute |

Sources: [Sidley — SEC Staff Issues No-Action Relief Permitting Use of State-Chartered Trust Companies as Qualified Custodians of Digital Assets](https://www.sidley.com/en/insights/newsupdates/2025/10/sec-staff-issues-no-action-relief-permitting-use-of-state-chartered-trust-companies),
[Dechert — SEC Staff Grants No-Action Relief for Certain State-Chartered Crypto Custodians](https://www.dechert.com/knowledge/onpoint/2025/10/sec-staff-grants-no-action-relief-for-certain-state-chartered-cr.html),
[Morgan Lewis — Crypto Custody Breakthrough](https://www.morganlewis.com/pubs/2025/10/crypto-custody-breakthrough-sec-staff-grants-relief-for-registered-funds-advisers),
[Proskauer — SEC Formally Withdraws Fourteen Rule Proposals](https://www.proskauer.com/alert/sec-withdraws-fourteen-rule-proposals).

## Cryptographic module validation — FIPS 140-2 is sunsetting

| Fact | Date |
|---|---|
| CMVP stopped accepting new FIPS 140-2 submissions | 2021-09-22 (limited exceptions to 2022-04-01) |
| **All remaining FIPS 140-2 certificates move to the CMVP Historical List** | **2026-09-21** |

Historical status is **not** revocation — a module already in service keeps
working — but NIST's guidance is that agencies should not include Historical
modules in new procurements. A custodian whose key management rests solely on a
FIPS 140-2 certificate has a live remediation item, and the engine raises one.

FIPS 140-3 (aligned to ISO/IEC 19790:2012) defines the **same four security
levels**, so a Level 3 requirement carries across; only the standard version
changes. Level 3 is the meaningful institutional floor: it adds tamper detection
and response and identity-based authentication over Level 2's tamper-evidence.

Sources: [NIST CMVP](https://csrc.nist.gov/projects/cryptographic-module-validation-program),
[FIPS 140-3 (final)](https://csrc.nist.gov/pubs/fips/140-3/final).

## SOC 2 Type II

A Type II report tests **operating effectiveness over a period** (typically 6–12
months); a Type I only describes control design at a point in time. Two checks
that a boolean "has SOC 2 Type II" hides:

- **Scope** — which Trust Services Criteria are in scope. Security is the common
  criterion; Availability and Confidentiality are optional additions.
- **Period coverage** — the report period must cover your review window; obtain a
  **bridge letter** for any gap between the report period end and today.

## Engineering defaults (not regulatory requirements)

No regulator prescribes a custodian due diligence score, an insurance coverage
ratio, or an uptime SLA. These are configurable policy inputs — calibrate them to
your firm's mandate and record the calibration.

| Parameter | Default | Meaning |
|---|---|---|
| `min_passing_score` | 80.0 | Composite score floor for approval |
| `min_insurance_usd` | $50,000,000 | Absolute policy limit floor |
| `min_insurance_coverage_ratio` | 0.10 | Limit as a fraction of assets under custody |
| `min_uptime_sla_pct` | 99.9 | Contractual uptime floor |
| `max_rto_hours` | 4.0 | Recovery Time Objective ceiling |
| Pillar weights | 25/25/20/15/15 | Validated to sum to 1.0 |

Note on insurance: crime/specie policies cover **named perils** — theft, insider
fraud, physical loss or destruction of key material — not market loss, protocol
failure, or the custodian's insolvency. No major custodian insures anything close
to 100% of assets under custody, so a coverage ratio is a relative risk signal,
not an attainable target. Read the perils and sub-limits, not the headline limit.

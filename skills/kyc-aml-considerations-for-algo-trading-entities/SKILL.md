---
name: kyc-aml-considerations-for-algo-trading-entities
description: >-
  Use when onboarding a trading fund or proprietary firm with a prime broker, exchange
  or clearing member, covering both FinCEN customer due diligence prongs: the 25%
  beneficial-ownership test and the mandatory control person.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: kyc, aml, ubo, fincen-cdd-rule, fatf, sanctions-screening, pep, enhanced-due-diligence, ofac-50-percent-rule
  brokers_frameworks: "FinCEN CDD Rule (31 CFR 1010.230); OFAC 50 Percent Rule; FATF Recommendations 12/19/24; EU Regulation 2024/1624 (AMLR); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when onboarding an algorithmic trading fund, proprietary trading firm, or trading corporate with a prime broker, exchange, clearing member, or OTC desk — from either side of the desk. It turns a documented ownership and screening file into a reproducible onboarding decision with a citation-bearing finding trail, so the outcome leaves evidence rather than a judgement call.

It is worth reaching for specifically because the beneficial-ownership question is where trading-entity onboarding usually goes wrong. Master-feeder structures, offshore GPs, and nominee holding layers routinely produce a cap table where **nobody** clears the 25% threshold — and a screen that only implements the ownership prong then approves an entity with zero identified natural persons.

## When NOT to Use

- **As a sanctions screening system.** Every `is_sanctioned` / `is_pep` field is an *input*. The caller must already have run each name against OFAC SDN/Consolidated, EU, UN, and UK HMT data and recorded the result — see `sanctions-screening-for-counterparties-and-instruments`. This engine cannot detect a name that was never screened.
- **As a legal determination.** Whether a specific structure satisfies a specific regulator is a conclusion for counsel and your MLRO, not a status string.
- **As your firm's AML program.** A program under 31 U.S.C. 5318(h) means policies, a designated compliance officer, independent testing, and training. This engine is one control inside such a program.
- **For transaction monitoring or SAR/STR decisioning.** Wholly different problem; nothing here looks at trading activity.
- **For natural-person retail onboarding.** The beneficial-ownership prongs are about *legal entity* customers.

## Prerequisites

- **Documented ownership**, not a self-certification form alone: register of members, LP agreement, or equivalent, resolved down to natural persons.
- **Screening results already obtained** for the entity name, every declared UBO, and the control person.
- The **control-prong individual** — one person with significant responsibility to control, manage, or direct the entity (CEO, CFO, COO, Managing Member, General Partner, President, Vice President, Treasurer, or anyone regularly performing similar functions), with their title.
- **Current FATF lists.** The bundled `FATF_LISTS_2026_06_19` snapshot is a starting default, not a feed. FATF republishes at every plenary (roughly February, June, October).
- Country identifiers your system can resolve to **ISO 3166-1 alpha-2**. Anything unresolvable is rejected, never assumed low risk.

## Workflow

1. **Fix the scope before the screen.** Decide whether you are the covered financial institution running CDD (bank, registered broker-dealer, mutual fund, FCM, introducing broker in commodities) or the trading entity assembling the file you will be asked for. The obligation in 31 CFR 1010.230 is the institution's; the fund is normally the *legal entity customer*. Do not assume your fund is itself a BSA financial institution — the 2024 rule that would have covered RIAs and ERAs was pushed from 2026-01-01 to **2028-01-01**.
2. **Normalise jurisdictions first, and fail closed.** Resolve incorporation and banking countries to alpha-2 before comparing them to anything. A system emitting `"IR"` against a list holding `"IRAN"` clears Iran on every screen and looks perfectly healthy doing it. `normalize_country` raises on anything it cannot resolve rather than returning a "clean" result.
3. **Apply both beneficial-ownership prongs — the control prong is not a fallback.** Aggregate each natural person's direct *and indirect* holdings before comparing to the threshold: 15% through one vehicle plus 15% through another is 30%, not two sub-threshold records. Then identify and verify the single control person **independently**, whether or not anyone cleared 25%. Under 31 CFR 1010.230(e)(3), a pooled investment vehicle advised by a non-excluded financial institution is subject to the control prong *only* — which is precisely the fund case, and precisely why an ownership-only screen is not enough.
4. **Test what is *not* declared, not only what is.** If the declared cap table leaves more than one threshold's worth of ownership unattributed, an undisclosed holder could sit at or above 25% and the assertion "all beneficial owners are identified" cannot be supported. That residual test — not a round policy number — is what catches the shell layer.
5. **Classify the sanctions hit before deciding what to do about it.** A minority blocked owner is a reason to decline. Blocked persons holding **50% or more in the aggregate** is a different legal event: the entity is itself blocked property whether or not it appears on the SDN List, so the property must be **blocked and reported to OFAC**, not quietly declined. OFAC aggregates across blocked owners — two blocked persons at 25% each reach the threshold.
6. **Tier the jurisdiction risk instead of collapsing it.** Of the FATF call-for-action jurisdictions, only Iran and the DPRK carry a call for *counter-measures*. Myanmar carries a call for *enhanced due diligence proportionate to the risk*, with FATF expressly asking that humanitarian, NPO and remittance flows not be disrupted. Grey-list jurisdictions call for EDD, not refusal.
7. **Treat a PEP as an EDD trigger, never as a rejection.** For a foreign PEP, FATF requires senior management approval, established source of wealth *and* source of funds, and enhanced ongoing monitoring. Discharge those and the relationship proceeds. Domestic and international-organisation PEPs are risk-based.
8. **Read every finding, not just the status.** `status` is the highest-precedence blocking finding; `report.findings` holds all of them, each with its citation. Pass `assessment_date` explicitly so the record is reproducible.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Implementing the ownership prong and calling it CDD.** 31 CFR 1010.230(d) has two prongs, and the control prong is mandatory and independent — a legal entity customer yields between one and five beneficial owners, and the *one* is always the control person. Five holders at 20% each produce no ownership-prong beneficial owner at all; an ownership-only screen approves that structure having verified nobody.
- **Screening a country string in the wrong namespace.** The failure that actually happens in production is not a typo, it is a format mismatch: an upstream feed switches to ISO alpha-2 and every name-based list comparison silently stops matching. Treating an unrecognised jurisdiction as "not on any list" makes the screen fail *open*, and it will never raise an alert about it.
- **Treating each shareholding record as a separate person.** The rule says "directly or indirectly". Two 15% holdings by the same individual through separate vehicles is a 30% beneficial owner who must be identified; comparing record-by-record misses exactly the structure the layering was built to hide.
- **Accepting a cap table that only adds to 26%.** Nothing in the arithmetic complains. But 74% of the entity is then behind undeclared holding companies, any one of which could carry a 25% natural person — the "look-through" failure this skill exists to catch.
- **Rejecting PEPs.** FATF's PEP measures are preventive and expressly not an implication of criminality; auto-rejecting is de-risking, and it is not what R.12 asks for. (The previous version of this skill's own standards reference said PEP matches "MUST result in immediate onboarding rejection" — that was wrong, and it is corrected in `references/standards.md`.)
- **Collapsing all three FATF call-for-action jurisdictions into one rule.** Counter-measures apply to Iran and the DPRK. Myanmar is EDD. R.19 requires measures "proportionate to the risks", not a blanket cut-off — and FATF specifically asks that humanitarian and remittance flows survive it.
- **Missing the OFAC 50% aggregation.** Checking each blocked owner against 50% individually never triggers. OFAC aggregates: 25% + 25% by two blocked persons blocks the entity.
- **Short-circuiting the audit on the first failure.** If the engine returns on a jurisdiction hit before screening the owners, the compliance record it writes says "no sanctions hit" about a screen that never ran. That is a false negative preserved in an audit file.
- **Running against a stale FATF snapshot.** A hard-coded list with no `as_of` date silently mis-screens for months after a plenary. Date the snapshot and alert on its age.

## Verification

- Audit the clean baseline (60/40 verified owners, verified CEO, US/US) and confirm `KYC_AML_APPROVED` with no blocking findings and 0.0% unaccounted ownership.
- Remove the control person from that same clean file and confirm `REJECTED_NO_CONTROL_PERSON` — the ownership prong is fully satisfied and the entity must still be rejected.
- Audit five holders at 20% each with no control person and confirm `REJECTED_NO_CONTROL_PERSON` with `unverified_ubos_count == 0`: the ownership prong found nobody, which is the point.
- Declare the same individual twice at 15%, both unverified, and confirm `REJECTED_UNVERIFIED_UBO` with `unverified_ubos_count == 1` — aggregation across the threshold.
- Audit a single declared 26% owner and confirm `REJECTED_OWNERSHIP_OPACITY` with `unaccounted_ownership_pct == 74.0`.
- Audit two blocked persons at 25% each and confirm `REJECTED_OFAC_50_PERCENT_RULE` with `aggregate_sanctioned_ownership_pct == 50.0`; drop one to 24% and confirm it falls back to `REJECTED_SANCTIONS_MATCH`.
- Screen `"IR"`, `"Iran"`, and `"islamic republic of iran"` and confirm all three reject identically; screen `"ATLANTIS"` and confirm `KycAmlValidationError` rather than a clean pass.
- Audit a Myanmar incorporation and confirm `KYC_AML_EDD_REQUIRED`, not `REJECTED_FATF_HIGH_RISK`; audit an Iran incorporation and confirm the rejection.
- Audit a foreign PEP and confirm `KYC_AML_EDD_REQUIRED` with two outstanding conditions; set `senior_management_approval_obtained` and `source_of_wealth_documented` and confirm `KYC_AML_APPROVED`.
- Audit an Iran incorporation whose UBOs also carry a sanctions hit, and confirm the report still records `has_sanctions_hit=True` and the real ownership total rather than unscreened zeros.
- Submit `float("nan")` ownership and confirm `KycAmlValidationError` rather than a silent below-threshold pass.
- Run `python -m unittest discover -s skills/kyc-aml-considerations-for-algo-trading-entities/scripts` and confirm a 100% pass rate.

## Related Skills

- `sanctions-screening-for-counterparties-and-instruments`
- `custody-solution-vendor-due-diligence-checklist`
- `record-retention-periods-by-jurisdiction`
- `cross-jurisdiction-regulatory-conflict-resolution`
- `regulatory-change-monitoring-service-integration`

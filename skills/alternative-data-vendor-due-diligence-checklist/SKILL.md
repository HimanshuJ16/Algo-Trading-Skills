---
name: alternative-data-vendor-due-diligence-checklist
description: Institutional compliance engine for automating alternative data vendor
  due diligence. Evaluates PII, MNPI, and web scraping (CFAA) legal risks.
domain: regulatory-compliance
subdomain: data-sourcing
tags:
- compliance
- alternative-data
- mnpi
- pii
- legal-risk
brokers_frameworks:
- generic
version: "1.3.0"
author: System
license: MIT
---

## When to Use

Use this skill when onboarding a new Alternative Data vendor (e.g., credit card receipts, satellite imagery, web-scraped job postings, social media sentiment). Hedge funds face massive legal and reputational risk if they ingest **Material Non-Public Information (MNPI)** or illegally obtained data (e.g., violations of the Computer Fraud and Abuse Act (CFAA) or GDPR/CCPA). This skill automates the primary triage of the vendor's Due Diligence Questionnaire (DDQ) and emits a versioned, audit-serializable `DiligenceRecord` that downstream pipelines (e.g. `alternative-data-feature-integration`) consume as the gating artifact.

## When NOT to Use

This skill is a **triage gate only**. An `APPROVED` verdict means the vendor cleared the legal-rights, MNPI, scraping/CFAA, ToS, and PII/anonymization checks — it is **not** a complete compliance clearance. The following are out of scope and belong to other skills:

- **Data quality, coverage, and freshness** of the delivered dataset.
- **Point-in-time correctness** and lookahead-safe availability lag.
- **Cross-border data-transfer lawfulness** (GDPR Chapter V, SCCs, adequacy decisions).
- **Retention and deletion obligations** (storage TTLs, right-to-erasure workflows).
- **Quantitative signal validity** (predictive value, decay, capacity).

Do not treat a passing `DiligenceRecord` as license to skip those assessments.

## Prerequisites

- Python 3.9+
- The vendor's completed DDQ responses regarding data provenance, PII scrubbing, scraping methodologies, and Terms-of-Service compliance.
- An independent verification artifact for the attested booleans (right-to-audit exercised, sample-data inspection, or third-party attestation) — see `references/standards.md`. Vendor self-attestation alone is insufficient (SEC App Annie enforcement, Sept 14 2021, Admin. Proc. Rel. No. 34-92975).
- For any behind-login collection, the **written authorization instrument from the source operator**, read by the firm, before `has_documented_login_authorization` is set.

## Workflow

1. **Ingest DDQ**: Map the vendor's answers into the `VendorDueDiligenceQuestionnaire` dataclass. Every boolean field is strictly type-checked at construction — a sloppily-mapped truthy string (e.g. `has_resell_rights='no'`) raises `TypeError` rather than silently approving. Cross-field consistency is enforced: `bypasses_captchas=True` or `scrapes_behind_login=True` with `is_web_scraped=False` raises `ValueError`, as does `has_documented_login_authorization=True` without `scrapes_behind_login=True`.
2. **Evaluate Risk**: Pass the DDQ to `VendorDueDiligenceEvaluator`. The rule set is configurable: `VendorDueDiligenceEvaluator(max_ddq_age_days=...)` tightens the freshness window (default 365 days).
3. **Hard Rejections**: The engine hard-rejects (decision `REJECTED`) vendors who:
   - Scrape behind password-protected logins **without documented authorization** from the source operator (CFAA exposure). `has_documented_login_authorization` defaults to `False`, so an unmapped or unevidenced DDQ still hard-rejects; setting it `True` downgrades the reject to a `LOGIN_SCRAPE_AUTHORIZED` warning that must go through recorded legal review, never to a clean approval.
   - Ingest PII without strict GDPR/CCPA anonymization workflows.
   - Do not hold the legal rights to resell the underlying data.
   - Collect via methods non-compliant with the source Terms of Service.
   - Submit an undated, stale, or future-dated DDQ (fails closed; cannot verify freshness). A questionnaire dated more than one day ahead of the UTC evaluation date is a data-entry or backdating error, not a fresh DDQ.
4. **Approval with Warnings**: If only non-critical flags remain (e.g. CAPTCHA bypassing on public data), the verdict is `APPROVED_WITH_WARNINGS`. This branch requires a recorded manual legal review with a terminating decision (see `references/workflows.md` for the warning-branch rubric).
5. **Clean Approval**: If all compliance checks pass with zero warnings, the verdict is `APPROVED`. The `DiligenceRecord` carries a computed `risk_tier` and a derived `next_review_date` governing re-diligence cadence.
6. **Persist the Record**: Persist the emitted `DiligenceRecord` (`dataclasses.asdict`-serializable) to the firm's system of record. It echoes `vendor_name`/`dataset_name`, the `Decision`, `rule_version`, `evaluated_at` (UTC), `risk_tier`, `next_review_date`, flag/codes, and `audit_notes`.
7. **Re-Diligence**: Approval is **not permanent**. Re-run the gate before `next_review_date` (Tier-1 = annual, Tier-2 = biennial, Tier-3 = triennial) and whenever a vendor changes data-collection methodology. See `references/workflows.md`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## DDQ Response-Mapping Guidance

Mapping a vendor's free-form DDQ answers to the boolean fields is the highest-judgment step and currently has no automated guardrail. Pay particular attention to the two hardest judgments:

- **`is_material_non_public_information`**: Set this on the dataset's **provenance**, never on how predictive it is. Alternative data is nonpublic and potentially material by construction — that is why the firm is buying it — so a "would this let us infer something before public disclosure?" test answers `True` for every dataset worth owning and turns this gate into a blanket ban. US liability under Rule 10b-5 requires a **breach of a duty of trust or confidence** (*Dirks*, *O'Hagan*, 17 CFR 240.10b5-2), and MAR Recital 28 provides that research and estimates prepared from publicly available data are not per se inside information. Set `True` when the origin implies a breached duty: leaked or hacked material, data supplied to the vendor under confidentiality or under a consent that does not cover resale for research, an insider or tippee source. Where provenance is genuinely unknown, set `True` and escalate — to establish provenance, not to debate signal strength. See `references/standards.md`; this is the same test `insider-trading-controls-for-alternative-data-usage` applies downstream.
- **`has_robust_anonymization`**: "Robust" is an operator rubric, not a vendor assertion. Require evidence that singling-out, linkability, and inference risks were assessed under the "means likely reasonably to be used" test (GDPR Recital 26; Art. 29 WP Opinion 05/2014). Identifier removal alone is insufficient. See `references/standards.md`.

For the remaining fields, map the vendor's narrative to the literal boolean: answer the yes/no question the field asks, not the question the vendor's marketing answers.

## Common Pitfalls

- **Setting the MNPI flag on predictive power**: The flag is a provenance test, not a signal-quality test. An operator who reasons "this dataset predicts the print, therefore it is MNPI" hard-rejects every dataset the firm would ever want, and an operator who reasons "it is assembled from public sources, therefore it is safe" misses the case that actually matters — public-looking data that reached the vendor through someone's breached duty. Ask where the data came from.
- **Trusting vendor self-attestation**: The App Annie enforcement is the canonical case of a vendor misrepresenting anonymization/MNPI status. Require independent verification (right-to-audit, sample-data inspection, or third-party attestation) for `has_resell_rights`, `contains_pii`, `is_material_non_public_information`, and `has_robust_anonymization`.
- **Treating all web scraping as equally risky**: Post-*Van Buren v. United States* (2021) and *hiQ v. LinkedIn*, 31 F.4th 1180 (9th Cir. 2022), scraping **public** data without authentication is likely **not** a CFAA violation — route it to a Terms-of-Service review only. Scraping **behind a login** without documented authorization remains real CFAA exposure and is a hard reject: the December 2022 consent judgment ending *hiQ* included a CFAA violation for accessing password-protected pages with fake accounts. Do not over-reject a legitimate public-data vendor, and do not under-review a behind-login one.
- **Reading "not a CFAA violation" as "no exposure"**: hiQ won the CFAA point on public pages and still took a $500,000 judgment for breaching LinkedIn's user agreement. `is_tos_compliant` is a separate critical flag for exactly this reason — clearing the CFAA question does not clear the contract question.
- **Ignoring ToS for Scraped Data**: Assuming that because data is "on the internet," it is legal to scrape. If a vendor scrapes data by violating explicit Terms of Service, the purchasing fund inherits that legal risk.
- **Inadequate PII Scrubbing**: Trusting a vendor who claims they "don't collect PII" without auditing the actual scrubbing mechanism.
- **Approving on a stale DDQ**: A two-year-old questionnaire cannot evidence current data practices. The gate fails closed on an undated, stale, **or future-dated** DDQ — a negative age is a data-entry or backdating error, not extra freshness.

## Verification

The onboarding task is complete only when **all** of the following hold (not merely when the unit tests pass):

- [ ] The `DiligenceRecord` is persisted to the firm's system of record (DDQ inputs, decision, flags, reviewer, CCO sign-off, `evaluated_at` timestamp, `rule_version`).
- [ ] For an `APPROVED_WITH_WARNINGS` verdict, a recorded manual legal review has produced a terminating decision (clear or reject), captured in `audit_notes`.
- [ ] For an approved vendor, `next_review_date` is set (derived from `risk_tier`) and added to the re-diligence calendar.
- [ ] CCO sign-off is captured on the persisted record.
- [ ] Independent verification artifacts (right-to-audit, sample-data inspection, or third-party attestation) are attached for the attested booleans.

Run `python scripts/test_alternative_data_vendor_due_diligence_checklist.py` (or `python -m unittest discover -s skills/alternative-data-vendor-due-diligence-checklist/scripts`) to confirm the engine produces the expected `Decision`/`FlagCode` verdicts.

## Worked Examples

- **Credit-card-receipts vendor**: `contains_pii=True`, `is_gdpr_ccpa_compliant=True`, `has_robust_anonymization=True` (independently verified), `has_resell_rights=True`, `is_material_non_public_information=False`, `is_web_scraped=False`. Verdict: `APPROVED`, `Tier-2` (PII present but fully compliant), biennial re-diligence. The PII checks are satisfied, so nothing raises a warning — the dataset still carries the firm's heaviest ongoing anonymization-reassessment burden despite the clean verdict.
- **Satellite-imagery vendor**: `has_resell_rights=False`. Verdict: `REJECTED`, `NO_RESELL_RIGHTS`. No re-diligence; vendor is blocked until rights are documented.
- **Partner-portal vendor**: `is_web_scraped=True`, `scrapes_behind_login=True`, `has_documented_login_authorization=True` (data-sharing agreement on file and read). Verdict: `APPROVED_WITH_WARNINGS`, `LOGIN_SCRAPE_AUTHORIZED`, `Tier-1`. Legal must confirm the agreement covers both the collection method and the firm's downstream use before the vendor is cleared. With the same DDQ and `has_documented_login_authorization=False`: `REJECTED`, `CFAA_LOGIN_SCRAPE`.

## Related Skills

- `algorithmic-trading-firm-licensing-thresholds`
- `insider-trading-controls-for-alternative-data-usage`
- `alternative-data-feature-integration`

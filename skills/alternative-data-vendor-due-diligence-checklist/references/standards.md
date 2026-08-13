# Standards for Alternative Data Vendor Due Diligence

| Risk Category | Action Required | Standard |
|---|---|---|
| **MNPI** | Immediate Disqualification | Data must not be derived from confidential corporate insiders or breaches of fiduciary duty. See *MNPI materiality rubric* below. |
| **CFAA / Scraping** | Legal Review / Disqualification | Vendors must not scrape behind authenticated, password-protected portals without explicit authorization. Public-data scraping is a ToS question, not a CFAA question, post-*Van Buren* / *hiQ*. See *Scraping* below. |
| **PII (GDPR/CCPA)** | Anonymization Required | Raw PII must never touch the firm's internal servers. Vendors must anonymize/aggregate prior to delivery. See *Robust anonymization* below. |
| **Terms of Service** | Evidence Required | Collection method must comply with the source ToS; non-compliance is a critical flag. ToS can drift post-onboarding — re-review on the re-diligence cadence. |
| **Data License Scope** | Evidence Required | Resell rights alone are insufficient. The license must cover the firm's intended usage scope. See *License scope* below. |
| **Evidence / Self-Attestation** | Independent Verification | Attested booleans (`has_resell_rights`, `contains_pii`, `is_material_non_public_information`, `has_robust_anonymization`) require right-to-audit exercised, sample-data inspection, or third-party attestation. Vendor self-attestation alone is insufficient. |

## MNPI materiality rubric

`is_material_non_public_information` is the single hardest judgment in alternative-data compliance. Operator rubric:

- **SEC materiality** — *Basic Inc. v. Levinson*: information is material if there is a substantial likelihood a reasonable investor would rely on it (the "reasonable-investor" test).
- **EU MAR** — Regulation (EU) 596/2014 Article 7 defines "inside information" via a reasonable-investor test; Recital 14 ties materiality to price significance.
- **Public/aggregate data is not per se safe** — MAR Recital 28 carves out that research/estimates from public data are not inside information *per se*, but can become so if routinely price-formative. A dataset assembled from public or aggregate sources that yields a non-public signal a reasonable investor would rely on **is MNPI/inside information** (App Annie, SEC Admin Order 34-92975, Sept 14 2021).
- **Decision rule**: if the dataset would let the firm infer a non-public corporate outcome before public disclosure, set `is_material_non_public_information=True`. When uncertain, fail closed (set `True`) and escalate to legal.

## EU MAR cross-reference

For dual-jurisdiction (US/EU) funds, the insider-trading dimension has an EU analogue alongside SEC Rule 10b-5:

- **MAR Article 7** defines "inside information" via the reasonable-investor test and the price-significance limb.
- **Recital 28** clarifies that research/estimates derived from public information are not inside information by default, but **become** inside information when they are routinely used as a basis for trading and a reasonable investor would rely on them.
- Treat the `is_material_non_public_information` gate as a single, jurisdiction-agnostic test covering both Rule 10b-5 and MAR Article 7. A dataset failing this gate is blocked in both jurisdictions.

## Scraping (post-Van Buren / hiQ)

Distinguish two regimes:

- **Public scraping** — after *Van Buren v. United States* (2021) and *hiQ v. LinkedIn* (9th Cir. 2022), scraping publicly accessible data without authentication is likely **not** a CFAA violation. Route to a **Terms-of-Service review** only. A CAPTCHA bypass on public data is a *warning* (`APPROVED_WITH_WARNINGS`), not a hard reject, pending ToS clearance.
- **Behind-login scraping** — accessing an authenticated portal without authorization remains real CFAA exposure. This is a **hard reject** (`CFAA_LOGIN_SCRAPE`).

The code already hard-rejects `scrapes_behind_login`; the documentation here corrects the prior implication that all scraping is high-risk.

## Robust anonymization

"Robust" is an operator rubric, not a vendor assertion. Per GDPR Art. 29 Working Party Opinion 05/2014 and Recital 26, require evidence of:

1. **Singling-out** risk assessment — can a single individual be isolated from the dataset?
2. **Linkability** risk assessment — can records be linked across datasets to re-identify?
3. **Inference** risk assessment — can non-public attributes be derived about an individual?
4. **"Means likely reasonably to be used" test** (Recital 26) — anonymization must withstand reasonably likely re-identification attacks, not just trivial ones.
5. **Irreversibility** — identifier removal or pseudonymization alone is insufficient; the process must be irreversible.
6. **Periodic reassessment** — re-identification techniques evolve; anonymization claims must be re-validated on the re-diligence cadence.

A vendor claiming anonymization without this evidence base invites the App Annie failure mode (vendor claims anonymization, uses identifiable data). Do not set `has_robust_anonymization=True` on self-attestation alone.

## License scope

Real alternative/market-data licenses (e.g. Nasdaq, NYSE, BPX) include terms beyond resell rights that the gate cannot see directly but that block production use:

- **Usage scope** — internal business use only vs. broader permitted uses.
- **Redistribution restrictions** — whether derived data may be shared with clients/counterparties.
- **Derived-data recreation restrictions** — whether the dataset may be used to recreate the underlying data.
- **Audit rights** — notice period and survival terms for the firm's right to audit the vendor.
- **Point-in-time / historical boundaries** — how far back and how far forward the license covers.

A vendor with resell rights but no audit rights or a competitive-use ban still blocks production use. Capture these terms in the persisted `DiligenceRecord.audit_notes` and require the license itself (not just the DDQ representation) as evidence.

## App Annie enforcement (canonical lesson)

SEC v. App Annie, Admin Order 34-92975 (Sept 14 2021): App Annie represented to subscribers that its data was anonymized and aggregated and did not constitute MNPI, while in fact using non-public, identifiable data and sharing select metrics with trading firms. This is exactly the failure mode the gate trusts away when it relies on vendor self-attestation for `has_robust_anonymization` and `is_material_non_public_information`. The independent-verification requirement above is the direct mitigation.

## Category
`regulatory-compliance`

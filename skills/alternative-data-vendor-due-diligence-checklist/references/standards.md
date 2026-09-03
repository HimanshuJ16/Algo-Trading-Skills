# Standards for Alternative Data Vendor Due Diligence

| Risk Category | Action Required | Standard |
|---|---|---|
| **MNPI** | Immediate Disqualification | Data must not be derived from confidential corporate insiders or breaches of fiduciary duty. See *MNPI rubric* below. |
| **CFAA / Scraping** | Legal Review / Disqualification | Scraping behind authenticated, password-protected portals **without documented authorization** is a hard reject; **with** a documented authorization instrument on file it is a warning requiring recorded legal review. Public-data scraping is a ToS/contract question, not a CFAA question, post-*Van Buren* / *hiQ*. See *Scraping* below. |
| **PII (GDPR/CCPA)** | Anonymization Required | Raw PII must never touch the firm's internal servers. Vendors must anonymize/aggregate prior to delivery. See *Robust anonymization* below. |
| **Terms of Service** | Evidence Required | Collection method must comply with the source ToS; non-compliance is a critical flag. ToS can drift post-onboarding — re-review on the re-diligence cadence. |
| **Data License Scope** | Evidence Required | Resell rights alone are insufficient. The license must cover the firm's intended usage scope. See *License scope* below. |
| **Evidence / Self-Attestation** | Independent Verification | Attested booleans (`has_resell_rights`, `contains_pii`, `is_material_non_public_information`, `has_robust_anonymization`, `has_documented_login_authorization`) require right-to-audit exercised, sample-data inspection, third-party attestation, or — for the authorization field — the instrument itself on file. Vendor self-attestation alone is insufficient. |
| **DDQ Freshness** | Fail Closed | An undated, stale, or future-dated questionnaire cannot evidence current practices and is a hard reject (`STALE_DDQ` / `FUTURE_DATED_DDQ`). The SEC's Apr 26 2022 Risk Alert names failure to determine *when* diligence must be re-performed as an observed deficiency. |

## MNPI rubric — set the flag on provenance, not on predictive power

`is_material_non_public_information` is the single hardest judgment in alternative-data compliance, and the most common way to get it wrong is to set it on how *good* the signal is.

Alternative data is nonpublic, and in aggregate potentially material, by construction — that is precisely why a fund buys it. A rubric of the form "would this let us infer something before public disclosure?" answers `True` for every dataset worth owning. Because `is_material_non_public_information=True` is a **hard reject** in this engine, such a rubric silently converts a triage gate into a blanket ban on alternative data.

Materiality is necessary but not sufficient:

- **US** — liability under Rule 10b-5 turns on a **breach of a duty of trust or confidence**, under either the classical or the misappropriation theory (*Dirks v. SEC*, 463 U.S. 646 (1983); *United States v. O'Hagan*, 521 U.S. 642 (1997); the qualifying duties are enumerated at 17 CFR 240.10b5-2). Materiality itself is the *Basic Inc. v. Levinson*, 485 U.S. 224 (1988) standard — a substantial likelihood that a reasonable investor would consider the information important, significantly altering the "total mix" of information made available. A material, non-public dataset acquired with **no breached duty anywhere in its chain of custody** is the ordinary, lawful case for alternative data.
- **EU MAR** — Regulation (EU) No 596/2014 Article 7(1)(a) defines inside information partly by whether, if made public, it would be likely to have a significant effect on price; Article 7(4) defines that limb by reference to information a reasonable investor would be likely to use as part of the basis of investment decisions. **Recital 28** provides that research and estimates prepared on the basis of publicly available data are **not per se** inside information. Recital 28's counterweight is narrow and runs to the *publication* of such research — an analysis carrying the views of a recognised market commentator, or one the market routinely expects and that contributes to price formation, can make the forthcoming publication itself inside information for those who know of it. It does not make the underlying dataset inside information.

**Decision rule (provenance).** Set `is_material_non_public_information=True` when the dataset's **origin** implies a breached duty: leaked, hacked, or misappropriated material; data supplied to the vendor under a confidentiality obligation, or under a consent that does not cover resale for investment research; an insider or tippee source; a corporate counterparty's confidential records. Where provenance is genuinely unknown, set `True` and escalate — but escalate in order to *establish provenance*, not to argue about how predictive the data is.

This is the same test the sibling skill `insider-trading-controls-for-alternative-data-usage` applies at the trading-eligibility stage. The two gates must not disagree about what the flag means.

App Annie is a **provenance** case, not a predictive-power case: the underlying app-performance data was confidential data supplied by developers under a promise of aggregation and anonymisation, and was used in non-aggregated, non-anonymised form (SEC Admin. Proc. Rel. No. 34-92975, Sept 14 2021). Cite it for the evidence and anonymisation requirements below, not for the proposition that public or aggregate data is per se MNPI.

## EU MAR cross-reference

For dual-jurisdiction (US/EU) funds, the insider-trading dimension has an EU analogue alongside SEC Rule 10b-5:

- **Article 7(1)(a)** defines inside information partly by price significance; **Article 7(4)** defines the price-significance limb by reference to information a reasonable investor would be likely to use as part of the basis of investment decisions.
- **Recital 28** provides that research and estimates prepared on the basis of publicly available data are not per se inside information. Its qualification runs to the *publication* of the research (a recognised commentator's view, or an analysis the market routinely expects and that contributes to price formation), not to the dataset the research was built from.
- MAR has **no Section 204A analogue** and frames the prohibition differently from Rule 10b-5; the two regimes are not interchangeable. This gate deliberately collapses them into one conservative flag, so a `True` blocks the vendor in both jurisdictions. Where a dual-jurisdiction question is close, run the MAR analysis separately — see `eu-market-abuse-regulation-mar-surveillance`.

## Scraping (post-Van Buren / hiQ)

Distinguish three regimes:

- **Public scraping** — after *Van Buren v. United States* (2021) and *hiQ v. LinkedIn*, 31 F.4th 1180 (9th Cir., Apr 18 2022), scraping publicly accessible data without authentication is likely **not** a CFAA violation. Route to a **Terms-of-Service review** only. A CAPTCHA bypass on public data is a *warning* (`APPROVED_WITH_WARNINGS`), not a hard reject, pending ToS clearance.
- **Unauthorized behind-login scraping** — accessing an authenticated portal without authorization remains real CFAA exposure, and the point is not academic. The December 2022 consent judgment that ended *hiQ v. LinkedIn* entered a $500,000 judgment against hiQ that included a **CFAA violation based on direct access to password-protected pages using fake accounts**, alongside breach of contract, trespass to chattels and misappropriation, plus a permanent injunction to stop scraping and destroy the derived data. This is a **hard reject** (`CFAA_LOGIN_SCRAPE`).
- **Authorized behind-login scraping** — after *Van Buren* the CFAA question is entitlement to access, so a vendor collecting from an authenticated portal **under a written instrument from the source operator** (contract, API licence, partner data-sharing agreement) is a different case. Set `has_documented_login_authorization=True` **only** when the firm holds and has read that instrument. It downgrades the hard reject to a warning (`LOGIN_SCRAPE_AUTHORIZED`) requiring recorded legal review — never to a clean approval. Legal must confirm the instrument covers this collection method *and* the firm's intended downstream use. The field defaults to `False`, so an unmapped or unevidenced DDQ still hard-rejects.

"Not a CFAA violation" is not "no exposure". hiQ prevailed on the CFAA question for *public* pages at the preliminary-injunction stage and still lost on breach of LinkedIn's user agreement, which the district court held enforceable against scraping in November 2022. That is why `is_tos_compliant` is a separate, independently-critical flag rather than a sub-case of the CFAA analysis.

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

SEC Admin. Proc. Rel. No. 34-92975 (Sept 14 2021), the SEC's first enforcement action against an alternative-data provider: App Annie assured the app companies supplying its data that their confidential performance data would be used only in aggregated, anonymised form, and assured its trading-firm subscribers that the estimates were generated consistently with the consents it had obtained. In fact it used non-aggregated, non-anonymised confidential data to adjust its model-generated estimates before selling them. App Annie and its founder paid over $10 million. This is exactly the failure mode the gate trusts away when it relies on vendor self-attestation for `has_robust_anonymization` and `is_material_non_public_information`. The independent-verification requirement above is the direct mitigation.

## Category
`regulatory-compliance`

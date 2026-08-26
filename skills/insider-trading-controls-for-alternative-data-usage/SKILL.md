---
name: insider-trading-controls-for-alternative-data-usage
description: >-
  Adviser-side trading-eligibility gate for onboarded alternative datasets, producing a per-control audit record across MNPI provenance, vendor diligence and terms-of-service posture, PII scrubbing with panel aggregation, and firm-policy earnings blackout windows.
domain: Quant Research & Alt Data
subdomain: Compliance & Legal Governance for Alt Data
tags: ["alt-data", "insider-trading", "sec-rule-10b5", "mnpi", "section-204a", "pii-anonymization", "compliance-governance"]
brokers_frameworks: ["SEC Rule 10b-5", "17 CFR 240.10b5-2", "Section 204A Investment Advisers Act", "GDPR Recital 26 / CCPA-CPRA 1798.140", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill at the point where an **already-onboarded** alternative dataset (satellite imagery, credit card transactions, web-scraped text, consumer geolocation) is about to drive trading, and on every re-check thereafter. It converts a dataset's compliance posture into an auditable `AltDataComplianceReport` recording, control by control, what was checked and what failed.

The controls support an investment adviser's **Section 204A** obligation to maintain *and enforce* written policies reasonably designed to prevent misuse of material non-public information. That obligation is independently chargeable: the SEC has settled Section 204A actions where the orders contained **no finding that anyone actually traded on MNPI** — the deficient policy was the violation.

## When NOT to Use

- **Not for vendor onboarding triage.** Legal-rights, CFAA/scraping posture, ToS review, and anonymisation *methodology* belong to `alternative-data-vendor-due-diligence-checklist`, which emits the `DiligenceRecord` this engine expects to already exist. This skill consumes that outcome as the `has_vendor_diligence_signoff` input; it does not re-derive it.
- **Not a legal determination.** Insider-trading liability turns on breach of a **duty of trust or confidence** (*United States v. O'Hagan*, 521 U.S. 642 (1997); 17 CFR 240.10b5-2), not on a boolean. A `LOW_RISK_APPROVED` verdict is evidence for counsel, not a substitute for counsel.
- **Outside the US.** EU MAR (Art. 7, 8, 14) defines inside information and the prohibition differently and has no Section 204A analogue; see `eu-market-abuse-regulation-mar-surveillance`. UK, Singapore and Hong Kong regimes are not modelled.
- **Not an issuer-level restricted-list check.** This gate scores a *dataset*. Whether a specific issuer is restricted, and whether the firm holds MNPI from a non-alt-data channel, is a separate control.
- **Not a data-quality or lookahead gate.** See `backtesting-alt-data-strategies-with-realistic-availability-lag`.

## Prerequisites

- A completed `AltDataDatasetSpec`. Every boolean must be a real `bool` backed by **evidence the firm holds**, not a vendor assertion — the engine raises `AltDataComplianceError` on a coerced value rather than scoring it.
- A current vendor diligence record from `alternative-data-vendor-due-diligence-checklist`.
- **A calibrated, written threshold policy.** The defaults (`min_panel_aggregation_count=50`, `earnings_blackout_window_hours=48.0`) are **engineering defaults with no regulatory basis** — no regulator prescribes either number. See `references/standards.md` before adopting them.

## Workflow

1. **Set `has_mnpi_risk` on provenance, not on predictive power.** Alt data is *nonpublic and material by design* — that is why it is bought. The SEC staff's own position is that alternative data "does not necessarily contain MNPI." The flag belongs to a different question: does the dataset's **origin** imply a breached duty of trust or confidence — leaked or hacked material, data supplied to the vendor under confidentiality obligations, an insider or tippee source? Where provenance is genuinely unknown, default to `True` and escalate; do not guess in the permissive direction.
2. **Carry the vendor diligence outcome, and separate it from ToS.** `has_vendor_diligence_signoff` records that a current diligence record exists; `is_tos_compliant` records that collection is consistent with the source's terms. They fail into distinct `failed_controls` entries because the exposures differ: a missing sign-off is a Section 204A policy failure, while a terms breach is contract/tort exposure **and** can itself supply the confidentiality duty whose breach makes the data MNPI. Re-run the gate whenever the vendor changes collection methodology — the 2022 Risk Alert cites failure to define *when* re-diligence is required as a named deficiency.
3. **Score PII scrubbing and panel aggregation as two controls.** `is_pii_scrubbed` asks whether identifiers were removed and that removal verified; `panel_aggregation_count` is a k-anonymity-style cell-size floor. A 500,000-contributor panel that was never scrubbed fails *scrubbing* — the audit note must say so rather than blame the panel size.
4. **Apply the earnings blackout as declared firm policy.** `hours_to_earnings_release` is a **signed** distance: positive before the release, negative after. The gate compares its absolute value, so the window is two-sided. Pass `None` when no release is scheduled in the monitoring horizon.
5. **Classify, then read the detail, not just the verdict.** All four controls are evaluated unconditionally before classification, so every `is_*_cleared` flag is a tested result. `risk_classification` reports the most severe failure; `failed_controls` reports all of them. Persist the whole report — the record is the point.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a vendor's written representation as the control.** App Annie's Terms of Service *promised* that developer metrics would be used only in aggregated, anonymised form; the SEC found it used non-aggregated, non-anonymised data anyway to sharpen estimates for trading clients (Admin. Proc. 34-92975, 2021-09-14; \$10M and \$300k penalties, the first securities-fraud action against an alt-data provider). Obtaining the representation is necessary and insufficient. Require independent verification — right-to-audit exercised, sample-data inspection, or third-party attestation.
- **Mapping compliance answers through a string.** A spec assembled from CSV, JSON, or an LLM tool call carries `'no'` and `'false'` strings. Every non-empty string is truthy, so a naive engine reads `is_pii_scrubbed='no'` as a **pass** on the very control it exists to enforce. This engine raises rather than coerces. Do not add a `bool()` cast at the call site to make the error go away.
- **Reporting a clearance you never tested.** A rejection on one gate must not hard-code the others as cleared. An MNPI-rejected dataset one hour from an earnings release is *not* outside the blackout window, and a record saying otherwise is a false statement in a compliance file.
- **Presenting the thresholds as legal requirements.** Neither GDPR Recital 26 nor CCPA/CPRA § 1798.140 specifies a minimum group size, and no rule imposes an alt-data earnings blackout at all — the only codified waiting periods in this area are the Rule 10b5-1(c) cooling-off periods (90 days for directors and officers, 30 for others), which govern insider trading *plans*, not research data. Documenting `N ≥ 50` as "required by GDPR" misstates the law to your own auditors and to any regulator reading the file.
- **Assuming lawful scraping means unrestricted scraping.** After *Van Buren v. United States*, 593 U.S. 374 (2021) and the *hiQ v. LinkedIn* line, scraping public pages without authentication is likely **not** a CFAA violation — but hiQ still lost on **breach of contract**, settling in December 2022 with a \$500,000 judgment and admitted liability for trespass to chattels. "Not a federal crime" is not "no exposure."
- **Reading `BLACKOUT_WINDOW_RESTRICTED` as a rejection.** It is a time-boxed pause on an otherwise-clean dataset; it clears on its own. Wiring it to the same remediation path as `REJECTED_MNPI_RISK` buries genuine rejections in routine noise.

## Verification

- Audit a compliant satellite dataset (no MNPI risk, diligence signed off, ToS clean, PII scrubbed, panel 250, 72h to earnings) and confirm `LOW_RISK_APPROVED` with `failed_controls == ()`.
- Set `has_mnpi_risk=True` on a spec that is *also* 1h from earnings with `panel_aggregation_count=1`, and confirm `REJECTED_MNPI_RISK` **with `is_blackout_window_cleared` and `is_pii_anonymization_cleared` both `False`** — no untested gate may report a pass.
- Set `is_pii_scrubbed=False` with `panel_aggregation_count=500_000` and confirm the note names PII scrubbing and does **not** claim the panel is below the minimum.
- Set only `is_tos_compliant=False` and confirm `failed_controls == ('TERMS_OF_SERVICE',)`, distinguishable from a missing sign-off.
- Pass `has_vendor_diligence_signoff='no'` and confirm `AltDataComplianceError` — not `LOW_RISK_APPROVED`.
- Check the boundaries: `panel_aggregation_count=50` clears and `49` fails; `hours_to_earnings_release=48.0` clears and `47.9` restricts; `-6.0` restricts exactly as `+6.0` does.
- Pass `float('nan')` or a negative panel count and confirm `AltDataComplianceError` rather than a scored report.
- Run `python -m unittest discover -s skills/insider-trading-controls-for-alternative-data-usage/scripts` and confirm a 100% pass rate.

## Related Skills

- `alternative-data-vendor-due-diligence-checklist`
- `alternative-data-feature-integration`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `web-scraped-sentiment-data-pipeline`
- `eu-market-abuse-regulation-mar-surveillance`

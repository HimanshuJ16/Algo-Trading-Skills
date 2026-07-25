---
name: alternative-data-vendor-due-diligence-checklist
description: Institutional compliance engine for automating alternative data vendor due diligence. Evaluates PII, MNPI, and web scraping (CFAA) legal risks.
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
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when onboarding a new Alternative Data vendor (e.g., credit card receipts, satellite imagery, web-scraped job postings, social media sentiment). Hedge funds face massive legal and reputational risk if they ingest **Material Non-Public Information (MNPI)** or illegally obtained data (e.g., violations of the Computer Fraud and Abuse Act (CFAA) or GDPR/CCPA). This skill automates the primary triage of the vendor's Due Diligence Questionnaire (DDQ).

## Prerequisites

- Python 3.9+
- The vendor's completed DDQ responses regarding data provenance, PII scrubbing, and scraping methodologies.

## Workflow

1. **Ingest DDQ**: Map the vendor's answers into the `VendorDueDiligenceQuestionnaire` dataclass.
2. **Evaluate Risk**: Pass the DDQ to the `VendorDueDiligenceEvaluator`.
3. **Hard Rejections**: The engine will automatically hard-reject vendors who:
   - Scrape behind password-protected logins without explicit authorization (CFAA violation risk).
   - Ingest PII without strict GDPR/CCPA anonymization workflows.
   - Do not hold the legal rights to resell the underlying data.
4. **Approval**: If all compliance checks pass, the vendor is flagged as `APPROVED` and can proceed to quantitative backtesting.

## Common Pitfalls

- **Ignoring ToS for Scraped Data**: Assuming that because data is "on the internet," it is legal to scrape. If a vendor scrapes data by bypassing CAPTCHAs or violating explicit Terms of Service, the purchasing hedge fund inherits that legal risk.
- **Inadequate PII Scrubbing**: Trusting a vendor who claims they "don't collect PII" without auditing their actual scrubbing mechanism.

## Verification

Run `python scripts/test_alternative_data_vendor_due_diligence_checklist.py` to ensure that MNPI, PII, and Web Scraping violations correctly trigger immediate compliance rejections.

## Related Skills

- `algorithmic-trading-firm-licensing-thresholds`
- `insider-trading-controls-for-alternative-data-usage`

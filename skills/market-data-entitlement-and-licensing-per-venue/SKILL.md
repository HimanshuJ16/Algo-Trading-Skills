---
name: market-data-entitlement-and-licensing-per-venue
description: >-
  Pre-stream market data entitlement and compliance engine, verifying exchange venue licenses (NASDAQ, NYSE, CME, LSE), professional vs non-professional subscriber classification, and non-display algorithmic licensing.
domain: Data Management Global
subdomain: Exchange Licensing & Data Entitlement Governance
tags: ["market-data", "entitlements", "licensing", "non-display-license", "professional-subscriber", "exchange-compliance", "dacs", "emrs"]
brokers_frameworks: ["CME Non-Display Policy", "CTA/UTP Entitlement Agreement", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing market data feed entitlements and exchange compliance across global trading venues (NASDAQ, NYSE, CME, LSE, Eurex). Stock exchanges enforce strict **Non-Display Licensing** for automated algorithms and **Professional Subscriber** classifications for corporate entities. Ingesting market data for trading algorithms without proper Non-Display licenses or under retail Non-Professional accounts exposes trading firms to severe exchange audit fines and stream revocations.

## Prerequisites

- User entitlement profile (`user_id`, `is_professional`, `has_non_display_license`, `licensed_venues`: set of venue IDs, `license_expiry_dates`: dict).
- Data stream request (`user_id`, `venue_id`: `NASDAQ`/`CME`/`LSE`, `data_level`: `L1`/`L2`/`L3`, `usage_type`: `DISPLAY`/`NON_DISPLAY_ALGO`, `request_timestamp_epoch`).

## Workflow

1. **Subscriber Classification Audit**:
   - Audit `is_professional`. Corporate entities and automated bots MUST be classified as Professional.
   - If misclassified $\implies$ Trigger `ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER`.
2. **Non-Display Licensing Audit**:
   - If `usage_type == "NON_DISPLAY_ALGO"`, verify `has_non_display_license == True`.
   - If missing $\implies$ Trigger `ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE`.
3. **Venue & Expiration Entitlement Gate**:
   - Verify `venue_id` exists in user's `licensed_venues`. If missing $\implies$ Trigger `ENTITLEMENT_DENIED_UNLICENSED_VENUE`.
   - Audit license expiration date. If expired $\implies$ Trigger `ENTITLEMENT_DENIED_EXPIRED_LICENSE`.
4. **Audit Report Generation**: Output structured `EntitlementAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Retail Accounts for Trading Bots**: Ingesting market data for automated algorithms using retail Non-Professional credentials, incurring massive back-fee penalties during exchange audits.
- **Ignoring Non-Display License Requirements**: Running black-box trading engines on direct exchange feeds without paying exchange-mandated Non-Display Application licensing fees.
- **Allowing Expired Venue Licenses**: Failing to track venue entitlement expiration dates, leading to mid-session market data stream cuts.

## Verification

- Instantiate `MarketDataEntitlementEngine`. Audit Institutional Algorithmic Bot (`is_professional=True`, `has_non_display=True`, `licensed_venues={"CME", "NASDAQ"}`) requesting `CME` L2 Non-Display feed $\implies$ verify `ENTITLEMENT_APPROVED`. Audit Bot misclassified as `is_professional=False` $\implies$ verify `ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER`. Audit Unlicensed Venue request (`LSE`) $\implies$ verify `ENTITLEMENT_DENIED_UNLICENSED_VENUE`.
- Run `python scripts/test_market_data_entitlement_and_licensing_per_venue.py`.

## Related Skills

- `vendor-specific-adjustment-methodology-reconciliation`
- `data-vendor-contractual-usage-restriction-tracking`
---

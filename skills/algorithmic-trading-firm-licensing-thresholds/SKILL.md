---
name: algorithmic-trading-firm-licensing-thresholds
description: Evaluates proprietary trading activity against global regulatory thresholds (SEC Rule 15b9-1, MiFID II, SEBI) to trigger mandatory firm licensing and registration alerts.
domain: regulatory-compliance
subdomain: legal-and-registration
tags:
  - compliance
  - sec
  - finra
  - mifid-ii
  - hft
brokers_frameworks:
  - generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when auditing a proprietary trading firm's or hedge fund's trading activity to ensure they are not operating an unlicensed broker-dealer or unauthorized High-Frequency Trading (HFT) desk. As trading volume, messaging rates, and off-exchange activity scales, firms frequently cross regulatory thresholds (such as the SEC's revised Rule 15b9-1 or MiFID II's HFT designation) requiring formal regulatory registration.

## Prerequisites

- Python 3.9+
- Firm-wide trading metrics including Orders Per Second (OPS), off-exchange (dark pool) trading volume, and primary operating jurisdiction.

## Workflow

1. **Data Aggregation**: Collect the firm's trading metadata (`FirmTradingActivity`).
2. **Jurisdictional Evaluation**: Pass the activity to the `LicensingThresholdEvaluator`.
3. **Threshold Checking**:
   - **US (SEC/FINRA)**: Checks if the firm has off-exchange flow, invalidating the historical 15b9-1 exemption, thereby mandating FINRA membership.
   - **EU (MiFID II)**: Checks if message rates exceed the HFT designation threshold, requiring an Investment Firm license.
   - **IN (SEBI)**: Checks if the system exceeds the retail Orders Per Second (OPS) limit, requiring formal Algo Registration.
4. **Action**: If a threshold is breached, trading must be suspended until formal legal registration is obtained to avoid severe civil or criminal penalties.

## Common Pitfalls

- **Relying on Outdated Exemptions**: Assuming a proprietary trading firm is exempt from FINRA registration because they have no customers. The 2023 SEC amendments to Rule 15b9-1 effectively closed this loophole for modern HFT firms.
- **Ignoring Message Rates (EU)**: Failing to measure peak quotes per second, leading to an accidental and illegal transition into a regulated HFT entity under MiFID II.

## Verification

Run `python scripts/test_algorithmic_trading_firm_licensing_thresholds.py` to assert that threshold breaches correctly trigger non-compliance alerts across US, EU, and IN jurisdictions.

## Related Skills

- `algorithmic-trading-disclosure-to-exchange-membership`
- `finra-algo-trading-registration-requirements`

---
name: annual-compliance-attestation-workflow
description: Automated compliance verification engine for SEC Rule 206(4)-7 and FINRA
  Rule 3130 annual attestations in quantitative hedge funds.
domain: regulatory-compliance
subdomain: institutional-reporting
tags:
- compliance
- sec-20647
- finra-3130
- regulatory
- attestation
brokers_frameworks:
- generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill to automate the year-end compliance checklist for a quantitative hedge fund or broker-dealer. It programmatically verifies that all mandatory regulatory obligations have been met before the Chief Executive Officer (CEO) and Chief Compliance Officer (CCO) can sign the final annual attestation. 

## Prerequisites

- Python 3.9+
- Audit logs of the CEO/CCO annual meeting.
- Audit logs of quantitative code integrity reviews and trade surveillance testing.

## Workflow

1. **Collect Attestation Data**: Gather the dates of mandatory compliance reviews, including algorithmic code risk assessments, trade surveillance tests, and the formal CEO-CCO meeting.
2. **Evaluate SEC Rule 206(4)-7**: The engine verifies that the annual review of written policies was completed within the calendar year.
3. **Evaluate FINRA Rule 3130**: If the firm is a broker-dealer, the engine mandates that a formal CEO-CCO meeting occurred prior to the CEO signing the certification.
4. **Evaluate Quant-Specific Controls**: The engine specifically flags missing reviews of algorithmic trading code integrity and trade surveillance, which are high-priority SEC examination targets for quantitative funds.
5. **Issue Report**: Returns a compliance report detailing any missing obligations.

## Common Pitfalls

- **Rubber-Stamping Rule 3130**: The CEO signing the FINRA 3130 certification without having the legally required meeting with the CCO in the preceding 12 months.
- **Generic Manuals**: Using off-the-shelf compliance manuals that fail to explicitly require testing of algorithmic code integrity, exposing the firm during an SEC sweep.

## Verification

Run `python scripts/test_annual_compliance_attestation_workflow.py` to confirm that missing CEO-CCO meetings or algorithmic risk reviews correctly block the final compliance attestation.

## Related Skills

- `algorithmic-trading-firm-licensing-thresholds`
- `kill-switch-and-drawdown-circuit-breakers`

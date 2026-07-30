---
name: cross-border-data-transfer-restrictions-for-trade-data
description: Quantitative data governance engine for enforcing cross-border trade
  data transfer compliance (GDPR, PIPL, Swiss Secrecy), tokenizing PII, and auditing
  cross-jurisdiction telemetry egress.
domain: Compliance & Data Governance
subdomain: Cross-Border Data Regulations
tags:
- cross-border
- data-governance
- gdpr
- pipl
- pii-masking
- tokenization
- data-residency
brokers_frameworks:
- Generic Compliance Engine
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-national quantitative trading architectures to enforce cross-border data transfer regulations (e.g. EU GDPR, China PIPL/DSL, Swiss Banking Secrecy, Singapore PDPA). Streaming raw trade telemetry containing Personally Identifiable Information (PII)—such as trader IDs, client names, tax IDs, or account numbers—across national borders without data masking or localization compliance violates financial privacy laws. This module audits destination rules, tokenizes PII fields, and blocks illegal data egress.

## Prerequisites

- Trade payload records containing PII and execution telemetry (`trader_id`, `client_name`, `account_number`, `symbol`, `qty`, `price`).
- Jurisdiction policy mapping for origin and destination countries.

## Workflow

1. **Jurisdiction Policy Audit**:
   - Query policy for `origin_country` $\to$ `destination_country`.
   - Check if transfer is `BLOCKED`, `REQUIRES_ANONYMIZATION`, or `ALLOWED_UNRESTRICTED`.
2. **PII Masking & Tokenization**:
   - If `REQUIRES_ANONYMIZATION`:
     - Tokenize `trader_id` $\to \text{SHA256}(trader\_id)$.
     - Mask `client_name` $\to \text{ANONYMOUS\_CLIENT}$.
     - Redact `account_number` $\to \text{XXXX-XXXX-1234}$.
3. **Egress Interception & Audit Report**:
   - Return clean payload if compliant, or raise `DataTransferViolationError` if transfer to restricted destination is blocked.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Transmitting Raw PII to Global Cloud Aggregators**: Sending raw trader names and client account numbers from local execution servers in China or Switzerland to a centralized US AWS S3 bucket.
- **Incomplete Tokenization**: Hashing the trader ID but leaving the taxpayer ID or full account number exposed in metadata tags.
- **Assuming Internal Transfers are Exempt**: Believing that moving data between subsidiary legal entities across borders does not trigger cross-border data transfer regulations.

## Verification

- Instantiate `CrossBorderTradeDataGovernanceEngine`. Configure policy: `CN` (China) $\to$ `US` requires PII anonymization, `CH` (Switzerland) $\to$ `US` blocks raw account numbers. Submit a trade payload from `CN` to `US` containing client name "John Doe" and trader ID "TRADER_99". Verify output payload has masked client name and hashed trader ID. Submit a blocked transfer and verify egress block.
- Run `python scripts/test_cross_border_data_transfer_restrictions_for_trade_data.py`.

## Related Skills

- `data-vendor-contractual-usage-restriction-tracking`
- `data-retention-policy-and-storage-tiering`
---

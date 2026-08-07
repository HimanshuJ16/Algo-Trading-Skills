---
name: backtest-audit-trail-for-regulatory-review
description: Use when conducting production backtests to record and sign a immutable
  audit trail capturing code git commit SHA, data version checksum, hyperparameter
  manifest, and execution environment metadata for regulatory review (e.g. SEC/FINRA/MiFID
  II).
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- regulatory-compliance
- audit-trail
- reproducibility
- sec-compliance
- data-lineage
brokers_frameworks:
- Regulatory Audit Trail Engine
- Python Cryptography
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when performing strategy validation prior to live deployment or institutional client presentation. Regulatory authorities (e.g. SEC, FINRA, ESMA/MiFID II) and institutional risk committees mandate that backtested performance claims be backed by an immutable audit trail. This skill generates cryptographic audit manifests linking code version (git commit SHA), input data checksums, hyperparameter configurations, and execution logs.

## Prerequisites

- Git repository commit SHA.
- SHA256 checksums of input price/volume datasets.
- Strategy parameter configuration dictionary.

## Workflow

1. **Capture Lineage Metadata**: Extract git commit SHA, user ID, system timestamp, and OS platform metadata.
2. **Compute Input Data Checksums**: Generate SHA256 hashes of all input CSV/Parquet files.
3. **Assemble Audit Manifest**: Combine code SHA, data hashes, parameters, and resulting Sharpe/drawdown performance metrics.
4. **Generate Immutable Audit Cryptographic Hash**:
   $$\text{AuditHash} = \text{SHA256}(\text{JSON}(\text{Manifest}))$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Untracked Code Modifications**: Running backtest on uncommitted local git working copy, rendering SHA meaningless.
- **Missing Data Versioning**: Failing to record exact dataset version or vendor download date.

## Verification

- Generate audit manifest for strategy backtest, verify valid SHA256 signature and metadata schema.
- Run `python scripts/test_regulatory_audit_trail.py` and confirm 100% pass rate.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `paper-to-live-promotion-checklist`
---

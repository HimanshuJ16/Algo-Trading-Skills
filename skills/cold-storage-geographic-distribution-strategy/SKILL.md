---
name: cold-storage-geographic-distribution-strategy
description: Institutional crypto custody module for analyzing Shamir Secret Sharing
  (SSS) M-of-N key shard distribution across geographically and jurisdictionally diverse
  vaults, auditing single-point-of-failure (SPOF) risks.
domain: Crypto Custody & Security
subdomain: Key Management
tags:
- crypto-custody
- cold-storage
- shamir-secret-sharing
- geographic-distribution
- jurisdiction-risk
- spof
brokers_frameworks:
- Generic Crypto Security
- NumPy
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing or auditing cold storage security architecture for institutional crypto assets. Storing all private key shards or seed backups within a single physical facility or legal jurisdiction creates extreme Single Point of Failure (SPOF) risk from natural disasters, physical raids, or regulatory seizure. This module evaluates an $M$-of-$N$ Shamir Secret Sharing distribution matrix to ensure no single jurisdiction or facility holds $\ge M$ shards required for key reconstruction.

## Prerequisites

- $M$-of-$N$ threshold parameters (e.g. 3-of-5 or 5-of-7 scheme).
- Inventory of vault locations, legal jurisdictions, and security standards (e.g. ISO 27001, SOC 2 Type II, DIN EN 1047-1).

## Workflow

1. **Vault Registration**: Register candidate vault locations (`location_id`, `country_code`, `jurisdiction`, `facility_provider`).
2. **Shard Allocation**: Assign key shards $1 \dots N$ across registered vaults.
3. **SPOF Audit**:
   - Count shards per country code and jurisdiction.
   - Flag violation if $\text{Country\_Shards} \ge M$ (allowing single-country key reconstruction).
   - Flag violation if $\text{Provider\_Shards} \ge M$ (allowing single-entity collusion).
4. **Resilience Scoring**: Compute a score based on threshold safety margin $N - M$, jurisdictional entropy, and physical security ratings.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Jurisdiction Concentration**: Placing 3 out of 5 key shards in different cities within the *same* country. A single domestic court order or tax seizure can compel all 3 shards.
- **Provider Collusion Risk**: Using different physical vaults operated by the *same* commercial custodian company.
- **Threshold Imbalance**: Using a 2-of-3 scheme where losing just 2 shards permanently destroys access to funds.

## Verification

- Instantiate `ColdStorageGeographicDistributor` with a 3-of-5 scheme. Attempt to place 3 shards in Switzerland (CH). Verify that the audit fails with a `Single Jurisdiction SPOF Violation`. Re-distribute shards across CH, SG, IS, US, and JP and verify 100% compliance pass.
- Run `python scripts/test_cold_storage_geographic_distribution_strategy.py`.

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `air-gapped-signing-workflow-for-cold-storage`

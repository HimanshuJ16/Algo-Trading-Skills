---
name: cold-storage-geographic-distribution-strategy
description: >-
  Use when auditing how M-of-N key shards are spread across facilities, countries and
  legal regimes, flagging both single points of failure and quorum concentrations.
  Generating the shards is shamir-secret-sharing-for-key-backup.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: crypto-custody, cold-storage, shamir-secret-sharing, geographic-distribution, jurisdiction-risk, spof
  brokers_frameworks: "Generic Crypto Security; Python Standard Library"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when designing or auditing cold storage security architecture for institutional crypto assets held under an $M$-of-$N$ Shamir Secret Sharing scheme. Concentrating shards in one facility, one country, one legal regime, or one commercial custodian creates a Single Point of Failure in **both** directions:

- **Confidentiality SPOF** - one group holds $\ge M$ shards and can reconstruct the key alone (seizure order, raid, insider collusion).
- **Availability SPOF** - one group holds $> N - M$ shards and, by becoming unavailable, leaves fewer than $M$ shards reachable, permanently destroying access to the assets.

## When NOT to Use

Do **not** use this skill to split or reconstruct secrets - it never touches key material and performs no cryptography. Shard generation belongs to `shamir-secret-sharing-for-key-backup`. It is also not a substitute for legal advice on where assets may lawfully be held: it audits a placement you supply, and no regulator surveyed prescribes a shard geography (see `references/standards.md`).

## Prerequisites

- $M$-of-$N$ threshold parameters (e.g. 3-of-5 or 5-of-7), with $M \ge 2$.
- Inventory of vault locations with ISO 3166-1 alpha-2 country code, the legal jurisdiction that can compel each vault, and the custodian entity operating it.
- Facility certification evidence. Note these standards cover different threats and none of them alone certifies a vault: ISO/IEC 27001 and SOC 2 Type II are organisational information-security assurances, EN 1143-1 grades burglary resistance of safes and strongrooms, and EN 1047-1 covers fire protection of data media only and confers no burglary resistance.

## Workflow

1. **Vault Registration**: Build a `VaultShardLocation` per shard (`shard_id`, `vault_name`, `country_code`, `provider_name`, optional `jurisdiction`, `is_iso_27001`). Country codes and provider names are upper-cased and stripped on construction so `'ch'`, `'CH'` and `' Ch '` group as one country; free-text country names are rejected because they would split a single country into several groups and hide a concentration.
2. **Jurisdiction Mapping**: Set `jurisdiction` explicitly whenever the compelling legal regime differs from the vault's country - a shared bloc, a parent entity's home regime, or a sub-custodian supervised elsewhere. When omitted it defaults to `country_code`, which is the optimistic assumption; if in doubt, map the broader regime.
3. **Placement Audit**: Call `audit_distribution`. It rejects a malformed matrix outright - wrong shard count, shard ids outside $[1, N]$, or duplicate shard ids, since two copies of one shard are one shard and would otherwise inflate every safety margin.
4. **Interpret Violations**: For each country, jurisdiction and provider group the audit reports a Confidentiality violation when the group holds $\ge M$ shards, otherwise an Availability violation when it holds $> N - M$. A placement can fail on availability while passing confidentiality (4-of-6 with 3 shards in one country) - do not treat "no reconstruction risk" as safe.
5. **Redundancy Reserve**: The audit flags $N - M$ below `min_redundancy_gap` (default 2, an internal engineering default, not a regulatory figure). Raise or lower it deliberately; lowering it accepts that fewer lost shards destroy access.
6. **Report**: `AuditReport` carries `is_compliant`, the per-group maxima, `redundancy_gap`, the country-level Shannon entropy and the violation list. Remediate every violation and re-audit; entropy is descriptive context only and never gates compliance.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing Confidentiality Only**: A 4-of-6 scheme with 3 shards in one country passes a reconstruction-risk check (3 < 4) yet is one country-level outage away from permanent, unrecoverable loss (6 - 3 = 3 < 4). Both directions must be checked on every group.
- **Single Jurisdiction Concentration**: Three shards in three different cities of the *same* country - one domestic court order or tax seizure reaches all three. Multi-city is not multi-jurisdiction.
- **Country-Level Grouping Only**: Vaults in different countries can still sit under one effective legal regime through a shared bloc or a sub-custodian's home supervisor. Country counts of 1 each hide this; the `jurisdiction` field is what catches it.
- **Provider Collusion Risk**: Different physical vaults operated by the *same* commercial custodian, or by subsidiaries of one parent, are one adversary.
- **Duplicated Shards Counted as Coverage**: Copying shard 3 into a second vault raises the count of places the key can leak from without raising the number of distinct shards; it weakens confidentiality and adds no reconstruction margin.
- **Threshold Imbalance**: A 2-of-3 scheme tolerates only one lost shard; the second loss is terminal. Choose $N - M$ against the real probability of losing a vault, not against convenience.
- **Treating Entropy as a Safety Score**: High Shannon entropy does not imply threshold safety - two placements with identical entropy can differ in whether a single group reaches $M$. Only the violation list is authoritative.
- **Assuming a Certificate Covers the Threat**: An EN 1047-1 data safe resists fire and provides no defined burglary resistance; ISO 27001 certifies a management system, not the steel around the shard.

## Verification

- Instantiate `ColdStorageGeographicDistributor(threshold_m=3, total_shards_n=5)` and place 3 shards in Switzerland (`CH`). Verify the report is non-compliant with a `Country Confidentiality SPOF Violation`. Redistribute across CH, SG, IS, US and JP and verify a compliant report with `max_shards_in_single_country == 1`.
- Instantiate `ColdStorageGeographicDistributor(threshold_m=4, total_shards_n=6)` with 3 shards in the US and one each in CH, SG, JP. Verify it is non-compliant with a `Country Availability SPOF Violation` and no confidentiality violation.
- Run `python -m unittest discover -s skills/cold-storage-geographic-distribution-strategy/scripts` (16 tests).

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `air-gapped-signing-workflow-for-cold-storage`
- `regulatory-custody-requirements-by-jurisdiction`

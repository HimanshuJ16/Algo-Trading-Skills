---
name: post-incident-forensics-for-suspected-key-compromise
description: >-
  Post-incident digital forensics engine analyzing KMS access logs, IP whitelist violations, and on-chain transaction traces following a suspected cryptographic key compromise.
domain: Crypto Custody & Security
subdomain: Digital Asset Security & Forensics
tags: ["key-compromise", "crypto-custody", "digital-forensics", "on-chain-analysis", "ip-whitelist", "incident-response", "chain-of-custody"]
brokers_frameworks: ["NIST SP 800-86 Digital Forensics", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when responding to a suspected private key leakage, unauthorized API signing event, or hot wallet exfiltration. Crypto assets are irreversible; immediate containment requires rapid off-chain access log analysis (detecting unauthorized IP addresses or KMS calls) coupled with on-chain transaction tracing. This engine isolates unauthorized access patterns, quantifies exfiltrated assets, generates evidence integrity hashes (SHA-256) for chain of custody, and mandates emergency key rotation protocols.

## Prerequisites

- Key incident metadata (`key_id`, `wallet_address`, `suspected_leak_time`, `affected_systems`).
- KMS / API gateway access logs (`timestamp`, `ip_address`, `action`, `status_code`, `authorized_ips`).
- On-chain transaction records (`tx_hash`, `from_address`, `to_address`, `amount_crypto`, `timestamp`).

## Workflow

1. **Access Log & IP Whitelist Audit**:
   - Filter access logs for requests originating from non-whitelisted IP addresses.
2. **On-Chain Transaction Tracing**:
   - Trace unauthorized outflows from the compromised wallet address to recipient addresses.
3. **Evidence Hash & Chain of Custody Preservation**:
   - Compute SHA-256 evidence integrity hashes over raw forensic log payloads.
4. **Containment Protocol Dispatch**:
   - Mandate immediate key revocation, CEX address blacklisting, and MPC/HSM key rotation.
5. **Audit Report Generation**: Output structured `KeyForensicsReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Preserve Chain of Custody**: Modifying raw log files during analysis, rendering evidence inadmissible for insurance claims or legal enforcement.
- **Delaying Key Revocation**: Investigating the breach before revoking API keys or pausing smart contract signing.
- **Single-Key Exposure Radius**: Assuming a breach affects only one wallet when derivative keys share a master seed or HSM module.

## Verification

- Instantiate `KeyForensicsAnalyzer`. Ingest incident `KEY-2026-X` with 1 unauthorized IP access (`198.51.100.44`) and 1 unauthorized $50$ ETH outflow $\implies$ verify `UNAUTHORIZED_ACCESS_CONFIRMED` status, total exfiltrated funds, evidence hash generation, and emergency key rotation mandate.
- Run `python scripts/test_post_incident_forensics_for_suspected_key_compromise.py`.

## Related Skills

- `recovery-plan-for-lost-or-compromised-keys`
- `on-chain-transaction-monitoring-for-anomalies`
---

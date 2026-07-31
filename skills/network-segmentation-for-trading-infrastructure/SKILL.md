---
name: network-segmentation-for-trading-infrastructure
description: >-
  Network segmentation auditor enforcing Zero-Trust security zone isolation, detecting illegal cross-zone traffic flows (Public/Dev to Execution/Custody), and auditing firewall rules.
domain: Cybersecurity & Infrastructure
subdomain: Zero-Trust Network Segmentation & Firewall Auditing
tags: ["network-segmentation", "zero-trust", "firewall-auditing", "trading-infrastructure", "vlan-isolation", "key-custody", "execution-zone"]
brokers_frameworks: ["Zero-Trust Architecture (NIST 800-207)", "AWS VPC Security Groups", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing or auditing cloud (AWS VPC / GCP VPC) or co-location network topology for high-frequency and automated trading operations. Without strict network micro-segmentation, a security breach in a developer jump host or public web frontend can allow attackers to move laterally into trading execution gateways or key custody vaults. This auditor enforces Zero-Trust security zone tiering (`PUBLIC_DMZ`, `TRADING_EXECUTION`, `STRATEGY_ENGINE`, `KEY_CUSTODY`, `DEV_MANAGEMENT`), detecting unauthorized cross-zone firewall ALLOW rules and exposed admin ports.

## Prerequisites

- Subnet tier definitions (`subnet_id`, `zone_tier`: `'PUBLIC_DMZ'`, `'TRADING_EXECUTION'`, `'KEY_CUSTODY'`, `'DEV_MANAGEMENT'`).
- Firewall & security group rules (`rule_id`, `source_subnet_id`, `destination_subnet_id`, `protocol`, `port`, `action`: `'ALLOW'`, `'DENY'`).

## Workflow

1. **Subnet Zone Tier Classification**:
   - Register subnets into security tiers (`PUBLIC_DMZ`, `TRADING_EXECUTION`, `STRATEGY_ENGINE`, `KEY_CUSTODY`, `DEV_MANAGEMENT`).
2. **Zero-Trust Cross-Zone Flow Audit**:
   - Evaluate all `ALLOW` firewall rules for critical security violations:
     - **Violation 1**: Direct ingress from `PUBLIC_DMZ` or `DEV_MANAGEMENT` to `TRADING_EXECUTION` or `KEY_CUSTODY`.
     - **Violation 2**: Administrative ports (SSH 22, RDP 3389) exposed to `PUBLIC_DMZ`.
     - **Violation 3**: Unauthorized non-whitelisted traffic entering `KEY_CUSTODY` subnets.
3. **Compliance Assessment**:
   - Flag violations, mark compliance status (`COMPLIANT` vs `NON_COMPLIANT_SECURITY_VIOLATION`), and list illegal rules.
4. **Audit Report Generation**: Output structured `NetworkSegmentationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Flat Network Topologies**: Placing execution gateways, strategy bots, and developer SSH jump hosts on the same flat subnet.
- **Overly Permissive Ingress**: Adding wildcard `0.0.0.0/0` ALLOW rules on security groups for troubleshooting and forgetting to remove them.
- **Shared Management Networks**: Allowing corporate office VPNs direct access to broker FIX execution endpoints without a intermediate bastion/proxy.

## Verification

- Instantiate `NetworkSegmentationAuditorEngine`. Audit compliant multi-tier VPC $\implies$ verify `COMPLIANT`. Add illegal ALLOW rule from `PUBLIC_DMZ` to `KEY_CUSTODY` port 22 $\implies$ verify `NON_COMPLIANT_SECURITY_VIOLATION` detection and alert.
- Run `python scripts/test_network_segmentation_auditor.py`.

## Related Skills

- `multi-party-computation-mpc-custody-solutions`
- `log-aggregation-and-centralized-observability`
---

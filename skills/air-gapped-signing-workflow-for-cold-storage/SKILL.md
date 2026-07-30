---
name: air-gapped-signing-workflow-for-cold-storage
description: Simulates and enforces a strict air-gapped signing workflow (Coordinator
  -> QR/SD -> Offline Vault -> Sign -> Broadcast) for institutional crypto custody.
domain: crypto-custody-security
subdomain: Key Management
tags:
- crypto
- custody
- security
- air-gap
- psbt
brokers_frameworks:
- Generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when designing the architecture for institutional crypto treasury management or securing API withdrawal whitelists. To prevent catastrophic private key theft, high-value signing must never occur on internet-connected machines. This workflow enforces the physical separation of the "Coordinator" (online) and the "Signer" (offline vault), ensuring clear signing without network exposure.

## Prerequisites

- Python 3.9+
- Understanding of Partially Signed Bitcoin Transactions (PSBT) or equivalent unsigned payload structures.

## Workflow

1. **Coordinator (Online)**: Algorithm generates an unsigned transaction intent (destination, amount).
2. **Export to Air-Gap**: The unsigned payload is encoded (conceptually via QR code or SD card) to cross the physical air gap.
3. **Signer (Offline)**: The offline vault decodes the payload, performs **Clear Signing** verification (displaying exact intent to hardware screen), and signs with the isolated private key.
4. **Import & Broadcast**: The signed payload is returned across the air gap to the Coordinator for blockchain broadcast.

## Common Pitfalls

- **Blind Signing**: The offline device simply signs the payload hash without decoding and verifying the destination address and amount, making it vulnerable to malware on the online coordinator.
- **Network Bridging**: Using USB cables, Bluetooth, or WiFi to transfer the payload to the "offline" device, completely defeating the air gap.

## Verification

Run `python scripts/test_air_gapped_signing_workflow_for_cold_storage.py` to ensure the strict segregation of online and offline environments and the failure of blind/tampered signatures.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `multi-signature-approval-for-large-transfers`

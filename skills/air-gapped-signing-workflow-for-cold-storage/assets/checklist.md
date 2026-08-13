# Checklist for Air-Gapped Signing Workflow

## Payload and Code Controls

- [ ] Private key is isolated inside `OfflineAirGappedSigner`; no network adapters or network APIs exist on the signer.
- [ ] `OnlineCoordinator` creates intents and verifies returned envelopes but never stores the vault private key.
- [ ] Payloads are versioned, schema-validated, canonically serialized, and hashed deterministically.
- [ ] Amounts use decimal-safe or chain base-unit representation with explicit precision limits.
- [ ] Address, network, nonce, and version validation is strict and fail-closed.
- [ ] Signed envelopes bind the exact unsigned payload, payload hash, and signer key identifier.
- [ ] The coordinator independently verifies signatures and does not trust caller-controlled authentication flags.
- [ ] Unknown intents, payload mismatches, forged signatures, replays, and duplicate broadcasts are rejected.
- [ ] Coordinator-issued and consumed payload state is durable and restart-safe in production.
- [ ] Production cryptography uses audited chain-native implementations and hardware-wallet/HSM isolation; the reference signature seam is not used for funds.

## Operational Controls

- [ ] QR/SD media is inspected, malware-scanned where appropriate, and tracked through chain of custody.
- [ ] USB, Bluetooth, Wi-Fi, and cellular bridges are prohibited.
- [ ] Intent creation, media handling, offline approval, and broadcast reconciliation have separation of duties.
- [ ] Clear signing displays exact destination, amount, network, and nonce before approval.
- [ ] Ambiguous broadcast outcomes have a reconciliation runbook and no unbounded retries.
- [ ] Key/device compromise, malformed media, and mismatched payloads have incident escalation paths.
- [ ] Behavioral tests cover success, malformed input, boundary values, tampering, replay, and fail-closed behavior.

## Sign-off

- Security Architect: ___________________________
- Date: ___________________________

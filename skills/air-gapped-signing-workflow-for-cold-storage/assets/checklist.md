# Checklist for Air-Gapped Signing Workflow

- [ ] Private key is completely isolated inside `OfflineAirGappedSigner`.
- [ ] `OnlineCoordinator` only handles the generation of `UnsignedPayload` and network broadcasting.
- [ ] Data transfer relies strictly on serialization (`to_qr_code_data()`) simulating physical medium.
- [ ] Vault implements `_verify_clear_signing()` to prevent malicious payload blind-signing.
- [ ] Tests pass verifying successful transfers and rejection of malicious addresses.

## Sign-off
- Security Architect: ___________________________
- Date: ___________________________
# Institutional Custody Vendor Lock-In Operations Checklist

Each box is signed off against a document you have read or a drill you have run,
never against a vendor assertion.

## Key Format & Portability Audit
- [ ] **Secret-Bearing Export Confirmed**: The custodian contractually exports at least one format that carries a secret (`BIP39_MNEMONIC`, `SLIP39_SHAMIR`, `WIF_PRIVATE_KEY`, or a reconstructable MPC share). A `BIP32_HD_PATH` export is metadata and does not satisfy this box.
- [ ] **Derivation Metadata Obtained in Writing**: Full derivation path and script type per network, confirmed by deriving a known funded address from the backup you hold. A seed without the map is not a recovery package.
- [ ] **Passphrase Status Documented**: Whether a BIP-39 passphrase is in use, and where it is held — separately from the mnemonic, and recoverable by the institution rather than by one individual.
- [ ] **SLIP-0039 Wallet Support Verified**: If shares are SLIP-0039, a named wallet or tool that implements SLIP-0039 has been identified and tested. SLIP-0039 is explicitly not BIP-39 compatible.
- [ ] **WIF Address Enumeration Complete**: If export is WIF, the export covers every derived address in use, and the enumeration method is documented.
- [ ] **Offline Recovery Tool Verified, Not Assumed**: For proprietary MPC shares, the vendor's offline reconstruction tool has been located, its licence and source reviewed, and it has been executed successfully on an air-gapped machine. Record the tool version and date — vendor tooling changes.
- [ ] **Non-Exportable Material Flagged**: Any enclave-bound HSM blob is recorded as zero-portability, and the assets it controls are quantified.

## Disaster Recovery & Migration Preparation
- [ ] **Offline Drill Executed, Not Simulated**: `simulate_disaster_recovery_drill(provider, is_vendor_responsive=False)` returns success **and** a real offline reconstruction has been performed with the vendor uninvolved. A vendor-responsive drill is explicitly not evidence of self-sovereignty.
- [ ] **Vendor-Service Dependency Assessed**: `requires_vendor_active_api_for_exit` is set from the custody agreement. If True, key material held today is not sufficient to recover tomorrow, and cold extraction is a pre-onboarding condition.
- [ ] **Drill Cadence Agreed and Diarised**: A fixed re-drill interval, plus an event-driven re-drill after any vendor recovery-tooling change, key rotation, or new network onboarding.
- [ ] **On-Chain Migration Cost Estimated**: Exit cost computed with the model's assumption (one sweep per wallet per network) understood, and expensive chains modelled separately.
- [ ] **SLA Exit Clause Review**: Contractual maximum export response time, zero proprietary exit penalties, and a written commitment to the export formats relied on above.

## Multi-Custodian Redundancy
- [ ] **Dual-Custodian Architecture**: An active integration with a secondary custodian sufficient to absorb the estate, tested with a real transfer.
- [ ] **Self-Sovereign Cold Backup**: Offline BIP-39/SLIP-0039 backup shares stored in institutional air-gapped vaults, with the derivation metadata stored alongside them.
- [ ] **Governance Sign-Off**: Every `risk_factor` in the assessment output has either been remediated or explicitly accepted in writing by the risk committee — a `LOW` score with an unread risk factor is not a sign-off.

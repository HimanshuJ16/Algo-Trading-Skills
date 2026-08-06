# Institutional Custody Vendor Lock-In Operations Checklist

## Key Format & Portability Audit
- [ ] **Open Standard Key Format Audit**: Verify custodian supports export in open standards (`BIP39_MNEMONIC`, `SLIP39_SHAMIR`, `BIP32_HD_PATH`, `WIF_PRIVATE_KEY`).
- [ ] **Open-Source Recovery Tool Verification**: Confirm availability of offline open-source tools to reconstruct private keys without vendor binaries.
- [ ] **Proprietary Share Isolation**: Identify and flag closed MPC/HSM key share encodings that create single-vendor lock-in.

## Disaster Recovery & Migration Preparation
- [ ] **Quarterly Offline Key Recovery Drill**: Execute `simulate_disaster_recovery_drill()` simulating vendor offline/insolvency scenarios.
- [ ] **On-Chain Migration Cost Estimation**: Calculate total exit costs factoring in vendor export fees and multi-chain gas costs.
- [ ] **SLA Exit Clause Review**: Require contractual commitments for maximum export response SLA ($\le 7\ \text{days}$) and zero proprietary exit penalties.

## Multi-Custodian Redundancy
- [ ] **Dual-Custodian Architecture**: Maintain active integration with a secondary custodian to enable rapid asset rebalancing.
- [ ] **Self-Sovereign Cold Backup**: Store offline BIP-39/SLIP-0039 backup shares in institutional air-gapped vaults.
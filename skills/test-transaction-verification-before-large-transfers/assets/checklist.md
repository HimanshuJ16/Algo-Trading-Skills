# Institutional Large Crypto Transfer Verification Checklist

## Pre-Flight Whitelist & Policy Audit
- [ ] **Address Whitelist**: Verify recipient address is active on HSM/MPC whitelist directory.
- [ ] **Checksum & Format**: Confirm address string passes EIP-55 (ETH) / Base58Check (BTC) checksum validation.
- [ ] **Destination Tag / Memo**: Confirm memo/destination tag is provided for XRP, XLM, TON, BNB, or EOS.
- [ ] **Threshold Trigger Calibration**: Verify large transfer threshold parameter (`large_transfer_threshold_usd`, default $50,000).

## Dust Test Transaction Operations
- [ ] **Dust Transfer Generation**: Broadcast minimal non-zero dust transfer (e.g. 0.001 ETH / 1.0 USDT) to recipient.
- [ ] **On-Chain Confirmation Depth**: Verify test transaction achieves minimum required block confirmations (12 blocks ETH, 2 blocks BTC).
- [ ] **Recipient Receipt Acknowledgement**: Confirm test transfer receipt on target custodian dashboard or RPC balance probe.

## Time Window & Authorization Finality
- [ ] **Expiry Window Verification**: Confirm primary transfer submission occurs within 30 minutes of test transaction confirmation.
- [ ] **Audit Trail Serialization**: Log immutable audit hash containing request ID, test tx hash, block depth, and approval timestamp.
- [ ] **Multi-Signature Release**: Submit approved authorization payload to MPC/custody vault (Fireblocks / Safe / BitGo) for multi-sig signing.
---
name: test-transaction-verification-before-large-transfers
description: "Institutional crypto custody & treasury verification engine requiring mandatory dust test transactions, address whitelisting, on-chain confirmation depth verification, and time-decay security windows prior to releasing high-value transfers."
domain: Crypto Custody
subdomain: Security
tags:
- crypto
- custody
- treasury
- security
- test-transaction
- dust-verification
- mpc-vault
- multi-sig
brokers_frameworks:
- fireblocks
- bitgo
- coinbase-custody
- safe-multisig
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when executing large cryptocurrency transfers, treasury rebalancing, cold storage movements, exchange deposits, or multi-signature disbursements exceeding specified monetary thresholds (e.g. > $50,000 USD or > 10 BTC / 100 ETH).

This skill enforces mandatory pre-flight dust test transactions to eliminate address typos, clipboard poisoning attacks, missing destination tags/memos, network parameter mismatches, and key rotation errors before committing primary capital.

## Prerequisites

- Python 3.9+
- Read-only RPC connection to target blockchain networks (Ethereum, Bitcoin, Solana, Ripple, etc.) or Custody API integration (Fireblocks, BitGo, Coinbase Custody).
- Approved recipient whitelist directory maintained in hardware security modules (HSM) or secure key vaults.

## Workflow

1. **Configure Verification Parameters**: Instantiate `VerificationConfig` with monetary threshold (`large_transfer_threshold_usd`), maximum authorization window (`test_expiry_window_minutes`), and whitelisting requirements.
2. **Register Asset Specifications**: Set up `AssetConfig` per asset (specifying decimal precision, minimum block confirmation depth e.g. 12 blocks for Ethereum, test dust amount e.g. 0.001 ETH, and destination tag rules).
3. **Initiate Transfer Request**: Submit `TransferRequest` to `initiate_transfer_request()`. The engine checks whitelist status, destination tags, and monetary threshold.
   - If below threshold: Returns `NOT_REQUIRED` and authorizes direct execution.
   - If above threshold: Returns `TEST_PENDING` and mandates a test transaction.
4. **Broadcast Test Transaction**: Transmit specified dust amount to the recipient address and log the transaction hash via `record_test_transaction()`.
5. **On-Chain Confirmation Depth Tracking**: Monitor block confirmations and update state via `update_test_confirmations()`. The engine flags the test transaction as `TEST_CONFIRMED` once minimum required confirmations are reached.
6. **Time-Window Authorization Gate**: Invoke `verify_and_authorize_large_transfer()`. The engine validates that the test transaction is confirmed **and** was completed within the active expiry window before issuing final approval for the primary transfer payload.

## Common Pitfalls

- **Bypassing Test Transfers for "Known" Addresses**: Assuming a previously used address is safe ignores clipboard malware, DNS spoofing, or custodian address deprecations. Always enforce test transactions on large amounts.
- **Omitted Destination Memos/Tags**: Tokens like XRP, XLM, BNB, TON, and EOS require destination tags to route deposits correctly. Omitting tags results in uncredited custodian deposits.
- **Premature Primary Transfer Authorization**: Executing the primary transfer before block finality (e.g. during a chain re-org) can invalidate the test transaction outcome.
- **Expired Verification Windows**: Waiting too long between test verification and primary transfer increases exposure to recipient key rotation or address recycling. Re-verify if window expires.

## Verification

Run the test suite to validate threshold checks, confirmation depth requirements, tag enforcement, and expiry limits:

```bash
python -m unittest discover -s skills/test-transaction-verification-before-large-transfers/scripts
```

## Related Skills

- `air-gapped-signing-workflow-for-cold-storage`
- `crypto-wallet-key-custody-security`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `multi-signature-approval-for-large-transfers`


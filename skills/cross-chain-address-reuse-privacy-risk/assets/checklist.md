# Pre-Flight Checklist

- [ ] Are wallet addresses audited across all active EVM/UTXO chains for address reuse?
- [ ] Are clusters linked by public key (not just identical address strings) so cross-format reuse (e.g. Bitcoin ↔ EVM) is caught?
- [ ] Are KYC exchange deposit linkages identified and propagated to the entire linked cluster?
- [ ] Is the Address Reuse Privacy Risk Score computed prior to deploying bot strategies?
- [ ] Are unregistered addresses reported as `NOT_TRACKED` (unknown) rather than low risk?
- [ ] Are unrevealed public keys recorded as `None` rather than a placeholder string (which would fuse unrelated wallets into one cluster and spread KYC contamination)?
- [ ] Are `chain_id` labels consistent, so casing/whitespace variants cannot manufacture a false reuse count?
- [ ] Is a `LOW` verdict read together with `remediation_actions`, given that a real 2-chain reuse scores only 20?
- [ ] Are base58 addresses (Bitcoin legacy, Solana) compared case-sensitively and `0x` hex case-insensitively?
- [ ] Is HD Wallet BIP-44 path separation enforced (distinct `coin_type` across chain families, distinct `account'` indexes within the EVM family)?

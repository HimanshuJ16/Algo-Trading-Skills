# Workflows for Cross-Chain Address Privacy Audit

1. **Address Cluster Mining**:
   - Normalise `chain_id` for grouping (strip + case-fold): `Ethereum`, `ethereum` and `" Ethereum "` are one chain, not three.
   - Link records that share an identical address (case-insensitive ONLY for `0x` hex per EIP-55; base58 compared case-sensitively) or an identical public key (links different address formats, e.g. Bitcoin ↔ EVM sharing one secp256k1 key).
   - A public key of `None` means "not yet revealed on-chain" (an unspent Bitcoin P2PKH output) and forms NO linkage edge. Never substitute a placeholder string — every record carrying it would join one fictitious cluster and share its KYC contamination.
   - Clusters are transitive (connected components): a chain A→B→C of linkages joins all three records even if A and C share nothing directly.
2. **KYC Exposure Audit**:
   - Check if any node in the address cluster interacts with centralized exchange KYC hot wallets; a single KYC linkage contaminates the entire cluster.
3. **Risk Scoring**:
   - $\text{Reuse Weight} = 0$ if $K = 1$ (one chain is no reuse), else $\min\left(50, \frac{K_{\text{chains}}}{M_{\text{total}}} \times 50\right)$.
   - $\text{Score} = \min\left(100, \text{Reuse Weight} + \text{KYC\_Penalty}\right)$ where KYC\_Penalty $= 50$ if the cluster is KYC-linked.
   - An address absent from the registry returns `NOT_TRACKED` (status unknown) — never interpret it as low risk.
   - A `LOW` level is not "no findings": a 2-chain reuse with no KYC scores 20 and stays below the alert thresholds. Read the remediation actions, which never end on a clean bill of health when a linkage was found.
4. **Remediation**:
   - `coin_type` isolates chain families (SLIP-44: Bitcoin $0'$, EVM $60'$, Solana $501'$); because an EVM key pair yields the identical `0x` address on every EVM network, EVM-to-EVM isolation requires distinct `account'` indexes or separate seeds ($m/44'/coin\_type'/account'/0/index$).

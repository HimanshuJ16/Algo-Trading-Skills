# Workflows for Exchange Proof of Reserves Verification

1. **Merkle Leaf Hash Computation**:
   - Compute account balance SHA-256 leaf hash.
2. **Merkle Path Traversal**:
   - Verify sibling hashes up to the declared Merkle Root.
3. **On-Chain Reserve Audit**:
   - Compare on-chain wallet balances against total Merkle liabilities.
4. **Solvency Classification**:
   - Classify exchange solvency state (Solvent vs Deficit).
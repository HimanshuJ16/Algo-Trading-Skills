# Workflows for Cross-Chain Address Privacy Audit

1. **Address Cluster Mining**:
   - Group addresses by public key or identical hex string across chains.
2. **KYC Exposure Audit**:
   - Check if any node in the address cluster interacts with centralized exchange KYC hot wallets.
3. **Risk Scoring**:
   - $\text{Score} = \min\left(100, \frac{N_{\text{chains}}}{N_{\text{total}}} \times 50 + \text{KYC\_Penalty}\right)$.
4. **Remediation**:
   - Enforce HD derivation path separation ($m/44'/coin\_type'/account'/0/index$).
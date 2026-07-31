# Workflows for MPC Custody Solutions

1. **Quorum Verification**:
   - Verify submitted partial key shares meet threshold requirement $t$-of-$N$.
2. **Threshold Signature Computation**:
   - Execute multi-round threshold protocol (CMP/GG18) without assembling private key.
3. **Audit & Proactive Refresh**:
   - Audit signing logs and trigger periodic key share refresh cycles.
4. **Audit Report Generation**:
   - Output structured MPC signing report.
# Workflows for Hot Wallet Key Rotation

1. **Key Metadata Audit**:
   - Audit key age (max 90 days), signature count (max 100k), and USD signed volume.
2. **Rotation Trigger Decision**:
   - Trigger rotation if age/usage limits exceeded or compromise suspected.
3. **Dual-Key Grace Period Management**:
   - Issue Key $N+1$ and place Key $N$ into a 24-hour deprecated grace period.
4. **Key Shredding & Reporting**:
   - Zeroize and revoke old key after grace period, outputting audit report.
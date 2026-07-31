# Workflows for Recovery Plan for Lost or Compromised Keys

1. **Backup Method Validation**:
   - Verify backup method is recognized (Shamir SSS, HSM Seed, Mnemonic Phrase).
2. **Shamir Shard Integrity Check**:
   - Verify verified shards $\ge$ threshold + surplus.
3. **Sweep Wallet & Drill Recency Check**:
   - Verify emergency sweep wallet configured; verify last drill within 90 days.
4. **Audit Report Generation**:
   - Output structured key recovery readiness report.
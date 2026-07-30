# Workflows for Database Backup and Point-In-Time Restore Testing

1. **Snapshot & WAL Archiving**:
   - Maintain continuous WAL streaming + base snapshots.
2. **PITR Simulation**:
   - Load base snapshot and replay WAL logs up to $T_{\text{target}}$.
3. **RPO & RTO Audit**:
   - Verify $\text{RPO} \le 60\text{ seconds}$ and $\text{RTO} \le 15\text{ minutes}$.
4. **Data Verification**:
   - Audit restored table checksums and record counts.
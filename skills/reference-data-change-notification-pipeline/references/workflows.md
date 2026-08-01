# Workflows for Reference Data Change Notification Pipeline

1. **Snapshot Comparison**:
   - Compare before/after instrument snapshots field by field.
2. **Change Detection & Severity Classification**:
   - Identify added, modified, removed fields; classify as CRITICAL or INFO.
3. **Notification Generation**:
   - Generate structured change notifications with old/new values.
4. **Audit Report**:
   - Output structured reference data change report.

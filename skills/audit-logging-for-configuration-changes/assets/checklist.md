# Checklist for Audit Logging

- [ ] Confirm the logger captures high-precision UTC timestamps.
- [ ] Confirm changes are blocked if the user provides an empty or negligible justification.
- [ ] Confirm the resulting audit record serializes cleanly to JSON for downstream SIEM ingestion.
- [ ] Run test suite: `python scripts/test_config_change_audit_logger.py`.

## Sign-off
- Deployment / SecOps Engineer: ___________________________
- Date: ___________________________
# Checklist for Audit Logging

- [ ] Confirm the logger captures high-precision UTC timestamps.
- [ ] Confirm changes are blocked if the user provides an empty, whitespace-only, or sub-`MIN_JUSTIFICATION_LENGTH` justification.
- [ ] Confirm changes are blocked if `user_id` is empty or whitespace-only.
- [ ] Confirm no-op changes (identical `old_value`/`new_value`) are recorded but not approved.
- [ ] Confirm rejected attempts are also emitted (WARNING) and retained, not just approved changes.
- [ ] Confirm each record carries a monotonic `sequence_number` and a SHA-256 `record_hash` chained to the prior record's `prev_hash`.
- [ ] Confirm recomputing `record_hash` from a serialized record matches the stored hash (tamper detection).
- [ ] Confirm JSON serialization is canonical (sorted keys) and does not raise on non-serializable config values (`default=str`).
- [ ] Confirm the originating `environment` is captured on every record.
- [ ] Confirm the downstream sink is WORM-compliant (e.g., S3 Object Lock) — JSON format alone is not immutability.
- [ ] Run test suite: `python -m unittest discover -s skills/audit-logging-for-configuration-changes/scripts`.

## Sign-off
- Deployment / SecOps Engineer: ___________________________
- Date: ___________________________

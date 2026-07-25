# Deep Workflow Reference — structured-logging-for-post-incident-forensics

## Full Procedure

1. Define standardized event schema with required fields.
2. Assign correlation IDs to link related events across order lifecycle.
3. Use monotonic sequence numbers for guaranteed ordering.
4. Query by correlation_id to reconstruct incident timelines.

## Production Implementation Reference

- Code: `scripts/structured_logger.py` (`ForensicLogger`, `EventType`).
- Tests: `scripts/test_structured_logger.py`.

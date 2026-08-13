---
name: audit-logging-for-configuration-changes
description: Immutable audit logging engine enforcing SEC Reg SCI and FINRA Rule 3110
  compliance for algorithmic trading configuration changes.
domain: deployment-ops
subdomain: regulatory
tags:
- compliance
- audit-logging
- sec-reg-sci
- finra-3110
- risk-controls
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill to track modifications to live trading algorithms, risk parameters, and system configurations. FINRA Rule 3110 (Supervision) and Regulatory Notice 15-09 require a documented change-management process for algorithmic trading code and risk-control parameter settings, including records of approvals and reasoning. SEC Rule 17a-4 imposes WORM-style retention for broker-dealer records. SEC Regulation SCI applies *directly* only to "SCI entities" (SROs, certain ATSs, plan processors, certain exempt clearing agencies); a 2023 SEC proposal would extend it to certain large broker-dealers. Firms not mandated by SCI nonetheless commonly adopt SCI-style controls voluntarily.

This engine intercepts configuration updates, validates that a justification and authenticated principal are present, and formats the change into a compliance-ready, tamper-evident JSON audit log. Use it for both approved changes and rejected attempts (a failed change attempt is itself a supervisory event).

## When NOT to Use

- For routine application logs that are not configuration changes (use `structured-logging-for-post-incident-forensics` instead).
- For order/fill audit trails (use `data-lineage-tracking-for-audit-and-debugging` or venue CAT reporting tooling).
- As a substitute for the downstream WORM/SIEM storage layer — this engine produces records; immutability is enforced by the sink.

## Prerequisites

- Python 3.9+
- A centralized, append-only log storage system (e.g., Splunk, Datadog, or an AWS S3 Object Lock WORM bucket) to receive the generated log lines.
- An authenticated caller context so `user_id` is always populated (e.g., SSO/JWT).

## Workflow

1. **Intercept Update**: A trader or automated system attempts to modify a configuration (e.g., increasing `max_order_size` from 100 to 500). Every UI, CLI, or API path that mutates a config parameter must route through `ConfigurationAuditLogger.process_change_request`.
2. **Validation**: The logger verifies that:
   - `justification` is present and at least `MIN_JUSTIFICATION_LENGTH` characters after stripping whitespace.
   - `user_id` is a non-empty, authenticated principal.
   - `old_value` and `new_value` are actually different (no-op changes are recorded but not approved).
3. **Record Construction**: The engine builds a `ConfigChangeRecord` with a high-precision UTC timestamp, the originating `environment`, a monotonically increasing `sequence_number`, and a SHA-256 `record_hash` chained to the previous record's hash via `prev_hash`.
4. **Log Emission**: Approved records are emitted at INFO as `AUDIT_LOG_ENTRY`; rejected attempts are emitted at WARNING as `AUDIT_LOG_REJECTED`. Both serialize to canonical (sorted-key) JSON for deterministic SIEM ingestion.
5. **Integrity Verification**: On examination, recompute each `record_hash` from the serialized fields and walk the `prev_hash` chain; the first record whose recomputed hash differs, or whose `prev_hash` does not match the prior `record_hash`, marks the boundary of tampering or a gap.

## Common Pitfalls

- **Silent Failures**: Allowing configurations to be updated via a database or flat file directly without passing through an application-layer audit logger, leaving the firm blind during an examination.
- **Missing Justifications**: Logging that a risk limit was changed without recording *why* or *who authorized it*.
- **Missing Principals**: Accepting a change request without an authenticated `user_id`, producing a record that cannot support supervisory attribution.
- **Dropping Rejected Attempts**: Logging only approved changes; a rejected attempt (e.g., missing justification) is itself auditable evidence of attempted unauthorized change.
- **Non-Serializable Values**: Config values that are not JSON-serializable (sets, datetimes, custom objects) must not silently drop the audit record — `to_json` uses `default=str` to guarantee emission.
- **Assuming JSON = Immutable**: JSON is a format, not a tamper control. True immutability comes from the WORM/SIEM sink; the hash chain provides *detection* of tampering, not prevention.

## Verification

Run `python scripts/test_config_change_audit_logger.py` (or `python -m unittest discover -s skills/audit-logging-for-configuration-changes/scripts`) to confirm that the engine: rejects un-justified / missing-principal / no-op changes; emits monotonic sequence numbers; chains `prev_hash` to the prior `record_hash`; produces deterministic sorted-key JSON; and survives non-serializable config values.

## Related Skills

- `audit-logging-for-configuration-changes`
- `risk-control-configuration-change-approval-workflow`
- `risk-control-bypass-audit-logging`
- `configuration-drift-detection-across-environments`
- `record-retention-periods-by-jurisdiction`

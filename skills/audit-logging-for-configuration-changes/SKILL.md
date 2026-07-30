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
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill to track modifications to live trading algorithms, risk parameters, and system configurations. Under SEC Regulation SCI and FINRA Rule 3110 (Supervision), institutional broker-dealers and quantitative funds must maintain strict, reconstructible audit trails of *who* changed *what* parameter, *when*, and *why*. 

This engine intercepts configuration updates, validates that a justification is provided, and formats the change into a compliance-ready, immutable JSON audit log.

## Prerequisites

- Python 3.9+
- A centralized, append-only log storage system (e.g., Splunk, Datadog, or an AWS S3 WORM bucket) to receive the generated log lines.

## Workflow

1. **Intercept Update**: A trader or automated system attempts to modify a configuration (e.g., increasing `max_order_size` from 100 to 500).
2. **Validation**: The `ConfigurationAuditLogger` intercepts the request. It verifies that:
   - The `old_value` and `new_value` are actually different.
   - The user has provided a mandatory `justification` for the change.
3. **Immutability Formatting**: The engine generates a structured `ConfigChangeRecord` with a high-precision UTC timestamp.
4. **Log Emission**: The engine serializes the record to a JSON string designed for ingestion by a SIEM (Security Information and Event Management) system.

## Common Pitfalls

- **Silent Failures**: Allowing configurations to be updated via a database or flat file directly without passing through an application-layer audit logger, leaving the firm blind during an SEC examination.
- **Missing Justifications**: Logging that a risk limit was bypassed without logging *why* or *who authorized it*.

## Verification

Run `python scripts/test_config_change_audit_logger.py` to confirm that the engine successfully rejects un-justified changes and properly formats compliance records.

## Related Skills

- `audit-logging-for-configuration-changes`
- `risk-control-configuration-change-approval-workflow`

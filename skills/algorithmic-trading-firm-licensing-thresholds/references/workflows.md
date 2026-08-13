# Workflows for Algorithmic Trading Firm Licensing

## Compliance Auditing Pipeline

1. **Daily Metric Aggregation**: Aggregate the firm's total off-exchange volume, peak 1-second order rates, and customer-trade flags over the documented rolling window from the data warehouse. The aggregation layer is responsible for ensuring values are finite, non-negative, and well-typed before passing them to `FirmTradingActivity`.
2. **Threshold Configuration**: Construct a `LicensingThresholdEvaluator` with the firm's threshold policy. Use class defaults first, then override with stricter per-firm values (`sebi_retail_ops_limit=`, `mifid_ii_hft_ops_limit=`, `sec_off_exchange_floor_usd=`) only when documented policy diverges.
3. **Threshold Evaluation**: Pass the aggregated `FirmTradingActivity` into `evaluator.evaluate()`. The result is a frozen `LicensingComplianceReport` carrying `reason`, `violations`, `rule_id`, `evaluated_at` (UTC), and `schema_version`.
4. **Alerting**: If `requires_registration` is `True`, route an immediate critical alert to the Chief Compliance Officer (CCO) and General Counsel. The alert payload should include the report's `violations`, `rule_id`, and `evaluated_at` so reviewers can quickly identify the firing rules.
5. **Remediation**: Trading desks must immediately throttle order rates, disable off-exchange routing, or stop trading entirely until formal regulatory licensing is secured. Customer-trading desks must not resume until counsel has confirmed all `violations` are remediated.
6. **Audit and Retention**: Persist the `LicensingComplianceReport` in the firm's compliance log with the underlying `FirmTradingActivity`. Reports are authoritative inputs for regulatory examinations; do not overwrite historic reports.

## Failure and Recovery Boundaries

- **Dataclass validation failure** (NaN/inf volume, negative OPS, unsupported jurisdiction): Quarantine the upstream aggregation job. Do not run the evaluator until the data source produces well-formed metrics. Investigation is required before re-running.
- **Multiple simultaneous violations**: Treat each violation as a distinct issue. Remediation of one trigger (e.g., throttling OPS) does not waive others (e.g., off-exchange routing). Re-run the evaluator after each remediation.
- **Threshold drift**: When policy changes (e.g., SEBI updates the retail OPS limit), update the relevant constructor override and re-run all reports. The `evaluated_at` and `schema_version` on prior reports make backfill deterministic.
- **Ambiguous jurisdiction**: A firm operating across multiple jurisdictions must run one evaluation per jurisdiction. Each jurisdiction's report should be retained independently. "Primary" jurisdiction claims must be confirmed with legal counsel.
- **Customer-funds flag flicker**: Customer-trade detection is a high-severity indicator. Any single `True` evaluation should escalate, even if downstream metrics appear compliant, because customer-funds violations require legal evidence handling.
- **Unrecognized jurisdiction**: Fail closed — return `requires_registration = True` with reason `"Unrecognized jurisdiction '<…>' requires manual legal review."` and route to legal counsel. Do not silently treat as compliant.
- **Log injection in upstream data**: Treat all upstream-captured strings (jurisdiction labels, reason text appended downstream) as untrusted. Use `report` fields with `%s` placeholders for structured logging and never `f-string`-interpolate raw fields into log lines.

---
name: algorithmic-trading-firm-licensing-thresholds
description: Evaluates proprietary trading activity against global regulatory thresholds
  (SEC Rule 15b9-1, MiFID II, SEBI) to trigger mandatory firm licensing and registration
  alerts.
domain: regulatory-compliance
subdomain: legal-and-registration
tags:
- compliance
- sec
- finra
- mifid-ii
- hft
brokers_frameworks:
- Generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill when auditing a proprietary trading firm or hedge fund to verify that trading activity has not crossed regulatory thresholds requiring formal licensing or registration. As off-exchange volume, message rates, and customer activity scale, firms frequently cross one or more thresholds (SEC's amended Rule 15b9-1, MiFID II's HFT designation, or SEBI's retail algo limits) that mandate immediate legal and compliance review.

The output is an audit-trail-friendly `LicensingComplianceReport`. It is not a substitute for legal opinion: every `requires_registration = True` result must be escalated to the Chief Compliance Officer and qualified regulatory counsel before the desk continues operating.

## Prerequisites

- Python 3.9+
- Firm-wide trading metrics measured over a documented rolling window:
  - `off_exchange_volume_usd` (dark-pool / ATS executions, in USD)
  - `peak_orders_per_second` (peak 1-second OPS)
  - `is_exchange_member`, `has_customers`, and `jurisdiction`
- Operating jurisdiction of the firm.

The module does not compute metrics from raw order data; it consumes a pre-aggregated `FirmTradingActivity` snapshot. Aggregation discipline (windowing, exchange-vs- ATS classification, customer-fund segregation) is unchanged from earlier versions and is treated as a prerequisite.

## Workflow

1. **Data Aggregation**: Build the firm's `FirmTradingActivity`. The constructor validates jurisdiction, finite non-negative metrics, and rejects `NaN`, `±inf`, or `bool` masquerading as numeric.
2. **Threshold Configuration**: Construct a `LicensingThresholdEvaluator`. Defaults reflect commonly-cited policy benchmarks. Pass `sebi_retail_ops_limit=`, `mifid_ii_hft_ops_limit=`, or `sec_off_exchange_floor_usd=` overrides when the firm operates under stricter internal policy.
3. **Threshold Evaluation**: Call `evaluator.evaluate(activity)`. Every triggered rule is collected; the report's `violations` tuple preserves the full list in deterministic order. `rule_id` identifies the dominant regulatory chain that drove the decision.
4. **Triaging**: Inspect `reason`, `violations`, and `rule_id`. If `requires_registration` is `True`, the desk is **non-compliant** under the configured thresholds and must throttle or suspend relevant activity until registration is obtained.
5. **Audit**: Record the `LicensingComplianceReport` (`evaluated_at`, `schema_version`, `violations`, `rule_id`) in the firm's compliance log. Re-run on a scheduled cadence; thresholds are policy benchmarks that can change.

## Common Pitfalls

- **Relying on Outdated Exemptions**: Assuming a proprietary firm is exempt from FINRA registration because they have no customers. The 2023 SEC amendments to Rule 15b9-1 effectively closed this loophole for modern HFT firms with material off-exchange flow.
- **Ignoring Message Rates (EU)**: Failing to track peak quotes per second can lead to an inadvertent transition into a regulated HFT entity under MiFID II.
- **Treating a Single Threshold as Decisive**: Real activity can simultaneously breach customer-fund, off-exchange, and OPS thresholds; the report returns every violation. A "compliant" report only means none of the configured rules fired, not that the firm is licensed.
- **Confusing Benchmarks with Rule Text**: The default OPS limits are practical policy benchmarks. They are not verbatim citations of SEC / ESMA / SEBI rule text and may diverge from current regulation. Always pair the report with counsel-reviewed threshold configuration.
- **Logging User-Provided Fields Verbatim**: Build downstream logging on `report` attributes only — never on raw call-site strings — to avoid log-injection through free-form fields.

## Verification

Run `python scripts/test_algorithmic_trading_firm_licensing_thresholds.py` to assert threshold breaches trigger non-compliance alerts across US, EU, and IN jurisdictions, that malformed inputs are rejected by the dataclass, that violations are collected and deduped, that `LicensingComplianceReport` carries stable metadata, and that constructor overrides change the decision.

## Related Skills

- `finra-algo-trading-registration-requirements`
- `mifid-ii-algo-trading-compliance-eu`
- `india-sebi-algo-trading-tagging-requirements`

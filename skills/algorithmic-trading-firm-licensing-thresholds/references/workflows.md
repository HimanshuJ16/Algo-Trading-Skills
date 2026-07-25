# Workflows for Algorithmic Trading Firm Licensing

## Compliance Auditing Pipeline

1. **Daily Metric Aggregation**: At EOD, aggregate the firm's total off-exchange volume, peak 1-second order rates, and customer trade flags from the data warehouse.
2. **Threshold Evaluation**: Pass these metrics into the `LicensingThresholdEvaluator` for the firm's operating jurisdiction.
3. **Alerting**: If the evaluator flags `requires_registration = True`, an immediate critical alert is routed to the Chief Compliance Officer (CCO) and General Counsel.
4. **Remediation**: The trading desk must immediately throttle order rates or disable off-exchange routing until formal regulatory licensing is secured.
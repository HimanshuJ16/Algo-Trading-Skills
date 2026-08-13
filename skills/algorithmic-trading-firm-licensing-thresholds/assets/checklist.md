# Checklist for Firm Licensing Thresholds

## Data and Code Controls

- [ ] Dark pool and ATS executions are accurately aggregated to `off_exchange_volume_usd` per documented rolling window for US compliance.
- [ ] `peak_orders_per_second` is computed over a rolling 1-second window for EU/IN compliance, including quote and order events.
- [ ] Customer-trade detection (`has_customers`) is reviewed by Compliance before each evaluation run; once `True`, any prior "compliant" history requires re-examination.
- [ ] Aggregation layer rejects NaN/inf and negative metrics before constructing `FirmTradingActivity`. Dataclass validation should not be the first line of defense in production.
- [ ] `LicensingThresholdEvaluator` is constructed with policy-reviewed thresholds; constructor overrides are tracked in the change log.
- [ ] `LicensingComplianceReport` records include `evaluated_at`, `schema_version`, `rule_id`, and `violations`, and are archived immutably for regulator inspection.

## Operational Controls

- [ ] Run the unit test suite: `python scripts/test_algorithmic_trading_firm_licensing_thresholds.py`
- [ ] Run the repository-wide skill validator: `python tools/validate_skills.py`
- [ ] On any `requires_registration = True`, the CCO and General Counsel are paged.
- [ ] On any customer-funds violation (`has_customers = True`), the trading desk is paused regardless of other `violations`.
- [ ] Threshold configuration changes are reviewed by Compliance and Legal; constructor overrides are mirrored in the policy registry.
- [ ] Reports flagged with `rule_id = None` (unrecognized jurisdiction) are routed to manual legal review and never silently treated as compliant.
- [ ] Customer funds are not commingled with proprietary capital at the desk level without explicit compliance sign-off.

## Sign-off
- Chief Compliance Officer (CCO): ___________________________
- Date: ___________________________

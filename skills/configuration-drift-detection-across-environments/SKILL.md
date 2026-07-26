---
name: configuration-drift-detection-across-environments
description: >-
  Quantitative infrastructure module for auditing configuration drift across trading environments (DEV, STAGING, PROD) against a Golden Source baseline using allowed-override rules and severity scoring.
domain: Infrastructure
subdomain: DevOps & Configuration Management
tags: ["configuration-drift", "devops", "gitops", "env-parity", "golden-source", "audit", "risk-control"]
brokers_frameworks: ["Generic Infrastructure", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in CI/CD deployment pipelines or pre-trade startup routines to verify environment parity across trading hosts (`DEV`, `STAGING`, `PROD`, `CANARY`). Unintended configuration drift (e.g., a developer changing `max_drawdown_stop_pct` in staging and accidentally deploying it to production, or mismatched API endpoints) is a major root cause of quantitative trading outages. This module compares target configs against a Golden Source baseline, accounting for allowed environment-specific overrides.

## Prerequisites

- Golden Source configuration (e.g. `prod_baseline.json` or GitOps main config).
- Target environment configuration dictionary or file.
- List of whitelisted environment-specific key overrides (`allowed_overrides`).

## Workflow

1. **Baseline Ingestion**: Load the Golden Source configuration tree.
2. **Target Ingestion**: Load the target environment configuration tree.
3. **Recursive Drift Audit**:
   - Compare nested keys and values recursively.
   - Categorize differences:
     - `ALLOWED`: Keys in `allowed_overrides` (e.g. `api_url`, `env_name`, `log_level`).
     - `WARNING`: Extra non-critical keys present in target.
     - `CRITICAL`: Missing required keys or value mismatches in core risk/trading parameters (e.g. `max_order_usd`, `position_limit`, `kill_switch_enabled`).
4. **Validation Enforcement**: Return boolean pass/fail (`is_compliant`). If `CRITICAL` drift exists, block deployment / prevent trading engine startup.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hardcoding Allowed Overrides**: Forgetting to whitelist environment-specific parameters like IP addresses or database URLs, causing constant false-positive alarms.
- **Ignoring Type Mismatches**: Failing to detect type coercion drift (e.g. `max_order_qty` configured as string `"100"` instead of integer `100`).
- **Post-Startup Auditing**: Running config drift checks only after trading has started. Drift checks MUST run pre-trade during process initialization.

## Verification

- Instantiate `ConfigurationDriftDetector`. Feed a Golden Source config and a PROD target config with a modified `max_order_usd` parameter ($100,000$ vs $1,000,000$). Verify that the detector flags `CRITICAL` drift and fails compliance (`is_compliant = False`). Whitelist `api_endpoint` and verify it is logged as an `ALLOWED` override.
- Run `python scripts/test_config_drift_detector.py`.

## Related Skills

- `research-environment-vs-production-environment-parity`
- `blue-green-deployment-for-live-strategy-updates`
---

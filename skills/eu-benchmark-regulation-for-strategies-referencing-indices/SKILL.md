---
name: eu-benchmark-regulation-for-strategies-referencing-indices
description: >-
  Quantitative European regulatory compliance engine for auditing EU Benchmark Regulation (EU BMR - Regulation 2016/1011) authorization, ESMA register status, and Article 28(2) robust written fallback provisions.
domain: Regulatory Compliance & Governance
subdomain: European Index & Benchmark Regulation (EU BMR)
tags: ["eu-bmr", "esma-register", "benchmark-regulation", "article-28-2-fallback", "index-referencing", "euribor-estren", "mifid-ii"]
brokers_frameworks: ["ESMA BMR Register", "Regulation (EU) 2016/1011", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European quantitative fund operations, index-referencing trading strategies, structured product desks, and algorithmic execution engines. Under **EU Benchmark Regulation (EU BMR - Regulation (EU) 2016/1011)**, EU supervised entities may only use financial benchmarks (indices, interest rate benchmarks, commodity indices) if the administrator is listed on the official **ESMA BMR Register**. Furthermore, **BMR Article 28(2)** mandates that firms maintain robust written plans detailing alternative fallback benchmarks in case of benchmark material change or cessation.

## Prerequisites

- Referenced benchmark details (`benchmark_name`, `administrator_name`, `is_esma_registered`: True/False, `category`: `'CRITICAL'`, `'SIGNIFICANT'`).
- Strategy fallback provisions (`fallback_benchmark_name`, `has_written_fallback_plan`: True/False).

## Workflow

1. **ESMA BMR Register Authorization Verification**:
   - Audit whether `administrator_name` is listed on the ESMA Public Register of Administrators.
   - If `is_esma_registered` is False $\implies$ Flag `UNAUTHORIZED_BENCHMARK_VIOLATION`.
2. **Article 28(2) Fallback Provision Audit**:
   - Audit presence of documented fallback benchmark and written plan (`has_written_fallback_plan`).
   - If False $\implies$ Flag `MISSING_FALLBACK_PLAN_VIOLATION`.
3. **Cessation Trigger & Rate Switch Simulation**:
   - Simulate benchmark cessation event and verify fallback rate substitution (e.g. EURIBOR $\to$ €STR + Spread Adjustment).
4. **Audit Report Generation**: Output structured `EuBmrAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Referencing Un-Authorized Third-Country Indices**: Using custom or third-country index benchmarks not endorsed or recognized on the ESMA BMR register.
- **Omitting Article 28(2) Fallback Provisions**: Referencing major benchmarks (e.g. STOXX 50, EURIBOR) without documented written plans for benchmark cessation.
- **Failing to Account for Spread Adjustments**: Switching to fallback risk-free rates (€STR, SOFR) during benchmark cessation without calculating ISDA-style credit spread adjustments.

## Verification

- Instantiate `EuBmrComplianceEngine`. Register `EURO STOXX 50` (Administrator = STOXX Ltd, ESMA Registered = True, Fallback = `STOXX 600`, Fallback Plan = True). Audit strategy referencing EURO STOXX 50. Verify engine returns `BMR_COMPLIANT`. Submit strategy referencing `CUSTOM_INDEX` (ESMA Registered = False). Verify engine flags `UNAUTHORIZED_BENCHMARK_VIOLATION`.
- Run `python scripts/test_eu_benchmark_regulation_for_strategies_referencing_indices.py`.

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `best-execution-record-keeping-global`
---

---
name: environment-parity-dev-staging-production
description: Quantitative DevOps engine for auditing 5-factor environment parity (Python
  runtime, dependency lockfile hashes, env vars, DB schema revisions, broker endpoint
  modes) across Dev, Staging, and Production.
domain: Infrastructure & DevOps
subdomain: Environment Parity & CI/CD Validation
tags:
- environment-parity
- dev-staging-prod
- 12-factor-app
- dependency-lockfile
- db-schema-parity
- ci-cd-pipeline
brokers_frameworks:
- 12-Factor App
- Terraform
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative infrastructure engineering, CI/CD automated deployment gates, and trading platform DevOps. Discrepancies between Development, Staging (Paper/Testnet), and Production environments cause subtle bugs, performance regressions, and execution failures. Adhering to 12-Factor App Factor X (Dev/Prod Parity), this engine audits 5 core parity vectors before promoting code to production.

## Prerequisites

- Target environment name (`target_env`: `'DEV'`, `'STAGING'`, `'PRODUCTION'`).
- Production baseline configuration specification (`expected_python_version`, `expected_lockfile_hash`, `expected_db_schema_revision`, `required_env_vars`).
- Active environment configuration instance.

## Workflow

1. **Python Runtime & Dependency Audit**:
   - Compare current Python version and `requirements.lock` SHA-256 hash against production baseline.
2. **Environment Variable & Secret Audit**:
   - Verify presence of mandatory env vars (`BROKER_API_KEY`, `MAX_POSITION_LIMIT`, `DATABASE_URL`).
3. **Database Schema Revision Audit**:
   - Check Alembic/Flyway migration revision head matches production target schema version.
4. **Broker Endpoint Mode Verification**:
   - Verify `STAGING` environment points to Testnet/Paper endpoints and `PRODUCTION` points to Mainnet endpoints.
5. **Parity Score Computation & Gating**:
   - Compute Parity Score ($0\%$ to $100\%$).
   - If any critical vector fails $\implies$ Block deployment (`PARITY_VIOLATION_BLOCKED`).
6. **Audit Report Generation**: Output structured `EnvironmentParityAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Pinned Python Minor Versions**: Running Python 3.11.2 in Dev and Python 3.11.8 in Prod, leading to floating point precision or library incompatibilities.
- **Accidentally Wiring Staging to Mainnet Endpoints**: Misconfiguring Staging environment variables, causing paper trading algorithms to execute real orders on live brokers.
- **Un-Applied Database Migrations**: Promoting trading bots before applying database migration scripts, crashing bots on missing database columns.

## Verification

- Instantiate `EnvironmentParityAuditorEngine`. Audit Staging environment matching Production baseline specs. Verify engine calculates 100% Parity Score and returns `PARITY_VERIFIED_PASSED`. Audit Dev environment with mismatched DB schema revision (`a1b2` vs `c3d4`). Verify engine flags `PARITY_VIOLATION_BLOCKED`.
- Run `python scripts/test_environment_parity_auditor.py`.

## Related Skills

- `research-environment-vs-production-environment-parity`
- `dependency-pinning-and-reproducible-builds`
---

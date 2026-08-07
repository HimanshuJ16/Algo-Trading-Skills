---
name: dependency-pinning-and-reproducible-builds
description: Quantitative software engineering tool for auditing Python dependency
  lockfiles, enforcing exact version pinning (==x.y.z), SHA-256 package hashes, and
  guaranteeing byte-for-byte reproducible builds.
domain: Infrastructure & DevOps
subdomain: Reproducible Builds & Dependency Governance
tags:
- dependency-pinning
- reproducible-builds
- poetry-lock
- pip-tools
- sha256-hash-verification
- supply-chain-security
- lockfile-audit
brokers_frameworks:
- Poetry
- pip-tools
- pip-compile
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative research environments, backtesting frameworks, and production live trading deployments. Unpinned dependencies (e.g. `pandas>=2.0` or `numpy`) allow third-party library updates to introduce floating-point precision changes, silent API deprecations, or subtle calculation shifts. This invalidates historical backtest results and causes production model drift. This module audits lockfiles for exact version pinning (`==`), SHA-256 hashes, and runtime version compatibility.

## Prerequisites

- Dependency lockfile content or lines (`requirements.txt`, `poetry.lock`, `Pipfile.lock`).
- Required target Python runtime version (e.g. `3.11.8`).

## Workflow

1. **Dependency Parsing & Inspection**:
   - Parse package specification lines.
   - Detect pinning operator (`==`, `>=`, `~=`, wildcard `*`).
2. **Hash Verification Audit**:
   - Verify presence of SHA-256 checksum hashes (`--hash=sha256:...`).
3. **Reproducibility Scoring ($0.0$ to $100.0$)**:
   - Deduct 20 points per unpinned package.
   - Deduct 10 points per missing SHA-256 checksum.
4. **Lockfile Generation & Remediation**:
   - Convert floating specifications into exact pinned lockfiles (`pip-compile`).
5. **Audit Report Generation**: Output structured `DependencyAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Floating Version Operators in Production**: Specifying `pandas>=2.0` in production `requirements.txt`, allowing a minor update to break backtest reproducibility.
- **Omitting Sub-Dependency Locking**: Pinning top-level packages (`scipy==1.12.0`) while leaving transitive sub-dependencies unpinned.
- **Ignoring C-Extension Compiler Variations**: Pinning Python packages without locking underlying C/C++ libraries (`libgomp`, `openblas`, `CUDA`).

## Verification

- Instantiate `ReproducibleBuildPinnerEngine`. Submit an unpinned requirements file (`pandas>=2.0`, `numpy`, `ccxt==4.2.0`). Verify engine flags 2 unpinned packages, calculates Reproducibility Score = 60.0, and generates an exact pinned lockfile with SHA-256 hashes.
- Run `python scripts/test_reproducible_build_pinner.py`.

## Related Skills

- `research-environment-vs-production-environment-parity`
- `execution-algorithm-regression-testing-suite`
---

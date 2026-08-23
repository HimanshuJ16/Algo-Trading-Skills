---
name: dependency-pinning-and-reproducible-builds
description: Auditor for pip requirements files that verifies exact version pinning
  (==x.y.z) and valid strong package hashes, so a lockfile can be checked before it
  is used with pip hash-checking mode (pip install --require-hashes).
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
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on the requirements files behind quantitative research environments, backtesting frameworks, and live trading deployments. Floating dependency specifiers (`pandas>=2.0`, or a bare `numpy`) let a third-party release change floating-point behaviour, deprecate an API, or shift a calculation between two installs of the "same" code — which invalidates historical backtests and produces production model drift that looks like alpha decay. This module parses requirements lines, reports which are pinned to an exact version and carry a valid strong hash, and drafts a lockfile skeleton for remediation.

## When NOT to Use

- **As a resolver.** It cannot tell you *which* version to pin, and it cannot discover transitive dependencies. Use `pip-compile --generate-hashes` or `uv pip compile --generate-hashes` for that; use this to audit the result.
- **As proof that `pip install --require-hashes` will succeed.** Hash-checking mode requires hashes for *all* dependencies and errors on any that is not spelled out. This tool sees only the lines you hand it, so it cannot confirm transitive completeness — a file can pass this audit and still fail the install.
- **As a reproducible-build guarantee.** Pinning plus hashes gives a reproducible *install*. A reproducible *build* (bit-for-bit identical artifacts) additionally requires the same build environment and instructions, plus handling of timestamps, locales, build paths and file ordering.
- **On non-requirements formats.** It does not parse `poetry.lock`, `Pipfile.lock`, or the PEP 751 `pylock.toml` standard lock format. Export to requirements format first.
- **As a vulnerability scanner.** A pinned, hashed dependency can still be a known-vulnerable one — see `dependency-vulnerability-scanning-in-ci`.

## Prerequisites

- Requirements-file lines as a `List[str]` (`requirements.txt`, or a `poetry export` / `pip freeze` rendering).
- The target Python runtime version, recorded in the generated lockfile header for traceability.

## Workflow

1. **Line Reassembly**:
   - Join backslash continuations *before* parsing. `pip-compile --generate-hashes` emits one requirement across several physical lines; parsing them independently reads the `--hash` lines as separate nameless packages and mis-audits a correctly locked file.
2. **Directive Filtering**:
   - Skip pip control directives (`-r`, `-c`, `-e`, `--index-url`, `--require-hashes`, ...). They are options, not requirements; counting them as packages produces false unpinned findings.
3. **Specifier Classification** — an exact pin is `==X.Y.Z`, `===X.Y.Z`, or a direct URL/path reference. Specifically **not** exact:
   - `==2.2.*` — PEP 440 *prefix matching*, which accepts any `2.2.x` release. This is the failure mode the audit exists to catch, and it looks like a pin.
   - `~=`, `>=`, `<`, `!=`, or a bare package name.
   - Strip environment markers (`; python_version >= "3.9"`) and extras (`[security]`) before reading the version, or the parsed version is corrupt.
4. **Hash Validation** — presence is not sufficient; the algorithm and the digest must both be checked:
   - Accept `sha256` (recommended) and stronger. **Reject `md5`, `sha1`, `sha224`** — pip excludes these from hash-checking mode "to avoid giving a false sense of security".
   - Verify the digest is hex of the algorithm's full length (64 characters for sha256). A short or non-hex digest is not a hash.
   - Retain *every* hash on a requirement: a package with wheels for several platforms legitimately carries several.
5. **Proportional Scoring ($0.0$ to $100.0$)**:
   - $\text{Score} = 100 \times \left(w_{\text{pin}} \frac{N_{\text{pinned}}}{N} + w_{\text{hash}} \frac{N_{\text{hashed}}}{N}\right)$, defaults $w_{\text{pin}} = w_{\text{hash}} = 0.5$.
   - Proportional rather than a fixed penalty per package: hash-checking mode requires every transitive dependency to be listed, so production files routinely hold hundreds of entries and any absolute penalty saturates at zero.
   - The score is an internal engineering heuristic. It is **not** a standard — see `references/standards.md`.
6. **Remediation Drafting**:
   - Compliant requirements pass through verbatim. Deficient ones are emitted as **commented** `# TODO(...)` lines naming the command that resolves them. A version or a hash is never invented.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Never synthesise a hash to make an audit pass.** A fabricated digest either fails `--require-hashes` outright or, if it were ever trusted, defeats the exact integrity check the hash exists to provide. Emit an unresolved TODO instead — a remediation tool that invents hashes is worse than no tool at all.
- **`==2.2.*` is not a pin.** PEP 440 treats a trailing `.*` as prefix matching, so it silently accepts any `2.2.x`. It passes a naive `"==" in line` check, which is why an auditor must parse the specifier rather than substring-match it.
- **Presence of `--hash=` is not hash verification.** `--hash=md5:...` is rejected by pip as too weak, and `--hash=sha256:nothex` is not a digest at all. Validate the algorithm *and* the digest, or the audit reports a false green.
- **Pinning top-level packages only.** `scipy==1.12.0` with unpinned transitive dependencies is not locked; `--require-hashes` errors on the first dependency that is not spelled out.
- **Auditing physical lines instead of logical requirements.** Real hashed lockfiles use backslash continuations, so a line-by-line audit mis-reads the most common lockfile shape there is.
- **Assuming pinned Python packages pin the whole environment.** The C/C++ and CUDA libraries beneath the wheels (`openblas`, `libgomp`) are not covered by any `--hash` and can change numerical results across otherwise-identical installs. Pin the base image too.

## Verification

- Instantiate `ReproducibleBuildPinnerEngine()`. Audit `["pandas>=2.0", "numpy", "ccxt==4.2.0 --hash=sha256:<64 hex digest>"]`: expect `total_packages_audited=3`, `unpinned_packages=['pandas', 'numpy']`, `missing_hashes_count=2`, `reproducibility_score=33.33`, `all_requirements_pinned_and_hashed=False`.
- Confirm the generated lockfile contains **no** uncommented line for `pandas` or `numpy`, and that no fabricated digest appears anywhere in it.
- Audit a real `pip-compile --generate-hashes` block (`numpy==1.26.4 \` followed by two indented `--hash=sha256:` lines): expect `total_packages_audited=1` and `reproducibility_score=100.0`, not three packages.
- Confirm `--hash=md5:<digest>` yields `missing_hashes_count=1` and `all_requirements_pinned_and_hashed=False`.
- Confirm `pandas==2.2.*` is reported in `unpinned_packages`.
- Run `python -m unittest discover -s skills/dependency-pinning-and-reproducible-builds/scripts`.

## Related Skills

- `research-environment-vs-production-environment-parity`
- `execution-algorithm-regression-testing-suite`
- `dependency-vulnerability-scanning-in-ci`
- `backtest-determinism-and-reproducibility`
- `immutable-infrastructure-for-trading-bots`

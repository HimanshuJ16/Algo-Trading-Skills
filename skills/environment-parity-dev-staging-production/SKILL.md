---
name: environment-parity-dev-staging-production
description: >-
  Use as the gate when a build moves between dev, staging and production, auditing
  declared runtime release, dependency lockfile hash, schema head and broker endpoint.
  Numeric research-versus-live signal parity is
  research-environment-vs-production-environment-parity.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, environment-parity, dev-staging-prod, 12-factor-app, dependency-lockfile, db-schema-parity, ci-cd-gate
  brokers_frameworks: "12-Factor App; Alembic; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill at the moment a build is about to move between environments — a CI/CD
promotion step, or the pre-flight check a trading process runs before it opens its
broker socket. It answers one question: *is this environment the one this release was
approved to run in?*

The five vectors it audits are the ones whose drift produces silent, expensive failures
rather than loud ones: a Python patch release that differs from the one the strategy was
validated on, a dependency set that is not the locked set, a database missing the
migration the new code expects, mandatory configuration that never made it into the
environment, and — the one that costs real money — a paper environment wired to a live
broker endpoint.

It sits alongside `configuration-drift-detection-across-environments`, which compares a
full configuration tree key-by-key. This skill is deliberately narrower: five named
vectors, an all-or-nothing verdict, cheap enough to run on every promotion.

## When NOT to Use

- **As evidence about a running host.** Every value audited is *declared* by the caller
  in an `EnvironmentSpec`. Nothing connects to an interpreter, a database or a broker.
  The audit is exactly as trustworthy as the collection step that built the spec — a
  spec hand-written to match the baseline passes cleanly. Collect the values from the
  live target, not from a config file describing it.
- **As proof that two environments have the same packages installed.** A matching
  lockfile hash proves the lockfile *file* is byte-identical. Dependency specifiers
  carry environment markers — `python_version`, `sys_platform`, `platform_machine` —
  so the same file resolves to different distributions on different hosts. Use
  `dependency-pinning-and-reproducible-builds` to audit the lockfile itself, and
  verify the installed set separately.
- **As a config-tree differ.** Only the presence of named environment variables is
  checked, never their values. For arbitrary nested configuration use
  `configuration-drift-detection-across-environments`.
- **As an API contract check.** That a staging endpoint is *labelled* TESTNET says
  nothing about whether its payload schema still matches production's — see
  `sandbox-vs-production-endpoint-drift`.
- **For research-vs-production signal parity.** Comparing model outputs, float
  precision and feature definitions is a different problem; see
  `research-environment-vs-production-environment-parity`.

## Prerequisites

- An `EnvironmentSpec` for the environment under audit, with `env_name` one of exactly
  `DEV`, `STAGING`, `PRODUCTION`. Other names are rejected, not guessed at.
- An `EnvironmentSpec` for the **release specification**, named `PRODUCTION`. This is
  the Python release, lockfile hash and schema head the release was *approved* to run
  on — not a snapshot of whatever is live in production right now. See the decision
  point in step 3.
- `python_version` as a full `major.minor.patch` release (`current_python_version()`
  returns it in the right form). `3.11` is rejected.
- `lockfile_sha256` as a 64-character hex digest. Produce it with
  `sha256_of_lockfile(path)` so every environment digests the file identically.
- `db_schema_revision` as the **complete** `alembic heads` output — every head, not the
  first line.
- The list of mandatory environment variable names (defaults:
  `BROKER_API_KEY`, `MAX_POSITION_LIMIT`, `DATABASE_URL`).

## Workflow

1. **Collect specs, and let collection failures fail loudly.**
   Build one `EnvironmentSpec` per environment from the live target.
   - **Decision point — a value you could not resolve is not an empty string.** A blank
     field raises `ValueError` rather than being audited. This is the single most
     important behaviour in the module: under naive string equality, two environments
     that both failed to resolve a lockfile hash compare `"" == ""`, pass, and the gate
     reports 100% parity on no evidence at all. If a collection step fails, fail the
     pipeline step — never substitute a default.

2. **Python runtime and dependency audit.**
   Compare the full release version and the lockfile SHA-256 against the baseline.
   - **Decision point — a lockfile mismatch is not automatically a build error.** Decide
     whether the lockfile legitimately changed (a dependency was intentionally bumped,
     so the *baseline* is stale and needs re-approval) or whether the environment is
     running something nobody locked. Re-approve the baseline in the first case; rebuild
     the environment in the second. Do not edit the spec to make the audit pass.

3. **Database schema revision audit.**
   Compare migration heads order-independently — `alembic heads` prints multiple heads
   on a branched history in no guaranteed order, so `"a, b"` and `"b, a"` are the same
   state.
   - **Decision point — which direction is the drift?** If staging is *ahead* of live
     production because it is validating a migration, that is the normal workflow and
     the baseline is the release spec, which already includes the new head. If the
     environment is *behind* the baseline, the new code will hit a missing column on its
     first query: apply the migration before promoting, never after.

4. **Broker endpoint mode verification.**
   `DEV` and `STAGING` must be wired to `TESTNET`; `PRODUCTION` must be wired to
   `MAINNET`. Both directions are failures, not just one.
   - **Decision point — the reverse misconfiguration is also a failure.** A production
     release pointed at a testnet endpoint will start, connect, log fills and report
     P&L, all against paper. It is silent for as long as nobody reconciles against the
     broker statement. Do not treat "at least it isn't trading real money" as safe.

5. **Mandatory environment variable audit.**
   Presence and non-emptiness only. Values are never compared — `DATABASE_URL` and
   `BROKER_API_KEY` are *supposed* to differ per environment — and never copied into the
   report.

6. **Gate on the verdict, not the score.**
   - **Decision point — `parity_score_pct` is diagnostic, `is_deployment_allowed` is the
     gate.** A staging environment wired to MAINNET scores 80%. Eighty percent is not a
     near-pass; it is one failed vector away from executing live orders from a paper
     strategy. Branch on `is_deployment_allowed` (or `audit_status ==
     "PARITY_VIOLATION_BLOCKED"`), never on a score threshold.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing missing evidence as parity.** The failure mode that makes a parity gate
  worse than no gate: a collection script returns empty strings for every vector, each
  vector compares equal to the equally-empty baseline, and the pipeline records
  `PARITY_VERIFIED_PASSED` at 100%. Absent evidence must raise. Never default a parity
  field.
- **Free-form environment names.** Classifying anything that is not literally
  `PRODUCTION` as non-production means an environment named `PROD` or `prod-eu-1` is
  expected to be on TESTNET — so a genuinely live deployment wired to a paper endpoint
  passes the endpoint check. Environment names are matched against a closed set and an
  unrecognised name is rejected.
- **Pinning only the minor version.** `3.11` on both sides compares equal while one host
  runs 3.11.2 and the other 3.11.8. Patch releases ship stdlib bug fixes and security
  patches; pin `major.minor.patch` and let the gate enforce it. (Note the *verifiable*
  risk is behavioural change in the standard library and its C extensions — not
  floating-point arithmetic, which CPython does not vary across patch releases.)
- **Reading an identical lockfile hash as an identical environment.** It is not.
  Environment markers (PEP 508) resolve the same file differently per Python version,
  platform and architecture. Two hosts can hold the same lockfile and different site
  packages.
- **Line endings silently breaking the hash.** A `requirements.lock` checked out on
  Windows with `core.autocrlf=true` has CRLF endings; on a Linux build host it has LF.
  Same content, different SHA-256, gate blocked forever. Fix the checkout
  (`core.autocrlf=input`, or mark the lockfile `-text` in `.gitattributes`) — do not
  "fix" it by hashing in text mode, which would also hide genuine changes.
- **Passing one Alembic head when the history has two.** `alembic current` on a branched
  database reports several revisions. Pass the complete head set; a single head compared
  against a two-head baseline is drift, but a single head passed as though it were the
  whole story is a partially-migrated database that the gate cannot see.
- **Comparing environment variable values.** They must differ. Auditing them produces
  permanent false failures and puts secrets in CI logs.
- **Leaking secrets through a dataclass repr.** `EnvironmentSpec.env_vars` holds live
  credentials; an unguarded `repr` puts `BROKER_API_KEY` into every traceback and CI log
  line. The field is declared `repr=False` for exactly this reason — keep it that way.
- **Swapping the two arguments.** Both are `EnvironmentSpec`, so an order swap type-checks
  and produces a plausible-looking report about the wrong environment. The baseline is
  required to be a `PRODUCTION` spec so the mistake raises instead.

## Verification

- Construct a `PRODUCTION` baseline and a matching `STAGING` spec on `TESTNET`; verify
  `parity_score_pct == 100.0`, `is_deployment_allowed` is `True`, and `audit_status ==
  "PARITY_VERIFIED_PASSED"`.
- Flip the staging spec to `MAINNET`; verify the audit blocks with
  `failed_vector_names == ["BROKER_ENDPOINT"]` at 80%. Repeat with a `PRODUCTION` spec on
  `TESTNET` — that must block too.
- Regression checks against fail-open behaviour: a blank `python_version`,
  `lockfile_sha256`, `db_schema_revision` or `broker_endpoint_mode` must raise, not pass;
  `"3.11"` must raise; a 16-character "hash" must raise; `env_name="PROD"` must raise.
- Verify `"27c6a30d7c24, ae1027a6acf"` and `"ae1027a6acf\n27c6a30d7c24"` compare equal,
  and that a single head against that two-head baseline blocks.
- Verify a secret placed in `env_vars` appears in neither `repr(spec)` nor `repr(report)`.
- Run `python -m unittest discover -s skills/environment-parity-dev-staging-production/scripts` and confirm a 100% pass rate.

## Related Skills

- `configuration-drift-detection-across-environments`
- `dependency-pinning-and-reproducible-builds`
- `sandbox-vs-production-endpoint-drift`
- `research-environment-vs-production-environment-parity`
- `paper-to-live-promotion-checklist`
- `blue-green-deployment-for-live-strategy-updates`

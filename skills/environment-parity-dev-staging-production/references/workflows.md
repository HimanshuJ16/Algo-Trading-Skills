# Workflows — environment-parity-dev-staging-production

## 0. Collect the specs from the live target

Build each `EnvironmentSpec` by interrogating the environment, not by reading a file that
describes it. A spec transcribed from a config template proves only that the template is
self-consistent.

```python
import os

from environment_parity_auditor import (
    EnvironmentSpec, EnvironmentParityAuditorEngine,
    sha256_of_lockfile, current_python_version,
)

target = EnvironmentSpec(
    env_name="STAGING",                               # DEV | STAGING | PRODUCTION only
    python_version=current_python_version(),          # 'major.minor.patch'
    lockfile_sha256=sha256_of_lockfile("requirements.lock"),
    db_schema_revision=" ".join(alembic_heads()),     # every head, not the first line
    broker_endpoint_mode="TESTNET",
    env_vars=dict(os.environ),
)
```

**Collection failures must fail the pipeline step.** Every parity field is required to be
non-blank and well-formed; a missing value raises `ValueError`. Do not catch that and
substitute a default — a default is precisely what turns this gate into a rubber stamp.

## 1. Parity vector auditing

| Vector | Compared how |
|---|---|
| `PYTHON_VERSION` | Exact string equality on the full `major.minor.patch` release. |
| `LOCKFILE_HASH` | Full 64-character SHA-256, case-insensitive. The report displays a 12-character prefix; the comparison always uses the whole digest. |
| `DB_SCHEMA` | Set equality over migration heads, order-independent. |
| `BROKER_ENDPOINT` | Against the mode required for that environment, both directions. |
| `ENV_VARS_PRESENT` | Presence and non-emptiness of each required key. Values are never read for comparison, never reported. |

## 2. Discrepancy evaluation

The report names what failed. Route each vector to the right remediation rather than
retrying the pipeline:

| Failed vector | What it usually means | Remediation |
|---|---|---|
| `PYTHON_VERSION` | Base image or `pyenv` pin drifted | Rebuild the image against the pinned release. Never relax the baseline to match the host. |
| `LOCKFILE_HASH` | Either a stale baseline after an intentional bump, or an environment running an unlocked set. Line endings are the third possibility — see below. | Re-approve the baseline, or rebuild the environment. Decide which before touching either. |
| `DB_SCHEMA` | Behind the baseline (migration not applied) or ahead (validating a migration the release spec already includes) | Apply the migration *before* promoting. Behind is a hard block; ahead-of-live-production is normal and is not what this compares against. |
| `BROKER_ENDPOINT` | Endpoint configuration crossed between environments | Fix the environment. This vector has no legitimate override. |
| `ENV_VARS_PRESENT` | Secret injection or config map incomplete | Fix the environment. Do not shrink `required_env_var_keys` to make the audit pass. |

### The line-endings trap

A `requirements.lock` checked out on Windows with `core.autocrlf=true` carries CRLF; the
same file on a Linux build host carries LF. Identical content, different SHA-256, and a
gate that blocks every promotion forever. Fix the checkout — `core.autocrlf=input`, or a
`.gitattributes` entry marking the lockfile `-text`. Do not switch to text-mode hashing:
it would hide genuine content changes along with the newline difference.

## 3. Parity score computation

`parity_score_pct = 100 × passed_vectors / 5`, rounded to one decimal.

It is a **diagnostic** for triage — which of five things is wrong — and nothing else.
There is no severity weighting, so a staging environment wired to a live broker scores
80%, the same as a Python patch mismatch. Never branch on a score threshold.

## 4. CI/CD deployment gate

```python
report = EnvironmentParityAuditorEngine().audit_environment_parity(target, baseline)
if not report.is_deployment_allowed:
    raise SystemExit(f"{report.audit_status}: {report.failed_vector_names}")
```

Gate on `is_deployment_allowed` (equivalently `audit_status == "PARITY_VIOLATION_BLOCKED"`).

Run the gate in **two** places. In CI, before the artifact is promoted — that catches the
mistake early and cheaply. Then again during process initialization on the target host,
before the broker socket opens — that is the run that actually protects capital, because
it executes against the environment the trading process will really use, not against a
CI runner's idea of it.

## 5. Retention

Keep the report. It records which release specification an environment was gated against
and when, which is the evidence you need when reconstructing what a strategy was running
during an incident — and, for firms in scope of MiFID II RTS 6, part of the deployment
documentation trail described in `references/standards.md`.

# Workflows for Configuration Drift Detection

## 1. Define the Golden Source

Designate exactly one version-controlled configuration tree as the baseline (GitOps main
branch, or a released `prod_baseline.json`). If two candidate baselines exist, the audit
is meaningless — resolve that first. See `reference-data-golden-source-designation` for
the general pattern.

## 2. Define the override policy

Split every key that legitimately differs between environments into one of two buckets:

- **Whitelisted** (`allowed_overrides`) — connectivity, naming and logging: `env_name`,
  `api_url`, `broker_endpoint`, `log_level`, `port`, `host`, `db_name`. Prefer the exact
  dot-separated path (`system.api_url`) over the bare leaf name (`api_url`), which
  whitelists that name everywhere in the tree.
- **Protected** (`protected_keys`) — risk-control parameters, which are never
  whitelistable. Extend `DEFAULT_PROTECTED_KEYS` with the risk parameters specific to
  your schema; the detector cannot infer which of your keys are risk limits.

Passing `allowed_overrides=set()` is the zero-tolerance policy: every difference is
CRITICAL. Passing `None` selects the built-in connectivity whitelist.

## 3. Load both trees

Ingest `golden_baseline` and `target_config` as dicts. Both must be dicts — the detector
raises `TypeError` otherwise rather than auditing partially. Load them from the same
source of truth the trading process will actually read, not from a copy prepared for the
audit; auditing a file the engine never opens proves nothing.

## 4. Run the audit

`ConfigurationDriftDetector(...).audit(golden_baseline, target_config)` flattens both
trees to dot-separated paths and classifies every key in the union:

| Condition | Severity | Blocks? |
|---|---|---|
| Key in baseline, missing from target | `CRITICAL` | Yes |
| Value or type differs, key is protected | `CRITICAL` | Yes |
| Value or type differs, key not whitelisted | `CRITICAL` | Yes |
| Value or type differs, key whitelisted and not protected | `ALLOWED` | No |
| Key in target, absent from baseline | `WARNING` | No |

A missing key is CRITICAL even when whitelisted: an override may change a value, not
delete a setting the engine expects to read.

`audit()` raises `ValueError` if either tree contains an ambiguous key path — two
distinct keys that flatten to the same dotted path. Resolve the collision; do not catch
and ignore it, because the alternative is an audit that silently skipped a branch.

## 5. Enforce the decision

- `report.is_compliant is False` → **block**. Fail the CI/CD promotion, or abort process
  initialization before the trading socket opens. Do not open the socket and alert; the
  point of a pre-trade gate is that it runs pre-trade.
- `report.is_compliant is True` with `warning_drift_count > 0` → proceed, but route the
  WARNING items to review. An extra key in production is still an unreviewed change.
- Persist `report.drift_items` to the deployment audit log alongside who authorized the
  deployment. For firms in scope of RTS 6 this record supports the Art. 5(7) and Art. 11
  obligations described in `references/standards.md`.

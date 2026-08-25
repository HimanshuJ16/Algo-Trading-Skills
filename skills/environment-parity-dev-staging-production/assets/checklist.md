# Pre-Flight Checklist — Environment Parity Promotion Gate

Sign off before promoting a build into DEV, STAGING or PRODUCTION.

## Collection (the audit is only as good as this)

- [ ] Every spec value was read from the **live target environment**, not transcribed
      from a config template describing it.
- [ ] No collection step returned a blank or defaulted value. A field that could not be
      resolved failed the pipeline step instead of being filled in.
- [ ] `python_version` is a full `major.minor.patch` release — not `3.11`.
- [ ] `lockfile_sha256` was produced by `sha256_of_lockfile()` (binary mode) on both
      sides, so line endings cannot silently change the digest.
- [ ] `db_schema_revision` carries the **complete** `alembic heads` output, every head.
- [ ] `env_name` is exactly `DEV`, `STAGING` or `PRODUCTION`.

## Audit

- [ ] The baseline is the **approved release specification**, and it has been re-approved
      if the lockfile or schema head changed since it was written.
- [ ] Arguments are in the right order: environment under audit first, baseline second.
- [ ] Python runtime release matches the baseline.
- [ ] Lockfile SHA-256 matches the baseline — and it is understood that this proves the
      lockfile file is identical, not that the installed distributions are.
- [ ] Migration head(s) match the baseline; if the environment is behind, the migration
      was applied **before** promotion, not after.
- [ ] Broker endpoint mode matches the environment: DEV/STAGING on `TESTNET`,
      PRODUCTION on `MAINNET`. A production release found on `TESTNET` was treated as a
      failure, not as a safe default.
- [ ] All mandatory environment variables are present and non-empty, and
      `required_env_var_keys` was not trimmed to make the audit pass.

## Gate

- [ ] The promotion decision was taken on `is_deployment_allowed`, not on
      `parity_score_pct`. No score threshold anywhere in the pipeline.
- [ ] The gate runs in CI **and** again at process initialization on the target host,
      before the broker socket opens.
- [ ] No secret value appears in the pipeline logs or in any retained report.
- [ ] The audit report is retained against this release for incident reconstruction.

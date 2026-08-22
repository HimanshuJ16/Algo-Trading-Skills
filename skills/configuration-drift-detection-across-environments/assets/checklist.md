# Pre-Flight Checklist

## Baseline and policy

- [ ] Is exactly one Golden Source configuration designated, and is it version-controlled (GitOps)?
- [ ] Are environment-specific allowed overrides (host, port, log level, endpoints) explicitly whitelisted?
- [ ] Are overrides scoped by exact dotted path (`system.api_url`) rather than bare leaf name where the override should apply to only one section?
- [ ] Are risk limits excluded from `allowed_overrides`?
- [ ] Has `protected_keys` been extended with the risk-parameter names specific to this schema, rather than relying on the built-in defaults alone?

## Execution

- [ ] Does the audit read the same configuration the trading process will actually load, not a copy prepared for the audit?
- [ ] Does the audit run **before** the trading socket opens and before any order is submitted?
- [ ] Does the audit also gate CI/CD promotion, so drift is caught before the artifact reaches a host?

## Enforcement

- [ ] Does the detector fail compliance and block deployment when `CRITICAL` drift is detected?
- [ ] Are `WARNING` items (extra keys in the target) routed to review rather than discarded, given they never block on their own?
- [ ] Is `ValueError` from an ambiguous key path treated as a hard failure rather than caught and ignored?
- [ ] Are `drift_items` persisted to the deployment audit log alongside the authorizing person?

## Known limits acknowledged

- [ ] Is it understood that a PASS proves configuration parity under the configured whitelist, not that the process loaded that configuration or kept it unmutated after startup?

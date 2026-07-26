# Pre-Flight Checklist

- [ ] Is Golden Source configuration defined in version-controlled repository (GitOps)?
- [ ] Are environment-specific allowed overrides (e.g. host IP, log level) explicitly whitelisted?
- [ ] Are risk limits excluded from allowed overrides?
- [ ] Does the detector fail compliance and block deployment if `CRITICAL` drift is detected?
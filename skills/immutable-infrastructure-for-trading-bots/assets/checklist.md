# Pre-Flight Checklist

- [ ] Is container root filesystem configured as read-only (`--read-only`)?
- [ ] Is image cryptographically signed via Cosign?
- [ ] Are temporary directory paths (`/tmp`) mounted via `tmpfs`?
- [ ] Is in-place SSH access disabled on production nodes?

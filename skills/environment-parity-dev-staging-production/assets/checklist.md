# Pre-Flight Checklist

- [ ] Is Python runtime version identical across Dev, Staging, and Production?
- [ ] Is `requirements.lock` SHA-256 hash verified before pipeline promotion?
- [ ] Are mandatory environment variables validated?
- [ ] Is DB schema migration revision head verified against production baseline?
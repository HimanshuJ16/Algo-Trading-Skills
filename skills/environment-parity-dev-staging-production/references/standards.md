# Standards for Environment Parity Dev Staging Production

| Metric | Engineering Standard |
|---|---|
| Python Runtime Match | Python release version MUST match production target exactly (e.g. `3.11.8`). |
| Dependency Lockfile Hash | `requirements.lock` SHA-256 hash MUST be identical across environments. |
| Database Schema Head | DB migration revision head MUST match production schema baseline before release. |
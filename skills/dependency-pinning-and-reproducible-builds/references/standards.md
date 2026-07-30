# Standards for Dependency Pinning and Reproducible Builds

| Metric | Engineering Standard |
|---|---|
| Exact Version Pinning | ALL production Python packages MUST use exact `==` version pinning. |
| SHA-256 Hash Lock | ALL production deployment lockfiles MUST specify SHA-256 hashes (`--hash`). |
| Reproducibility Index Ceiling | Production deployment pipelines MUST achieve a Reproducibility Score of 100.0. |
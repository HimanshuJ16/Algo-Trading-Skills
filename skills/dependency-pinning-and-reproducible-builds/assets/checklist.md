# Pre-Flight Checklist

- [ ] Are all top-level and transitive dependencies pinned with exact `==` versions?
- [ ] Are SHA-256 package checksum hashes included in the lockfile?
- [ ] Is Python runtime version (`python_version`) explicitly pinned in CI/CD?
- [ ] Is the lockfile (`poetry.lock` / `requirements.txt`) committed to version control?
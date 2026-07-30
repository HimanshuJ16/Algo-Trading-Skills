# Workflows for Dependency Pinning and Reproducible Builds

1. **Lockfile Parsing**:
   - Parse package dependency declarations and version operators.
2. **Pinning & Hash Verification**:
   - Audit exact `==` operators and `--hash=sha256:...` declarations.
3. **Reproducibility Index Computation**:
   - Compute composite Reproducibility Score ($0.0$ to $100.0$).
4. **Lockfile Enforcement**:
   - Emit fully pinned lockfiles for CI/CD integration.
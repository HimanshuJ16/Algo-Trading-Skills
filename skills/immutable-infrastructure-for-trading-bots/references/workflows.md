# Workflows for Immutable Infrastructure Audit

1. **Root Filesystem Audit**:
   - Verify container spec enforces `--read-only` root filesystem.
2. **Cosign Cryptographic Signature Audit**:
   - Verify image signature and SHA256 digest against Git commit.
3. **Ephemeral State Isolation Audit**:
   - Verify temporary directories use `tmpfs` memory mounts.
4. **Audit Report Generation**:
   - Output structured immutability report.

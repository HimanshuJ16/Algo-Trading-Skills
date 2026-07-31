# Workflows for Post-Incident Forensics for Suspected Key Compromise

1. **Off-Chain Access Log Audit**:
   - Filter KMS/API gateway logs for non-whitelisted IP addresses and anomalous API calls.
2. **On-Chain Transaction Tracing**:
   - Trace unauthorized outflows from compromised wallet addresses.
3. **Evidence Hash Generation**:
   - Compute SHA-256 evidence integrity hashes over raw forensic logs.
4. **Containment Protocol Dispatch**:
   - Mandate immediate key revocation, exchange address blacklisting, and key rotation.
5. **Audit Report Generation**:
   - Output structured key forensics report.
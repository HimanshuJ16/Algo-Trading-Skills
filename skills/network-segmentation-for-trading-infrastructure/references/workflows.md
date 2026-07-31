# Workflows for Network Segmentation for Trading Infrastructure

1. **Subnet Tier Classification**:
   - Register subnets into security tiers (`PUBLIC_DMZ`, `TRADING_EXECUTION`, `KEY_CUSTODY`).
2. **Zero-Trust Rule Audit**:
   - Audit firewall rules for illegal cross-zone traffic flows and exposed admin ports.
3. **Violation Remediation**:
   - Flag non-compliant firewall rules and generate remediation instructions.
4. **Audit Report Generation**:
   - Output structured network segmentation audit report.
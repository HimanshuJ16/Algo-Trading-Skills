# Workflows for Cross-Border Data Transfer Restrictions

1. **Policy Lookup**:
   - Retrieve policy for route: $\text{Origin} \to \text{Destination}$.
2. **Data Classification**:
   - Identify PII fields (`trader_id`, `client_name`, `account_number`, `tax_id`).
3. **Anonymization Processing**:
   - Replace `client_name` with `ANONYMOUS_CLIENT`.
   - Hash `trader_id` using SHA-256.
   - Redact `account_number`.
4. **Egress Interception**:
   - If route status is `BLOCKED` $\implies$ Abort transfer and log security exception.
   - If route status is `ALLOWED` $\implies$ Transmit sanitized payload.

# Workflows for Cross-Border Data Transfer Restrictions

1. **Policy Lookup**:
   - Normalize and validate country codes ($\text{Origin} \to \text{Destination}$, e.g. `cn` $\to$ `CN`); empty/None codes raise `ValueError` before any decision.
   - Retrieve policy for the route. An explicitly registered policy takes precedence over every default, including the same-country shortcut. Decision points:
     - No policy registered $\implies$ default-deny: treat as `REQUIRES_ANONYMIZATION` under `DEFAULT_STRICT_PRIVACY`.
     - Status not in {`ALLOWED_UNRESTRICTED`, `REQUIRES_ANONYMIZATION`, `BLOCKED`} $\implies$ `ValueError` (fail-closed; rejected at registration, guarded again at processing).
     - Origin == destination **and no policy registered for that route** $\implies$ domestic transfer, approved without masking. A registered same-country policy (e.g. `CN`->`CN` `BLOCKED`) is honoured instead — the domestic shortcut must never override configured policy.
2. **Data Classification**:
   - Identify PII fields (`trader_id`, `client_name`, `account_number`, `tax_id`).
3. **Pseudonymization Processing** (`REQUIRES_ANONYMIZATION` routes):
   - Replace `client_name` with `ANONYMOUS_CLIENT`.
   - Tokenize `trader_id` with keyed HMAC-SHA256 (or salted SHA-256 when no key is configured) — never unsalted SHA-256.
   - Redact `account_number` to last 4 digits (`XXXX-XXXX-1234`); $\le 4$ characters $\to$ `****`.
   - Drop `tax_id` entirely (set to `None`).
4. **Egress Interception**:
   - If route status is `BLOCKED` $\implies$ Return report with `transfer_approved=False`, `sanitized_payload=None`; log security exception. (Blocked routes return a report — they do not raise; malformed input raises before any policy decision.)
   - If route status is `ALLOWED_UNRESTRICTED` (compliance-verified route, e.g. adequacy/DPF-certified recipient) $\implies$ Transmit payload unchanged.
5. **Audit Trail Append**:
   - Append timestamped entry (route, decision, framework, message) to `engine.audit_trail` for every decision, including blocked attempts.
   - `engine.audit_trail` returns a copy of the list **and** of each entry, so a caller holding the returned records cannot rewrite a recorded decision.

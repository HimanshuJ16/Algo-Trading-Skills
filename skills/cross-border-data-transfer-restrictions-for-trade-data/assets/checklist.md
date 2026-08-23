# Pre-Flight Checklist

- [ ] Are origin and destination jurisdiction policy rules configured for all active trading venues, with unknown routes defaulting to anonymization (default-deny)?
- [ ] Are all route statuses restricted to `BLOCKED` / `REQUIRES_ANONYMIZATION` / `ALLOWED_UNRESTRICTED`, with invalid statuses rejected at registration (fail-closed)?
- [ ] Is `ALLOWED_UNRESTRICTED` backed by a verified legal mechanism (adequacy decision, DPF certification incl. UK Extension, SCCs/IDTA, PIPL standard contract) — not just convenience?
- [ ] Is PII pseudonymization implemented with keyed HMAC-SHA256 (or salted SHA-256) — not unsalted hashing — and is the key/salt stored outside the exported data?
- [ ] Are tax IDs dropped entirely (not partially masked) on restriction-bearing routes?
- [ ] Do explicitly registered route policies take precedence over the domestic/same-country shortcut, so a configured intra-country `BLOCKED` rule actually blocks?
- [ ] Is data residency egress filtering active on all API telemetry exporters?
- [ ] Are timestamped audit-trail entries generated for every transfer decision (approved, pseudonymized, blocked, domestic), and are the exposed entries copies that a caller cannot edit after the fact?
- [ ] Do operators understand that pseudonymization is data minimization, not a lawful transfer mechanism, and that GDPR Chapter V / PIPL Art. 38 instruments are still required?

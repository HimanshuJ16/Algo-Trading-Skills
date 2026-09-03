---
name: cross-border-data-transfer-restrictions-for-trade-data
description: >-
  Use when trade telemetry containing personal data crosses a border, to enforce route
  policy under GDPR Chapter V, China PIPL Articles 38 to 40, Swiss banking secrecy and
  similar regimes, and to pseudonymise before egress.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: cross-border, data-governance, gdpr, pipl, pii-masking, tokenization, data-residency
  brokers_frameworks: "Generic Compliance Engine; Python Dataclasses"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in multi-national quantitative trading architectures to enforce cross-border data transfer route policies (e.g. EU GDPR Chapter V, China PIPL Arts. 38-40, Swiss Art. 47 BankA / revised FADP, Singapore PDPA s. 26, India DPDP Act s. 16). Streaming raw trade telemetry containing Personally Identifiable Information (PII)—such as trader IDs, client names, tax IDs, or account numbers—across national borders requires both a lawful transfer mechanism and data minimization. This module is the egress-control layer: it audits route policy, pseudonymizes PII fields, blocks prohibited routes, and records a decision audit trail.

**Scope caveat (important):** the masking performed here is *pseudonymization*, not anonymization. Pseudonymized data remains personal data under GDPR Art. 4(5), and keyed-hash de-identification is not PIPL "anonymization" (which requires irreversibility, PIPL Art. 73). This engine reduces exposure; it does not by itself make a transfer lawful — a Chapter V mechanism (adequacy Art. 45, SCCs Art. 46, derogations Art. 49) or PIPL Art. 38 mechanism (CAC security assessment, standard contract, certification) is still required.

## When NOT to Use

- **As a substitute for a lawful transfer mechanism.** SCCs, adequacy findings, or PIPL standard contracts are legal instruments this engine does not provide; route policies must be configured by a compliance officer based on those instruments.
- **As anonymization.** If data must exit a strict regime entirely (e.g. PIPL scope), true anonymization (irreversible, key destroyed) or aggregation is required — not keyed tokens.
- **For storage-residency decisions.** Where primary records must physically reside in-country (China CIIO/important data, India RBI payment data), use `data-localization-requirements-for-trade-records`; the two skills compose.
- **For vendor licensing / redistribution rules.** Use `data-vendor-contractual-usage-restriction-tracking`.

## Prerequisites

- Trade payload records containing PII and execution telemetry (`trader_id`, `client_name`, `account_number`, optional `tax_id`, `symbol`, `qty`, `price`).
- Jurisdiction policy mapping for origin and destination countries, configured by compliance (each route: `BLOCKED`, `REQUIRES_ANONYMIZATION`, or `ALLOWED_UNRESTRICTED`, with the governing framework named).
- Optional shared `tokenization_key` if pseudonyms must be joinable across engine instances (e.g. origin and destination sites both computing analytics).

## Workflow

1. **Jurisdiction Policy Audit**:
   - Query policy for `origin_country` $\to$ `destination_country` (codes normalized, e.g. `cn` $\to$ `CN`).
   - Route resolves to `BLOCKED`, `REQUIRES_ANONYMIZATION`, or `ALLOWED_UNRESTRICTED`.
   - Decision points: an **unregistered cross-border route defaults to `REQUIRES_ANONYMIZATION`** (default-deny — never silently unrestricted); an **invalid status string raises `ValueError`** at registration (fail-closed) so a typo cannot approve raw-PII egress; **an explicitly registered policy always wins**, including on a same-country route; the unmasked-domestic shortcut applies only when no policy is registered for `(origin, destination)`.
2. **PII Pseudonymization** (`REQUIRES_ANONYMIZATION` routes):
   - Tokenize `trader_id` $\to \text{HMAC-SHA256}_{key}(trader\_id)$ (or salted SHA-256 when no key is configured — never unsalted, which is dictionary-attack recoverable per EDPB Guidelines 01/2025).
   - Mask `client_name` $\to \text{ANONYMOUS\_CLIENT}$.
   - Redact `account_number` $\to \text{XXXX-XXXX-1234}$ (last 4 only; $\le 4$ chars $\to$ `****`).
   - **Drop `tax_id` entirely** — no partial tax identifier may survive.
3. **Egress Interception & Audit Report**:
   - Every decision returns a `DataTransferAuditReport` (`transfer_approved`, `applied_anonymization`, `sanitized_payload`, `audit_message`, UTC `timestamp`) and appends an entry to `engine.audit_trail`, which returns per-entry copies so a caller cannot rewrite a recorded decision. Blocked transfers return `sanitized_payload=None` with `transfer_approved=False` — nothing is raised for a blocked route; malformed input (empty/None country codes, wrong payload type) raises `ValueError`/`TypeError` before any policy decision.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating Pseudonymization as a Transfer Mechanism**: Masking PII before export does not legalize the transfer — pseudonymized data is still personal data (GDPR Art. 4(5), Recital 26) and de-identified — not anonymized — data under PIPL Art. 73. Route status must reflect a real legal mechanism (e.g. adequacy/SCCs, CAC standard contract).
- **Unsalted/Unkeyed Hashing**: `SHA256(trader_id)` is trivially reversible by dictionary attack because trader IDs have low entropy; EDPB Guidelines 01/2025 require keyed (HMAC) or salted hashing with the secret kept outside the pseudonymization domain.
- **Transmitting Raw PII to Global Cloud Aggregators**: Sending raw trader names and client account numbers from local execution servers in China or Switzerland to a centralized US AWS S3 bucket (Swiss Art. 47 BankA criminalizes disclosure of client-identifying data abroad without consent).
- **Fail-Open Policy Configuration**: A typo'd route status (e.g. `REQUIERS_ANONYMIZATION`) must abort registration, not fall through to an unrestricted transfer — this engine rejects unknown statuses with `ValueError` rather than guessing.
- **Same-Country Routes Bypassing Configured Policy**: An `origin == destination` shortcut that runs before the policy lookup silently discards a registered same-country `BLOCKED` or `REQUIRES_ANONYMIZATION` rule and releases raw PII. Intra-country restrictions are real (entity-to-entity disclosure limits, PIPL Art. 40 domestic-storage obligations), so an explicitly configured policy must take precedence over the domestic default.
- **Mutable Audit Records**: Returning the audit trail as a shallow list copy leaves the entry dicts aliased, so a caller can rewrite a `BLOCKED` decision into an approval after the fact. Copy each entry, not just the list.
- **Assuming Internal Transfers are Exempt**: Moving data between subsidiary legal entities across borders still triggers cross-border transfer rules (GDPR Art. 44 covers transfers "irrespective of whether or not ... repetitive, ... to a third country").
- **Uncritical `ALLOWED_UNRESTRICTED`**: e.g. UK$\to$US "unrestricted" presumes the recipient is certified under the UK Extension to the EU-US Data Privacy Framework (in force 12 Oct 2023); otherwise the ICO IDTA/Addendum is required, and EU$\to$US DPF adequacy remains subject to pending CJEU review.

## Verification

- Instantiate `CrossBorderTradeDataGovernanceEngine`. Configure policy: `CN` (China) $\to$ `US` requires PII pseudonymization, `CH` (Switzerland) $\to$ `US` blocks. Submit a trade payload from `CN` to `US` containing client name "John Doe", trader ID "TRADER_99", and tax ID. Verify output payload has masked client name, `TRD_HASH_`-prefixed keyed token, and no tax ID. Submit the Swiss transfer and verify `transfer_approved=False`, `sanitized_payload=None`, and a `BLOCKED` audit-trail entry. Register a policy with a typo'd status and verify `ValueError`.
- Verify tokenization is not plain hashing: the token must not equal `TRD_HASH_` + unsalted `SHA256(trader_id)` truncated to the token length (16 hex chars).
- Register a same-country `BLOCKED` policy (`CN` to `CN`) and verify the transfer is blocked rather than approved by the domestic shortcut.
- Mutate an entry returned by `engine.audit_trail` and verify the engine's own record is unchanged.
- Run `python -m unittest discover -s skills/cross-border-data-transfer-restrictions-for-trade-data/scripts`.

## Related Skills

- `data-localization-requirements-for-trade-records`
- `data-vendor-contractual-usage-restriction-tracking`
- `data-retention-policy-and-storage-tiering`

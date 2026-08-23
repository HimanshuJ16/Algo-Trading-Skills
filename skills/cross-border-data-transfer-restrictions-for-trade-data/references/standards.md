# Standards for Cross-Border Data Transfer Restrictions

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Zero Unmasked Cross-Border PII | PII fields MUST NOT cross international borders on restriction-bearing routes without tokenization/masking. This is a data-minimization floor, not a lawful-transfer mechanism (see below). |
| Keyed Pseudonymization | Trader IDs MUST be pseudonymized with keyed HMAC-SHA256 (shared secret) or salted SHA-256 where the salt/secret is kept outside the exported data. Unsalted hashing is prohibited: low-entropy IDs are dictionary-attack recoverable (AEPD-EDPS joint paper on hash functions as a pseudonymisation technique, 30 Oct 2019 — a final published paper; and EDPB Guidelines 01/2025 on Pseudonymisation, issued for public consultation 17 Jan - 14 Mar 2025, final adoption status unconfirmed as of Aug 2026 — treat as draft guidance, not settled law). |
| Tax ID Handling | Tax identifiers MUST be dropped entirely on anonymization routes — last-4 partial masking is not acceptable for tax IDs. |
| Fail-Closed Policy Validation | Route policy statuses outside {`ALLOWED_UNRESTRICTED`, `REQUIRES_ANONYMIZATION`, `BLOCKED`} MUST be rejected at registration; unknown routes MUST default to `REQUIRES_ANONYMIZATION`. |
| Audit Trail Integrity | Audit entries MUST NOT be mutable through the accessor that exposes them: return per-entry copies (or an immutable mapping), so a recorded decision cannot be rewritten after the fact. |
| Explicit Policy Precedence | A registered route policy MUST take precedence over implicit defaults, including the same-country/domestic shortcut. A configured `CN`->`CN` `BLOCKED` rule must block, not fall through to an unmasked domestic approval. |
| Audit Logging | 100% of transfer decisions (approved / pseudonymized / blocked / domestic) MUST produce timestamped audit-trail entries retrievable via `engine.audit_trail`. |

## Regulatory Anchors (verify currency before relying on them)

Pseudonymization is risk reduction, not a transfer basis: pseudonymized data remains
personal data under GDPR and de-identified (not anonymized) data under PIPL, so a
lawful transfer mechanism is required in addition to masking.

| Jurisdiction / Regime | Provision | Cross-Border Effect |
|---|---|---|
| EU GDPR | Arts. 44-49 (Chapter V), Art. 4(5), Recital 26 | Transfers to non-adequate countries need Art. 46 safeguards (SCCs/BCRs) or Art. 49 derogations. EU-US Data Privacy Framework adequacy (Decision (EU) 2023/1795) in force; upheld by General Court 3 Sep 2025 (T-553/23, *Latombe*); appealed to the CJEU 31 Oct 2025 (Case C-703/25 P), still pending as of Aug 2026 — monitor. The General Court assessed only the position as at the adequacy decision's adoption and expressly declined to rule on post-2023 developments. |
| UK (UK GDPR) | UK Extension to EU-US DPF ("data bridge"), in force 12 Oct 2023 | UK->US unrestricted only for recipients certified under DPF + UK Extension; otherwise ICO IDTA or Addendum to EU SCCs. |
| China PIPL | Arts. 38-40, 55; Art. 4 & 73(4) | Cross-border provision needs CAC security assessment, certification, or standard contract; separate consent (Art. 39); CIIOs/large handlers store domestically (Art. 40). Truly anonymized (irreversible) data exits PIPL scope; keyed hashes do not. March 2024 CAC Provisions on Cross-Border Data Flows eased volume thresholds — check current thresholds. |
| Switzerland | Art. 47 Banking Act (BankA, SR 952.0); revised FADP (in force 1 Sep 2023) | Art. 47 criminalizes disclosing client-identifying bank data abroad (even account existence) without consent; FADP separately requires adequacy or safeguards for disclosure abroad. |
| Singapore | PDPA s. 26; PDP Regulations 2021, Regs. 10 & 13 (in force 1 Feb 2021) | Transfer only where recipient is bound by legally enforceable obligations giving a comparable standard of protection. |
| India | DPDP Act 2023 s. 16; DPDP Rules 2025 (notified 13 Nov 2025), Rule 15 | Negative-list model: transfers permitted except to countries notified by the Central Government (no list notified as of Aug 2026). Sectoral rules persist — e.g. RBI Storage of Payment System Data directive (6 Apr 2018) mandates domestic storage of payment system data. |

Sources: GDPR (Regulation (EU) 2016/679) text; AEPD-EDPS 2019 paper on hash functions (edps.europa.eu); EDPB Guidelines 01/2025 on Pseudonymisation (consultation draft, edpb.europa.eu);
PIPL of the PRC (effective 1 Nov 2021, DigiChina translation); Swiss BankA SR 952.0 and FADP
SR 235.1 (fedlex); Singapore Statutes Online (PDPA 2012, PDP Regs 2021); India MeitY DPDP
Rules 2025. Dates and status verified August 2026.

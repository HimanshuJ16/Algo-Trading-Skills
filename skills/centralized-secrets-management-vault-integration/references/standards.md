# Standards for Centralized Secrets Management (Vault)

## 0. How to read this document

Section 1 is a **regulatory touchpoint**, with the jurisdiction stated. Sections 2-5 are
**vendor documentation** — HashiCorp's documented product behaviour, which is a fact about
the tool, not a legal requirement. Section 6 is **this repository's engineering standard**:
recommended practice, labelled as such so an agent does not present it to an operator as a
compliance mandate.

**No regulator named here mandates HashiCorp Vault, or any named product.** What the rules
address is access restriction, traceability, and confidentiality of credentials. Vault is
one way to satisfy those; a cloud KMS or an HSM-backed store is another. Do not tell an
operator that a regulation requires Vault.

## 1. EU / UK — MiFID II RTS 6, Article 18 (Security and limits to access)

**Applicability:** investment firms engaged in algorithmic trading authorised under MiFID
II (Directive 2014/65/EU). The UK operates a materially equivalent onshored version
supervised by the FCA. It does **not** bind a US-only broker-dealer, a non-EU proprietary
trader, or an individual trading their own capital.

| Requirement in Art. 18 | Bearing on this skill |
|---|---|
| Physical and electronic security arrangements that minimise the risk of attacks against information systems and ensure the **confidentiality, integrity, authenticity and availability** of data | A credential in a `.env` file or a container image satisfies none of these properties. Centralised storage with per-workload authentication is the direct response. |
| Identify all persons with **critical user access rights**, restrict their number, and **monitor their access to IT systems to ensure traceability at all times** | This is the reason an AppRole must belong to *one* workload rather than being shared: an audit trail that says "the shared bot role read the key" identifies nothing. Vault's audit device is what makes reads traceable. |
| Prompt notification of material security breaches to the competent authority | A recorded, per-workload read trail is what makes the blast radius of a compromised host answerable at all. |
| Annual penetration testing and vulnerability scanning | Credentials recoverable from a process image or a log file are a routine finding; `SecretBundle`'s redaction and the "no plaintext to disk" rule below are aimed at it. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>; UK onshored text
<https://www.legislation.gov.uk/eur/2017/589>.

## 2. Vault AppRole — documented behaviour that changes client design

Source: <https://developer.hashicorp.com/vault/api-docs/auth/approle>.

| Parameter / field | Documented behaviour | Consequence |
|---|---|---|
| `POST auth/approle/login` with `role_id`, `secret_id` | Returns `auth.client_token`, `auth.accessor`, `auth.lease_duration`, `auth.renewable`, `auth.token_policies` | `lease_duration` is the only reliable statement of how long the token is good for; a client that ignores it is guessing. |
| `secret_id_ttl` | The SecretID expires after this duration | Re-login can fail permanently even though RoleID and SecretID are unchanged. |
| `secret_id_num_uses` | The SecretID expires after this many logins | With the recommended value of `1`, a process gets exactly one login. Unattended re-authentication after max TTL is then impossible by design. |
| `token_ttl` / `token_max_ttl` | Incremental and maximum lifetime of issued tokens | The max TTL is the hard stop that renewal cannot cross. |
| `secret_id_bound_cidrs` / `token_bound_cidrs` | Restrict which source addresses may log in / use the token | A stolen SecretID is useless off the trading host. |

HashiCorp's recommended AppRole pattern
(<https://developer.hashicorp.com/vault/tutorials/recommended-patterns/pattern-approle>)
states the RoleID and SecretID should never be together except on the workload that
consumes them, recommends `secret_id_num_uses=1`, and recommends delivering the SecretID
**response-wrapped** rather than in plaintext. Shipping both from the same CI job as two
environment variables is named as an anti-pattern.

## 3. Vault tokens — TTL, max TTL, renewal

Source: <https://developer.hashicorp.com/vault/docs/concepts/tokens>.

- Every non-root token has a TTL measured from creation or last renewal.
- A token **cannot be renewed past its max TTL**; at renewal, lifetime since creation is
  compared against the maximum. Once reached, the client must re-authenticate.
- **Periodic tokens** are the alternative for long-running services: each renewal resets
  the TTL to the configured period, and the token never expires as long as it is renewed
  in time. If your bot cannot obtain a fresh SecretID unattended, a periodic token is the
  mechanism to discuss with the Vault operators.
- **Batch tokens are not renewable** and have a fixed TTL. Do not issue batch tokens to a
  long-lived trading process.
- When a token expires it and its associated leases are revoked; there is no grace period.

## 4. Vault KV v2 — read semantics

Source: <https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2>.

- Read path is `GET {mount}/data/{path}`; the response nests the values under `data.data`
  and version information under `data.metadata` (`version`, `created_time`,
  `deletion_time`, `destroyed`). A KV v1 mount has no such envelope.
- `metadata.version` increments on every write, which is the cheapest available signal
  that a secret was rotated.
- A **soft-deleted** version stops being returned by reads: the response carries
  `data: null` with a populated `deletion_time`. Deleting the latest version does not roll
  reads back to the previous one.

## 5. Vault HTTP status codes and quotas

Source: <https://developer.hashicorp.com/vault/api-docs> and
<https://developer.hashicorp.com/vault/docs/concepts/resource-quotas>.

| Status | Documented meaning | Correct client response |
|---|---|---|
| 403 | "Forbidden, your authentication details are either incorrect, you don't have access to this feature…" | Ambiguous between an expired/revoked token and a policy denial. Re-authenticate **once**; if it recurs, it is the policy. |
| 404 | "Invalid path. This can both mean that the path truly doesn't exist or that you don't have permission to view a specific path." | Never report this to an operator as "the secret is missing". Check the policy first. |
| 429 | Too many requests — a rate-limit quota was exceeded. Rate-limit quotas are available in Vault Community Edition, not Enterprise-only. | Back off. Do not retry in a tight loop. |
| 503 | Vault is sealed, down for maintenance, or overloaded | Sealed Vault is an operator event; a client cannot resolve it by retrying quickly. |

ACL policy path matching (<https://developer.hashicorp.com/vault/docs/concepts/policies>):
`*` is only valid as the **last character** of a path and matches everything beyond that
point; `+` matches exactly one path segment; `path "secret/data/prod/x"` matches that exact
path only, and `path "secret/data/prod/x/*"` does **not** grant the parent path itself. The
`deny` capability always wins.

## 6. Engineering standards (this repository's recommendation, not regulation)

| Standard | Rationale |
|---|---|
| Source code and images contain no API keys, including for testnet and paper environments | A testnet key still identifies your firm and is routinely reused when a bot is promoted to live. |
| Secrets live in process memory only — never written to disk, a local cache file, or a container volume | Disk survives the process, gets snapshotted, and ends up in a backup nobody scoped. |
| Every cached secret carries an expiry | An unbounded cache converts a completed rotation into an unbounded window of stale credentials. Bound it explicitly and let the rotation path invalidate it. |
| One AppRole per workload, scoped to the paths that workload reads | Anything broader makes the audit trail unable to answer "which process read this key". |
| A bot trading Binance must not have read access to the Kraken path | Least privilege, and the difference between one compromised venue and all of them. |
| Log formatters mask known secret material; secret containers redact in `repr` | Log pipelines fan out to places the security review never looked. See `structured-logging-for-post-incident-forensics`. |
| Re-authentication and retries are bounded | An unbounded retry against a permanently spent SecretID is a self-inflicted denial of service against Vault. |
| The client-side environment guard is defence in depth, never the boundary | The boundary is the Vault policy, enforced server-side, verifiable with `sys/capabilities-self`. |

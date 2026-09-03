# Standards for Secrets Rotation Without Bot Downtime

## 0. How to read this document

Sections 1–2 are **regulatory touchpoints**, each with its jurisdiction stated. Sections
3–5 are **protocol and vendor documentation** — documented behaviour of OAuth and of named
exchanges, which is a fact about the technology, not a legal requirement. Section 6 is
**this repository's engineering standard**: recommended practice, labelled as such so an
agent does not present it to an operator as a compliance mandate.

**No regulator named here mandates a rotation interval, a dual-credential overlap, or a
particular overlap duration.** What the rules address is access restriction, traceability,
and the confidentiality and availability of the firm's systems. Rotation is one way to
serve those ends. Do not tell an operator that a regulation requires them to rotate every
N days or to hold a fallback credential for N minutes — no cited source says so. A
five-minute overlap "MUST", in particular, has no basis in any standard.

## 1. EU / UK — MiFID II RTS 6, Article 18 (Security and limits to access)

**Applicability:** investment firms engaged in algorithmic trading, authorised under
MiFID II (Directive 2014/65/EU); RTS 6 supplements Article 17(1) of that Directive. The UK
operates a materially equivalent onshored version supervised by the FCA. It does **not**
bind a US-only broker-dealer, a non-EU proprietary trader, or an individual trading their
own capital.

| Article 18 text | Bearing on rotation |
|---|---|
| 18(2): "set up and maintain appropriate arrangements for physical and electronic security that minimise the risks of attacks against its information systems and that includes effective identity and access management. Those arrangements shall ensure the confidentiality, integrity, authenticity, and availability of data and the reliability and robustness of the investment firm's information systems." | Both halves matter here and they pull against each other. Confidentiality argues for revoking an exposed credential immediately; **availability and robustness** argue against a rotation procedure that drops the bot mid-session. The overlap window is how a firm serves both rather than trading one for the other. |
| 18(3): "promptly inform the competent authority of any material breaches of its physical and electronic security measures… indicating the nature of the incident, the measures taken following the incident and the initiatives taken to avoid similar incidents from recurring." | A compromised API key is the incident; the rotation record is the "measures taken". This is why the rotator retains an audit history and why a failed revocation must be recorded rather than swallowed. |
| 18(5): "identify all persons who have critical user access rights to its IT systems… restrict the number of such persons and… monitor their access to IT systems to ensure traceability at all times." | An orphaned credential — one still valid at the venue that no inventory tracks — is a direct failure of traceability. It is the reason `rotate()` refuses to proceed while an un-revoked previous credential is outstanding. |
| 18(4): annual penetration tests and vulnerability scans. | Long-lived, never-rotated API keys recovered from a host or a log are a routine finding. |

Primary text: Commission Delegated Regulation (EU) 2017/589 of 19 July 2016,
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>; Commission publication of the
adopted RTS, <https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160719-rts-6_en.pdf>;
UK onshored text, <https://www.legislation.gov.uk/eur/2017/589>.

## 2. United States — SEC Rule 15c3-5 (17 CFR 240.15c3-5)

**Applicability:** per paragraph (b), "a broker or dealer with market access, or that
provides a customer or any other person with access to an exchange or alternative trading
system through use of its market participant identifier or otherwise." A retail customer
trading their own account through a broker's API is **not** the regulated party — the
broker is. Do not cite this rule at an individual algo trader as though it bound them.

| Provision | Text | Bearing on rotation |
|---|---|---|
| (c)(2)(iii) | "Restrict access to trading systems and technology that provide market access to persons and accounts pre-approved and authorized by the broker or dealer" | A credential that should have been revoked and was not is unrestricted access by a party no longer authorised. Revocation, not the swap, is the control this provision is about. |
| (e)(1) | Review "no less frequently than annually… the business activity of the broker or dealer in connection with market access to assure the overall effectiveness of such risk management controls" | The credential inventory — which keys exist, which are live, which were revoked and when — is what makes this review answerable. |

Source: <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>. SEC staff FAQ:
<https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>.

## 3. OAuth refresh tokens — where the dual-credential pattern does not apply

Source: RFC 9700, *Best Current Practice for OAuth 2.0 Security*, January 2025,
Section 4.14.2. <https://www.rfc-editor.org/rfc/rfc9700>

Under refresh token rotation, "the authorization server issues a new refresh token with
every access token refresh response. The previous refresh token is invalidated". Replay
detection is built on exactly that invalidation: if both an attacker and the legitimate
client present the same refresh token, one of them presents an invalidated token, and the
authorization server "will revoke the active refresh token." Section 4.14.2 further
requires that "Authorization servers MUST utilize one of these methods to detect refresh
token replay by malicious actors for public clients."

The consequences for this skill are direct and non-negotiable:

- **There is no overlap window.** The old refresh token is dead the moment the new one is
  issued. A "fallback" refresh token is a token that is already invalid.
- **Falling back is actively harmful.** Presenting the superseded token is precisely the
  signal the authorization server treats as a breach, and its documented response is to
  revoke the active token too — converting a recoverable hiccup into a full loss of the
  grant, requiring interactive re-authentication during market hours.
- Therefore: **do not point `SecretsRotator` at an OAuth refresh token.** Use
  `upstox-oauth-refresh-token-rotation`, whose model is atomic persistence and
  single-flight refresh, not dual-credential overlap.

This constraint is a property of the grant type, not of any one broker; it applies wherever
the authorization server rotates refresh tokens, which RTS-6-scale brokers commonly do.

## 4. Venue behaviour that constrains the overlap

| Venue behaviour | Documented position | Consequence |
|---|---|---|
| **Multiple concurrent API keys** | Kraken supports multiple simultaneous keys per account, the permitted number depending on account verification level; Kraken Derivatives documents up to 50. Most REST/HMAC exchanges are comparable. | The dual-credential overlap is only possible where two keys can be valid at once. **Verify this for your venue before designing around it** — and verify the cap, because a rotation that cannot mint key N+1 without first deleting key N has no overlap available. |
| **Per-key nonce sequences** | Kraken's nonce window is a per-API-key setting that provides "a short time frame… during which API requests with an invalid nonce (a nonce value lower than a previously used nonce value), will not cause an invalid nonce error". | The nonce floor belongs to the key, not to the process. Falling back to a previous key resumes *that key's* sequence, and a counter that restarted from zero is rejected on every request. This is what `on_activate` exists for. |
| **Programmatic key creation** | Commonly unavailable: many venues gate API-key creation behind a 2FA-protected console. | "Automated rotation" usually means an operator mints the key and the bot performs the unattended swap and revoke. Confirm which half your venue can automate before promising a fully hands-off schedule. |

Sources: <https://support.kraken.com/articles/360000919966-how-to-create-an-api-key>,
<https://support.kraken.com/articles/360001163666-how-many-api-keys-can-i-generate->,
<https://support.kraken.com/articles/360022839451-how-to-create-an-api-key-for-kraken-derivatives>,
<https://docs.kraken.com/exchange/guides/general/subaccounts>.

## 5. What an HTTP status does and does not tell you

Do not instruct a bot to "automatically revert to the previous key on HTTP 401/403
response." That rule is unsourced, and as a blanket rule it is unsafe.

| Observation | Why an automatic revert can be wrong |
|---|---|
| `401` after a swap | Consistent with a bad new key — but equally with clock skew breaking HMAC signature validation, with an IP allowlist that was not updated for the new key, or with the venue not having propagated the key yet. Reverting treats every one of these as "the new key is bad". |
| `401` on the *old* key | If an operator already revoked it venue-side, the fallback target is dead. In-memory `is_valid` is this process's belief, not the venue's state; a revert can land on a credential that authenticates nowhere. |
| `403` | On many venues this is a permission or scope condition, or a WAF block — not an authentication failure. Reverting hides a key that was minted with the wrong permissions, which is a misconfiguration to fix, not to roll back past. |
| A single failure of any kind | One rejection is not evidence of a broken credential. |

The defensible rule is narrower: **fall back on a sustained, credential-attributable
authentication failure rate on the new key, with a threshold and observation window the
operator sets** — and treat the fallback as an incident requiring investigation, not as a
routine self-healing action. `SecretsRotator` deliberately does not decide this for you:
it exposes `fallback_to_previous()` and leaves the trigger to the caller, who is the only
party that can see the venue's actual error semantics.

## 6. Engineering standards (this repository's recommendation, not regulation)

| Standard | Rationale |
|---|---|
| A credential is never published to live order flow until something has proven it authenticates | The alternative is discovering the key is wrong from rejected orders. |
| A probe that fails to answer is treated as *unproven*, never as *proven bad or good* | A timeout says nothing about the credential. Both "swap anyway" and "conclude the key is broken" overstate the evidence. |
| Revocation means revocation **at the venue** | Forgetting a credential locally leaves it valid for anyone who has it. A revocation path that cannot fail is a revocation path that is not really revoking. |
| A failed revocation is loud and leaves the credential tracked | The failure mode to design against is a key that everyone believes is dead. |
| Every credential the firm has minted is either active, tracked as a fallback, or confirmed revoked | Anything else is an orphan, and orphans are what breach post-mortems find. |
| The overlap window is closed deliberately, never by timeout alone | In-flight requests signed with the old key must land. Drain what you can observe; time-bound what you cannot. |
| Rotations are serialised | Two rotations in flight against one credential pair will strand one of the credentials. |
| Durations use a monotonic clock | An NTP step during an overlap window must not open it early. |
| Credential containers redact in `repr` | Log pipelines and tracebacks fan out to places the security review never looked. See `structured-logging-for-post-incident-forensics`. |
| Rotation is scheduled outside volatile sessions where the strategy allows | Rotation is a change to a live system. See `deployment-freeze-windows-around-market-events`. |

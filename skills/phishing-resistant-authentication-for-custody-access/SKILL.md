---
name: phishing-resistant-authentication-for-custody-access
description: >-
  Use when the login in front of a custody portal or key-management console must resist
  adversary-in-the-middle phishing, enforcing WebAuthn origin and relying-party binding
  with single-use server-issued challenges.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: webauthn, fido2, phishing-resistant, crypto-custody, hardware-security-key, origin-binding, replay-protection
  brokers_frameworks: "W3C WebAuthn Level 3; FIDO2 CTAP 2.2; NIST SP 800-63B-4; 23 NYCRR Part 500"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or reviewing the authentication gate in front of a
custody portal, hot-wallet administration console, exchange API key management
screen, or signing-key operation. Traditional MFA — SMS, email OTP, TOTP,
push-without-number-matching — is defeated by adversary-in-the-middle reverse
proxies (Evilginx2 and successors), which relay a real login page and capture the
resulting session. FIDO2/WebAuthn defeats that class because the authenticator
signs over the origin the browser actually visited, and the proxy's origin is not
one the server accepts.

The engine here is a **Relying Party policy gate**, not a WebAuthn library. It
sits behind `py_webauthn` or `python-fido2` and enforces the server-side steps of
W3C WebAuthn Level 3 §7.2 that a library cannot do for you — the ones depending
on server state and firm policy: was this challenge issued by *this* server and
never used before, is this origin one *this* server serves, does this credential
belong to the user being claimed, has the signature counter gone backwards.

## When NOT to Use

- **As your WebAuthn implementation.** It performs no cryptography on the
  assertion and will not verify a signature. Passing `signature_verified=True`
  without a library behind it produces an authoritative-looking approval that
  checked nothing.
- **For the registration ceremony.** §7.1 verification, attestation, and
  AAGUID provenance are out of scope; an AAGUID is self-asserted unless you
  verify attestation.
- **As a transaction approval control.** WebAuthn authenticates a person, not a
  payload. Binding an approval to the exact transfer being approved is
  `multi-signature-approval-for-large-transfers`.
- **As a session control.** Session lifetime, re-authentication intervals and
  device binding belong in your session layer.
- **Across multiple processes without a shared store.** Challenge, credential
  and counter state is in-process; two workers with two challenge tables each
  accept the same replayed assertion once.

## Prerequisites

- A working WebAuthn verifier whose signature result you can pass in.
- Server-side configuration of `rp_id` and the exact `allowed_origins` you serve
  — from configuration, never from the assertion being verified.
- Credentials registered against their owning `user_id`, with the registration-
  time BE flag captured.
- At least two authenticators enrolled per user before enforcement; a
  device-bound credential is not resilient to device loss.

## Workflow

1. **Issue a server-side single-use challenge**: `issue_challenge(user_id)`
   returns 32 bytes of entropy bound to one user. A freshness window over a
   client-supplied timestamp is not replay protection — the attacker supplies
   the timestamp. Only equality against a stored, single-use, server-generated
   value is (WebAuthn L3 §13.4.3).
2. **Consume the challenge before anything else**: the challenge is spent on
   first use whatever the outcome, so one captured value cannot be probed
   repeatedly against different origins and flag combinations until something
   passes.
3. **Bind origin and RP ID to server expectations**: exact-match
   `clientDataJSON.origin` against `allowed_origins`, and `authenticatorData[0:32]`
   against SHA-256 of the configured `rp_id`. Never derive the expected origin by
   concatenating `https://` with an RP ID taken from the response — that lets
   attacker-controlled input define the expectation it is checked against.
4. **Bind the credential to the user**: reject an unknown credential, a revoked
   one, or one registered to a different account. Without this, any valid
   assertion from any enrolled key authenticates any claimed `user_id`.
5. **Apply flag policy**: UP and UV are separate §7.2 steps and separate
   failures — a missing touch is not a missing PIN, and conflating them
   misdirects the incident response. Reject the BE=0/BS=1 combination §6.1.3
   disallows, and reject a BE that contradicts registration, since BE never
   changes.
6. **Check the signature counter last, under the same lock as the state update**:
   a non-advancing counter is the specification's clone signal. §7.2 leaves the
   response to the RP; this engine rejects by default for custody.
7. **Defer state updates until every check passes**, then read `report.status`
   and `report.audit_notes` — not the truthiness of the report, which is a
   populated object on every rejection too.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling a timestamp check "replay protection"**: verifying that
  `now - assertion.timestamp < 60` proves nothing when the assertion — and its
  timestamp — is what the attacker replays. WebAuthn's anti-replay property comes
  from a server-generated challenge compared for equality and used exactly once.
  A future-dated timestamp under the naive check never expires at all.
- **Deriving the expected origin from the response**: computing
  `expected = "https://" + assertion.rp_id` and then checking membership of
  `rp_id` in an allowlist looks like origin binding, but the expectation is
  built from the input under test. The RP must compare against its own
  configured origin list (§13.4.9), which is also the only way multi-origin and
  related-origin deployments work — an RP ID may legitimately be a registrable
  suffix of the origin's domain.
- **Verifying an assertion without binding it to a user**: a signature that
  verifies proves someone holds *a* registered key, not that they hold *this
  account's* key. Without a credential→user record, an attacker with any enrolled
  authenticator authenticates as anyone.
- **Reporting the claim instead of the finding**: writing an assertion's
  self-declared `user_verified` into a rejected report's `is_user_verified` field
  puts attacker-supplied data into the column an auditor reads as established
  fact. A rejected assertion verified nobody.
- **Conflating user presence with user verification**: `UP=0` means no physical
  touch; `UV=0` means no PIN or biometric. Logging the first as
  `USER_VERIFICATION_FAILED` sends the investigation after the wrong failure.
- **Leaving a phishable fallback in place**: an SMS backup, a TOTP re-enrolment,
  or a help-desk reset that can register a new credential collapses the control
  to the strength of that path. The enrolment and recovery routes need protection
  equivalent to the login they guard.
- **Treating a syncable passkey as a hardware key**: a BE=1 credential lives in a
  vendor cloud account, which becomes part of your custody attack surface and is
  usually protected more weakly than the custody portal. Decide deliberately via
  `require_device_bound_credential`.
- **Citing regulators for requirements they did not write**: NYDFS §500.12
  requires MFA for any individual accessing any information system (since
  2025-11-01) but is technology-neutral and does **not** mandate phishing-resistant
  MFA. NIST SP 800-63B-4 defines phishing resistance at AAL3 but binds federal
  agencies, not private custodians.

## Verification

- Register a credential, issue a challenge, and verify a well-formed assertion
  from `https://custody.firm.com` with UP=1, UV=1 and an advancing counter ⇒
  `AUTH_SUCCESSFUL`, and confirm the stored counter advanced.
- Replay the same challenge a second time ⇒ `CHALLENGE_UNKNOWN`. Run eight
  threads against one challenge and confirm exactly one succeeds.
- Submit an otherwise perfect assertion from `https://cust0dy-firm.com` ⇒
  `ORIGIN_MISMATCH_PHISHING_ATTEMPT`, and confirm `is_user_verified` is `False`
  on that report despite the assertion claiming UV=1.
- Submit an assertion for a credential registered to another user ⇒
  `CREDENTIAL_USER_MISMATCH`; for a revoked credential ⇒ `CREDENTIAL_REVOKED`.
- Date a challenge 600s in the future ⇒ `CHALLENGE_EXPIRED` rather than
  indefinite acceptance; at exactly `max_challenge_age_sec` ⇒ still accepted.
- Submit UP=0 ⇒ `USER_PRESENCE_MISSING` (not `USER_VERIFICATION_FAILED`), and
  `signature_verified=False` ⇒ `SIGNATURE_NOT_VERIFIED`.
- Submit a counter that does not advance ⇒ `SIGN_COUNT_REGRESSION_CLONE_SUSPECTED`,
  and confirm a rejected assertion left the stored counter untouched.
- Configure `allowed_origins=()`, `"http://..."`, or a trailing slash ⇒
  `PhishingResistantAuthError` at construction rather than a silent lockout.
- Run `python -m unittest discover -s skills/phishing-resistant-authentication-for-custody-access/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `hardware-security-module-hsm-for-signing-keys`
- `multi-party-computation-mpc-custody-solutions`
- `multi-signature-approval-for-large-transfers`
- `segregation-of-duties-for-custody-operations`
- `employee-offboarding-procedure-for-custody-access`
- `emergency-manual-override-access-control`

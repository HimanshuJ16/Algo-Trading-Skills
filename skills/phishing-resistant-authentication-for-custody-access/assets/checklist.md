# Pre-Flight Checklist — Phishing-Resistant Authentication for Custody Access

Sign-off gate before a WebAuthn gate protects custody or signing-key access.
Every box is a check this skill's engine either enforces or explicitly leaves to
you. Cite `references/standards.md` for the specification step behind each.

## Verifier wiring

- [ ] A real WebAuthn library (`py_webauthn`, `python-fido2`, or the custodian's
      verifier) parses the response and verifies the COSE signature.
- [ ] `signature_verified` is populated from that library's result — never
      hard-coded to `True`, never defaulted away.
- [ ] `rp_id_hash` is passed as the raw `authenticatorData[0:32]` bytes, not hex
      or base64 text.
- [ ] `require_signature_verification` and `require_rp_id_hash` are left enabled,
      or the exception is documented and approved.

## Origin and RP ID binding

- [ ] `rp_id` and `allowed_origins` come from server configuration, never from
      the assertion being verified.
- [ ] `allowed_origins` lists every origin you actually serve, exactly — correct
      scheme, no trailing slash, no path.
- [ ] No subdomain wildcarding (WebAuthn L3 §13.4.8 code-injection risk).
- [ ] An assertion from a look-alike domain is rejected as
      `ORIGIN_MISMATCH_PHISHING_ATTEMPT` and pages security, not the user.

## Challenge and replay

- [ ] Challenges are generated server-side by `issue_challenge()`, at least 16
      bytes of entropy (§13.4.3).
- [ ] The client never chooses, caches, or reuses a challenge.
- [ ] A replayed challenge is rejected — verified by test, not by inspection.
- [ ] `max_challenge_age_sec` is calibrated and its basis recorded as firm
      policy, not cited as a regulatory number.
- [ ] Verifying and issuing hosts share a clock source; future-dated challenges
      are alerted on rather than tolerated.

## Credentials and users

- [ ] Every credential is registered against exactly one `user_id`.
- [ ] A credential belonging to another user cannot authenticate a claimed
      account (`CREDENTIAL_USER_MISMATCH`).
- [ ] Each user has **at least two** authenticators enrolled before enforcement.
- [ ] `revoke_credential()` is wired into offboarding and lost-key procedures,
      and fires at the same moment SSO access is cut.
- [ ] `allowed_aaguids` is either populated from the FIDO Metadata Service or
      deliberately left open, with the choice recorded.

## Flags and clone detection

- [ ] User presence (UP) is required; its failure is recorded as
      `USER_PRESENCE_MISSING`, distinct from a verification failure.
- [ ] User verification (UV — PIN or biometric) is required for custody actions.
- [ ] `require_device_bound_credential` reflects a decision on whether syncable
      passkeys are acceptable, given that the sync account joins your attack
      surface.
- [ ] Signature-counter regression has a defined response: rejection (default)
      or a monitored warning, with an owner for the resulting incident.

## Fallbacks — where these deployments usually fail

- [ ] No SMS, email OTP, or TOTP fallback can reach custody-privileged actions.
- [ ] Help-desk and self-service recovery cannot enrol a new credential without a
      control of equivalent strength.
- [ ] The enrolment path itself is protected — a phishable enrolment makes a
      phishing-resistant login decorative.

## Session and operations

- [ ] Session lifetime and re-authentication for privileged actions are enforced
      in the session layer (this engine does not do it); NIST SP 800-63B-4 AAL3
      guidance is 12h overall / 15min inactivity, adopted or departed from
      knowingly.
- [ ] Credential, challenge, and counter state is backed by a shared store if
      more than one process serves authentication.
- [ ] `audit_notes` from every decision is retained with access records.
- [ ] Applicable regulatory scope is confirmed with counsel — for a NY-chartered
      custodian, 23 NYCRR §500.12 requires MFA for any individual accessing any
      information system, but does not itself mandate phishing-resistant MFA.

# Workflows — phishing-resistant-authentication-for-custody-access

## 0. Wire the verifier first

This engine performs no cryptography on the assertion. Before it can protect
anything, a real WebAuthn library must parse the response and verify the
signature, and its result must reach `WebAuthnAssertion.signature_verified`.

```python
from phishing_resistant_authentication_for_custody_access import (
    AuthPolicyConfig,
    PhishingResistantAuthenticationForCustodyAccessEngine,
    WebAuthnAssertion,
    compute_rp_id_hash,
)

engine = PhishingResistantAuthenticationForCustodyAccessEngine(
    policy=AuthPolicyConfig(
        rp_id="custody.firm.com",
        allowed_origins=("https://custody.firm.com",),
        require_device_bound_credential=True,   # hardware keys only
    )
)
```

The defaults `signature_verified=False`, `user_present=False`,
`user_verified=False` exist so that an integration which forgets to populate
them fails closed. If you find yourself hard-coding `signature_verified=True`,
you have disabled the control rather than implemented it.

## 1. Registration (enrolment)

Registration is verified by your library against WebAuthn §7.1; this engine only
records the outcome. Capture the BE flag as it was at registration — §6.1.3 says
BE never changes, so it is a durable fingerprint of the credential.

```python
engine.register_credential(
    credential_id=credential.id,          # base64url credential ID
    user_id="custody_admin_1",
    sign_count=auth_data.sign_count,
    aaguid=str(auth_data.attested_credential_data.aaguid),
    backup_eligible=auth_data.flags.be,
)
```

Re-registering an existing `credential_id` against a different user raises,
because silently re-binding a credential transfers custody access.

**Enrol at least two authenticators per user before enforcing.** A single
device-bound credential is not resilient to device loss (§6.1.3), and the
recovery path you improvise at 2am under pressure will be weaker than the
control you are deploying.

## 2. Issue a challenge — server side, per attempt

```python
issued = engine.issue_challenge(user_id="custody_admin_1")
# send issued.value to the browser as PublicKeyCredentialRequestOptions.challenge
```

Never let the client choose, cache, or reuse a challenge. The value is
single-use and bound to one user; presenting user A's challenge for user B is
`CHALLENGE_USER_MISMATCH`.

## 3. Verify the assertion

```python
report = engine.verify_assertion(
    WebAuthnAssertion(
        user_id="custody_admin_1",
        credential_id=credential.id,
        client_origin=client_data.origin,
        challenge=client_data.challenge,
        rp_id_hash=authenticator_data[0:32],      # raw bytes, not hex
        client_data_type=client_data.type,
        user_present=auth_data.flags.up,
        user_verified=auth_data.flags.uv,
        signature_verified=library_verified_signature,   # from your library
        sign_count=auth_data.sign_count,
        backup_eligible=auth_data.flags.be,
        backup_state=auth_data.flags.bs,
        aaguid=aaguid_string,
    )
)
if not report.is_authenticated:
    deny(report.status, report.audit_notes)
```

Check `report.is_authenticated`, not the truthiness of the report object. Every
rejection is a populated report, and `if report:` is true for all of them.

### Order of checks, and why it matters for the audit trail

1. Engine enabled → `ENGINE_DISABLED`
2. `C.type` is `webauthn.get` → `CLIENT_DATA_TYPE_INVALID`
3. Challenge issued, unused, right user, fresh → `CHALLENGE_*`
4. Origin in the allowlist → `ORIGIN_MISMATCH_PHISHING_ATTEMPT`
5. `rpIdHash` matches the expected RP ID → `RP_ID_HASH_MISMATCH`
6. Credential known, not revoked, belongs to this user → `CREDENTIAL_*`
7. Authenticator model allowed → `AUTHENTICATOR_NOT_ALLOWED`
8. Backup flags sound and permitted → `BACKUP_STATE_INVALID`, `DEVICE_BOUND_CREDENTIAL_REQUIRED`
9. UP set, then UV if required → `USER_PRESENCE_MISSING`, `USER_VERIFICATION_FAILED`
10. Signature verified → `SIGNATURE_NOT_VERIFIED`
11. Counter advanced → `SIGN_COUNT_REGRESSION_CLONE_SUSPECTED`
12. State update, deferred until everything above passed

The challenge is consumed at step 3 regardless of what happens later, so one
captured challenge cannot be replayed against different origins or flag
combinations until something passes. Origin and RP ID binding are evaluated
before the flag checks so that a phished assertion is recorded as phishing
rather than as a missing PIN — the two demand very different incident responses.

## 4. Respond to each rejection class

| Status | What it means | Response |
|---|---|---|
| `ORIGIN_MISMATCH_PHISHING_ATTEMPT` | An assertion was produced at an origin you do not serve | **Security incident.** Someone is running a proxy against your users. Page the security on-call; do not surface a retry prompt. |
| `RP_ID_HASH_MISMATCH` | Authenticator signed over a different RP ID, or the field was not wired | Treat as the above unless you can prove it is an integration bug |
| `CHALLENGE_UNKNOWN` | Never issued, or already consumed | Replay or an expired UI. One retry with a fresh challenge; alert on repetition |
| `CHALLENGE_EXPIRED` | Too old, or dated beyond skew in the future | Re-issue. Persistent future-dating means your clocks disagree — fix NTP |
| `CREDENTIAL_USER_MISMATCH` | Valid key, wrong claimed account | Security incident; a credential authenticates its own owner only |
| `CREDENTIAL_REVOKED` | Offboarded or lost key still in use | Security incident; someone kept a key they should not have |
| `SIGN_COUNT_REGRESSION_CLONE_SUSPECTED` | Counter did not advance | Possible clone. Suspend the credential, contact the holder, re-enrol |
| `BACKUP_STATE_INVALID` | Impossible or changed BE/BS | Unsound authenticator data or a substituted credential; re-enrol |
| `DEVICE_BOUND_CREDENTIAL_REQUIRED` | Syncable passkey where hardware is required | Policy failure, not an attack. Enrol a hardware key |
| `USER_PRESENCE_MISSING` / `USER_VERIFICATION_FAILED` | No touch / no PIN or biometric | User-facing retry |
| `SIGNATURE_NOT_VERIFIED` | Your verifier did not run or did not pass | Integration defect. Never "fix" it by defaulting the field to True |

## 5. Operate it

- **Purge**: `purge_expired_challenges()` runs automatically on each issuance,
  and can be called on a timer for a long-idle process.
- **Revoke on offboarding**: `revoke_credential()` at the same moment SSO access
  is cut, not at the end of the week. See `employee-offboarding-procedure-for-custody-access`.
- **Deterministic clocks**: pass `now=` to `issue_challenge` and
  `verify_assertion` in tests and replays so decisions are reproducible.
- **Shared state**: registered credentials, issued challenges and counters live
  in-process behind a lock. Two workers with two challenge tables will each
  accept the same replayed assertion once. Back this with Redis, your session
  store, or a database before running more than one process.
- **Log the report**: `audit_notes` is written to be readable months later by
  someone reconstructing an incident. Retain it with your other access records.

## 6. What this does not protect

Origin binding stops credential phishing. It does not stop:

- **Session theft after authentication.** An attacker who steals the session
  cookie you issue never touches WebAuthn again. Bind sessions to the device and
  re-authenticate before each privileged custody action.
- **Malware on an authenticated endpoint.** The user touches the key for an
  operation the malware chose.
- **A weak fallback factor.** A help-desk reset or an SMS backup that can
  register a new credential collapses the entire control to the strength of that
  path.
- **Approval of the wrong transaction.** WebAuthn authenticates a *person*, not a
  payload. Binding the approval to what is being approved is
  `multi-signature-approval-for-large-transfers`.

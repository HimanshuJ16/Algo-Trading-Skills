---
name: secrets-rotation-without-bot-downtime
description: >-
  Use when a live trading bot must swap an exchange API key or database
  credential without restarting — pre-swap validation, an atomic hot-swap under
  a lock, a lease-gated overlap window so in-flight requests still land, a
  fallback that is distinct from a validation refusal, and a revocation that
  actually calls the venue and reports failure when it does not.
domain: DevSecOps & High-Availability Operations
subdomain: Zero-Downtime Secret Rotation
tags:
- secrets-rotation
- zero-downtime
- hot-swap
- dual-credential-overlap
- credential-revocation
- thread-safety
- bot-reliability
brokers_frameworks:
- Exchange REST API keys
- HMAC-signed venue APIs
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a **long-running trading process** must move from one API credential
to another and cannot be restarted to do it — restarting drops market data, abandons
in-flight orders, and leaves positions unhedged across the gap.

`SecretsRotator` covers the unattended half of that procedure:

1. **Probe** the candidate credential while the old one is still carrying order flow, so
   nothing unproven reaches the venue.
2. **Publish** it atomically under a lock, because the trading loop reads the credential
   from another thread.
3. **Overlap** — the outgoing credential stays available as a fallback, and requests
   already in flight against it keep it un-revokable until they land.
4. **Revoke** it at the venue, and say so loudly when the venue did not confirm.

The design assumption is a bot that boots once and runs for days, where the credential is
read thousands of times by threads that know nothing about rotation.

## When NOT to Use

- **The credential is an OAuth refresh token.** This is a hard stop, not a preference.
  Refresh-token rotation invalidates the outgoing token at the moment the new one is
  issued, and presenting the superseded token is exactly what replay detection punishes —
  RFC 9700 §4.14.2 has the authorization server revoke the *active* token on detection.
  There is no fallback to hold, and reaching for one converts a hiccup into a lost grant
  and an interactive re-auth during market hours. Use
  `upstox-oauth-refresh-token-rotation`.
- **The venue permits only one live key at a time,** or the account is at its key cap so
  the old key must be deleted before the new one can be minted. Without two concurrently
  valid credentials there is no overlap, and this reduces to a restart with extra steps.
- **You are containing a suspected compromise.** The overlap exists to make *scheduled*
  change graceful. When a key is believed leaked, revoke first and accept the downtime.
  See `post-incident-forensics-for-suspected-key-compromise`.
- **You need the credential fetched, not rotated.** Retrieval, caching, and staleness
  bounds belong to `centralized-secrets-management-vault-integration`.
- **You are deciding when and how often to rotate.** Schedule policy and cryptoperiods are
  `key-rotation-schedule-for-hot-wallet-keys`.
- **You are auditing what a key is permitted to do.** That is
  `api-key-least-privilege-audit-tool`.
- **The secret must never exist in process memory.** This module holds plaintext. For keys
  that must not leave a boundary, move the operation to the key —
  `hardware-security-module-hsm-for-signing-keys`.

## Prerequisites

- A venue that permits **two credentials valid simultaneously**, verified against its own
  documentation. Confirm the per-account key cap too: a rotation that must delete key N to
  mint key N+1 has no overlap available.
- A `validate_fn` that genuinely probes the venue and **exercises a permission the strategy
  needs**. A read-only probe passes for a key minted without trading permission, which then
  fails on the first order.
- A `revoke_fn` that invalidates a credential **at the venue**. Without one,
  `revoke_previous()` can only forget the credential locally and reports
  `REVOCATION_FAILED` rather than pretending to succeed.
- Knowledge of any **per-key state the venue enforces** — an HMAC nonce floor above all.
  Kraken rejects a nonce below one already used with that key, so a fallback that resumes a
  key with a reset counter is refused on every request. That is what `on_activate` is for.
- An operator or orchestrator able to **mint the key**: many venues gate key creation behind
  a 2FA console with no API.
- Python 3.8+. No third-party package required.

## Workflow

1. **Confirm the pattern applies before writing code.** Two keys valid at once, headroom
   under the key cap, not an OAuth refresh token. If any of those fails, stop — see
   *When NOT to Use*.
2. **Seed and wire the loop.** `set_initial_credential(...)`, then have every request take
   the credential through `use()`:
   `with rotator.use() as cred: client.order(cred.key_id, cred.reveal(), ...)`. Reading
   `active_credential` directly works but is not leased, so a concurrent revocation cannot
   see that the credential is still on the wire.
3. **Rotate, and classify the outcome by more than truthiness.** `success=False` with
   `indeterminate=False` means the venue refused the key — a real bad credential. With
   `indeterminate=True` the probe never answered, so the credential is **unproven, not
   proven bad**: retry with backoff, do not force the swap, and do not delete the new key.
   In both cases the active credential is untouched and state is `VALIDATION_FAILED`.
4. **Do not treat `VALIDATION_FAILED` as a rollback.** It means nothing changed and no
   trading was affected. `ROLLED_BACK` means a credential was live on real order flow and
   failed. Route alerts off the two differently — that separation is the whole reason they
   are distinct states.
5. **Watch the new credential on real flow before revoking.** Do not wire a single `401` to
   an automatic revert: `401` is equally consistent with clock skew, an un-updated IP
   allowlist, or key propagation lag, and `403` is often a permission or WAF condition where
   reverting hides a misconfiguration. Fall back on a sustained, credential-attributable
   failure rate with a threshold you set.
6. **Drain before revoking.** `drain_previous(timeout=...)`, then `revoke_previous()`.
   Revocation refuses while leases are outstanding (`CredentialInUse`) or before
   `min_overlap_seconds` elapses (`OverlapWindowOpen`). The lease gate covers requests this
   rotator handed out; `min_overlap_seconds` covers users it cannot see — an authenticated
   websocket session being the usual one.
7. **Escalate a failed revocation; never loop on it.** `success=False` from
   `revoke_previous()` means the old credential **still authenticates at the venue**. That
   needs a human, not a retry.
8. **Finish the rotation.** Only `REVOKED_OLD` means done. Until then a credential is
   outstanding, which is why `rotate()` refuses a second rotation while an un-revoked
   previous credential exists — the alternative is a live key that nothing tracks.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Believing a credential is dead because the code said "revoked".** Marking
  `is_valid = False` in memory stops nothing; anyone holding the key still authenticates.
  Revocation happens at the venue or not at all, and this module reports
  `REVOCATION_FAILED` rather than letting the belief go unchallenged. Verify afterwards by
  using the old key once and confirming it fails.
- **Rotating twice before revoking.** The second rotation's fallback becomes the first
  rotation's new key, and the original credential is dropped from the rotator while still
  valid at the venue — an orphan that no inventory tracks and nobody will revoke. Refused
  by default; `force=True` only when the revocation is arranged elsewhere.
- **Two rotations firing at once.** A scheduled rotation and an operator's manual one is a
  real scenario, and the validation probe is a network call slow enough for them to overlap.
  Serialised here; the loser is refused, and someone should be watching for that.
- **Reverting on a single `401`.** It is equally consistent with clock skew breaking the
  signature, an IP allowlist not updated for the new key, or the venue not having propagated
  it yet. Worse, if an operator already revoked the old key venue-side, the revert lands on a
  credential that authenticates nowhere.
- **Falling back without restoring the nonce floor.** The floor belongs to the key, not the
  process. Resuming a previous key with a counter that restarted from zero is rejected on
  every request — at precisely the moment the fallback was supposed to save the session.
- **Revoking the instant the swap returns.** Requests signed a millisecond before the swap
  are still on the wire. The lease gate holds them; anything the rotator never leased — an
  authenticated websocket, a client object built at boot — is only covered by
  `min_overlap_seconds`, which defaults to 0 because no universal value is defensible.
- **Treating a validation timeout as a verdict.** It is not evidence the key is bad, and it
  is not permission to swap. It is an absence of evidence, reported as `indeterminate`.
- **A `validate_fn` that returns `True` unconditionally.** The one safety property that
  matters, disabled. Required explicitly here — `accept_without_validation` exists so the
  unsafe choice has a name and a log line.
- **Logging the credential.** A plain dataclass prints its secret into every traceback and
  log line that touches it. `Credential.__repr__` redacts; `reveal()` is the deliberate
  escape hatch.
- **Deleting the old key first to free a slot at the cap.** That is a restart with extra
  steps: the overlap is gone and any failure of the new key is unrecoverable.

## Verification

- `python -m unittest discover -s skills/secrets-rotation-without-bot-downtime/scripts`
  runs the suite (40 tests). It drives the rotator through validation refusal, an
  indeterminate probe, venue-refused revocation, lease-gated and time-gated overlap,
  fallback with and without `on_activate`, and concurrent rotation.
- Regression checks worth reading before trusting a change: `VALIDATION_FAILED` never
  reported as a rollback; a probe that raises leaving the state machine usable rather than
  stranded in `VALIDATING_NEW`; `revoke_previous()` reporting failure when the venue refuses;
  a second rotation refused rather than orphaning a live key; two concurrent rotations
  leaving exactly one winner; `repr` of `Credential` and `SecretsRotator` containing no
  secret material.
- Against the venue's sandbox, confirm what the code cannot: that the new key carries the
  permissions the strategy needs (place an order, do not just read), that the old key
  **actually fails** after `revoke_previous()` succeeds, and that a forced revocation failure
  surfaces as `REVOCATION_FAILED` rather than being swallowed.
- Verify no cached copy of the old credential outlives the swap — a client object built at
  boot, a connection pool, an env var read once — by exercising the bot after revocation and
  confirming nothing still authenticates with the old key.

## Related Skills

- `centralized-secrets-management-vault-integration`
- `upstox-oauth-refresh-token-rotation`
- `key-rotation-schedule-for-hot-wallet-keys`
- `api-key-least-privilege-audit-tool`
- `post-incident-forensics-for-suspected-key-compromise`
- `sandbox-credential-leakage-prevention`
- `blue-green-deployment-for-live-strategy-updates`

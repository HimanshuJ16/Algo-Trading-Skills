# Pre-Flight Checklist — Secret Rotation on a Live Trading Bot

Run this before rotating a credential on a bot carrying real orders. Anything unchecked in
**Section A** is a stop.

## A. Is this pattern even applicable? (stop conditions)

- [ ] The credential is **not** an OAuth refresh token. Refresh-token rotation invalidates
      the old token on issue, and replaying it is what triggers the authorization server to
      revoke the whole grant (RFC 9700 §4.14.2). There is no fallback to hold. Use
      `upstox-oauth-refresh-token-rotation`.
- [ ] The venue permits **two credentials valid at once** — confirmed against the venue's
      own documentation, not assumed.
- [ ] The account is **below its API-key cap**, so the new key can be minted without first
      deleting the old one. Deleting first destroys the overlap.
- [ ] Whether keys can be created and deleted **programmatically** at this venue is known,
      so it is clear which steps are automated and which need an operator.
- [ ] Per-key state the venue enforces (an HMAC **nonce floor** above all) is identified,
      and `on_activate` restores it. A fallback that resumes a key with a reset counter is
      rejected on every request.

## B. The validation probe

- [ ] `validate_fn` is a **real probe against the venue**, not a stub that returns `True`.
      If `accept_without_validation` is in use, someone decided that deliberately.
- [ ] The probe exercises a **permission the strategy actually needs**. A read-only probe
      passes happily for a key minted without trading permission, which then fails on the
      first order.
- [ ] The probe has a **finite timeout**, shorter than the rotation window.
- [ ] An `indeterminate=True` result is handled as *unproven* — retried with backoff — and
      not as either "swap anyway" or "the key is bad, delete it".

## C. The swap

- [ ] The trading loop takes credentials through **`use()`**, not by reading
      `active_credential` directly. Without the lease, revocation cannot see in-flight
      requests.
- [ ] Leases are held **per request**, not per session.
- [ ] The new key was minted with the **same permissions and IP allowlist** as the one it
      replaces.
- [ ] Nothing else in the process holds a **cached copy** of the old credential that the
      rotator does not know about — a client object constructed at boot, a connection pool,
      an env var read once. See `centralized-secrets-management-vault-integration`.
- [ ] Only **one rotation** can be in flight. A scheduled rotation and an operator's manual
      one firing together is a real scenario; the rotator refuses the second, and someone
      is watching for that refusal.

## D. Fallback

- [ ] The fallback trigger is a **sustained, credential-attributable** failure rate with an
      operator-set threshold — not a single `401`, and not any `403`.
- [ ] It is understood that the fallback target may be dead: if an operator already revoked
      the old key venue-side, `is_valid` in memory is this process's belief, not the
      venue's state.
- [ ] `ROLLED_BACK` **pages**; `VALIDATION_FAILED` does not. The two are distinct states
      precisely so the alerting can distinguish them.
- [ ] There is no automatic retry of a credential that was rolled back off.

## E. Revocation — the step that is actually the point

- [ ] `revoke_fn` is configured and **calls the venue**. Without it the rotator can only
      forget the credential locally, which it reports as `REVOCATION_FAILED`, not success.
- [ ] `drain_previous()` is called before revoking, with a finite timeout.
- [ ] `min_overlap_seconds` is set to the maximum request lifetime plus retry budget, or
      there is a written reason it is left at 0. It covers users the lease gate cannot see —
      an authenticated **websocket session** most often.
- [ ] A `success=False` result from `revoke_previous()` **escalates to a human**. It means
      the old credential still authenticates.
- [ ] Revocation is **verified at the venue**, not inferred from the return value: the old
      key is used once afterwards and confirmed to fail.

## F. Inventory and audit

- [ ] Every key the firm has minted is active, tracked as a fallback, or confirmed revoked.
      There is no fourth category.
- [ ] The rotation is not marked complete until state is `REVOKED_OLD`.
- [ ] `rotation_history` (or the caller's own audit sink) is captured somewhere that
      survives the process, so the "measures taken" after an incident are answerable.
- [ ] For EU/UK investment firms: the credential inventory supports RTS 6 Art. 18(5)
      traceability. For US broker-dealers: it supports the 15c3-5(e)(1) annual review.

## G. Leakage

- [ ] Printing or logging a `Credential` does **not** emit the secret — verified by
      actually printing it, not by reading the code.
- [ ] The secret is not written to disk, a cache file, a container volume, or a crash dump.
- [ ] Exception handlers do not echo request bodies that could carry the credential.
- [ ] `reveal()` call sites are few enough to enumerate.

## H. Timing

- [ ] The rotation is scheduled outside volatile sessions where the strategy allows it.
      See `deployment-freeze-windows-around-market-events`.
- [ ] For a **suspected compromise**, this checklist is the wrong one: revoke immediately
      and accept the downtime. See `post-incident-forensics-for-suspected-key-compromise`.
- [ ] The whole sequence has been rehearsed end to end against the venue's **sandbox**,
      including a forced revocation failure.

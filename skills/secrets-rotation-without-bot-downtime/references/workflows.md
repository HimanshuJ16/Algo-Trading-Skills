# Workflows for Secrets Rotation Without Bot Downtime

The rotator automates one span of a longer procedure. Steps 1 and 7 are usually operator
or orchestrator work, because most venues do not expose API-key creation or deletion to an
API. Know which half your venue automates before you promise a hands-off schedule.

## 0. Decide whether this pattern applies at all

Before anything else, answer three questions about the credential:

1. **Can two credentials be valid at the same time?** If the venue permits only one live
   key, or if issuing the new one invalidates the old (OAuth refresh-token rotation — see
   `references/standards.md` §3), there is no overlap to work with and this skill is the
   wrong tool. For OAuth refresh tokens use `upstox-oauth-refresh-token-rotation`.
2. **What is the key cap?** If the account is at its limit, the new key cannot be minted
   until an old one is deleted — which destroys the overlap. Free a slot first.
3. **Is there per-key state the venue enforces?** An HMAC nonce floor is the common case.
   It follows the key, not the process, and it is what `on_activate` restores.

## 1. Mint the new credential (operator / orchestrator)

Create the key at the venue with **the same permission set and IP allowlist as the one it
replaces**. A key minted with narrower permissions will validate against a read-only probe
and then fail on the first order — the probe must exercise a permission the strategy
actually needs. Do not delete the old key yet.

Record the new key in the credential inventory as `pending` before it is used anywhere.
A key that exists at the venue but in no inventory is already an orphan.

## 2. Pre-validate before switching anything

Call `rotate(new_key_id, new_secret)`. The probe supplied as `validate_fn` runs while the
**old** credential is still active, so nothing reaches live order flow unproven.

Classify the outcome by the result, not by whether it was falsy:

- `success=True` → the swap happened; the overlap is now open.
- `success=False, indeterminate=False` → the venue actively refused the credential. This is
  a bad key: check permissions, allowlist, and that the secret was copied whole.
- `success=False, indeterminate=True` → the probe never got an answer. **The credential is
  unproven, not proven bad.** Retry the probe with backoff. Do not respond by forcing the
  swap, and do not respond by deleting the new key.

In every failing case the active credential is untouched and no trading was affected; the
state is `VALIDATION_FAILED`, which is deliberately distinct from `ROLLED_BACK`.

## 3. Hot-swap and open the overlap

On success the new credential is published atomically and the outgoing one is retained as
the fallback. State is `SWAPPED`.

The trading loop must take credentials through `use()` for anything that makes a request:

```python
with rotator.use() as credential:
    client.place_order(credential.key_id, credential.reveal(), order)
```

The lease is what makes the overlap real. A request signed just before the swap keeps the
old credential un-revokable until it lands. Hold the lease for the request only — wrapping
a session in one lease blocks revocation for the life of the session.

At this point **both credentials are live at the venue.** That is the intended state, and
it is also a state you must not linger in: two live keys is twice the exposure, and the
window is the reason step 6 is not optional.

## 4. Observe the new credential on real flow

Watch authentication-attributable failures on the new key over a window the operator sets.
Do not wire a single `401` to an automatic revert — see `references/standards.md` §5 for
why `401` and `403` are both ambiguous, and why reverting on one rejection can land the bot
on a credential the operator already revoked.

## 5. Fall back only on evidence, and treat it as an incident

If the new credential is genuinely failing, `fallback_to_previous()` restores the previous
one and sets state `ROLLED_BACK`. If `on_activate` is configured it runs for the restored
credential, which is where a nonce floor is re-established — without it the venue rejects
every request signed with the resumed key.

`ROLLED_BACK` means a credential was live on real order flow and failed. It should page.
Investigate the failed key before retrying it; do not schedule an automatic re-attempt.

## 6. Drain, then revoke at the venue

This is the step that actually removes the old credential's access, and the step most
often skipped.

```python
rotator.drain_previous(timeout=30.0)   # let in-flight requests land
result = rotator.revoke_previous()
if not result.success:
    escalate(result.message)           # the old key is STILL LIVE
```

`revoke_previous()` calls `revoke_fn`, which must invalidate the credential **at the
venue**. It refuses while leases are outstanding (`CredentialInUse`) or while
`min_overlap_seconds` has not elapsed (`OverlapWindowOpen`). The lease gate covers requests
the rotator handed out; `min_overlap_seconds` covers users it cannot see — an authenticated
websocket session being the usual one. Set it to your maximum request lifetime plus retry
budget; there is no universal value, which is why the default enforces only the lease gate.

`REVOCATION_FAILED` — whether because `revoke_fn` raised or because none was configured —
means the old credential still authenticates. Escalate it. Do not retry in a loop, and do
not mark the rotation complete.

## 7. Close the inventory record

Mark the old key revoked with its timestamp, and the new key active. State `REVOKED_OLD`
is the only state in which the rotation is finished. Until then something is outstanding,
which is why `rotate()` refuses a second rotation while a previous credential is
un-revoked.

## 8. Scheduling

Rotate outside volatile sessions where the strategy permits it — a rotation is a change to
a live system, and the overlap is a window in which two keys are valid. Coordinate with
`deployment-freeze-windows-around-market-events`. An emergency rotation after a suspected
compromise inverts the trade-off: revoke immediately and accept the downtime, because the
whole point of the overlap is graceful scheduled change, not containment of an active
breach. For that path see `post-incident-forensics-for-suspected-key-compromise`.

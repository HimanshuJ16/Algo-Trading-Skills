# Pre-Flight Checklist — Multi-Signature Approval for Large Transfers

## Vault-side controls (do these first; this engine is the second layer)

- [ ] The vault itself enforces a threshold — on-chain multisig, HSM quorum policy,
      or the custodian's policy engine. This gate does not constrain an attacker
      who controls your code.
- [ ] No single key held by this system can move funds on its own.
- [ ] Signing key material for each signer sits on a different logical or physical
      device (CCSS v9 `1.05.9`).
- [ ] $N$ exceeds $M$ by at least one, so losing a single key does not strand the
      wallet (CCSS v9 `1.02.2`).

## Policy configuration

- [ ] `auto_approve_threshold_usd` is set to the largest single loss the firm will
      absorb without human review — or to `0.0` to remove the automation tier.
- [ ] `low_tier_allows_self_approval` is a deliberate decision, not a default.
- [ ] `high_value_threshold_usd`, the $M$-of-$N$ pairs and the distinct-role
      minimums are recorded as firm policy with a named owner. **No regulator
      prescribes these numbers.**
- [ ] `high_value_timelock_seconds` is long enough for a human to actually respond
      at 3am in the signer's timezone.
- [ ] Someone confirmed exactly `high_value_threshold_usd` falling in the *medium*
      tier (no timelock) is intended.

## Roster

- [ ] Every eligible signer is registered with `register_signer`, sourced from the
      identity provider rather than a hard-coded list.
- [ ] Roles are genuinely separate functions, not job titles on one desk.
- [ ] The eligible roster is at least as large as the $N$ the policy declares —
      check reports for the "N is not actually available" warning.
- [ ] Offboarding revokes a signer here at the same time as everywhere else
      (CCSS v9 `1.04.1`), and the grant/revoke action is itself logged with the
      identity of who performed it (`1.04.3`).

## Request path

- [ ] `current_time` comes from a trusted server clock on every call; nothing
      passes `request.creation_timestamp` as the evaluation clock.
- [ ] `asset_symbol`, `asset_quantity` and `chain` are populated above the low
      tier — otherwise the quorum is approving a USD valuation and the on-chain
      amount is unbound.
- [ ] Every request carries a `nonce` that is not reused for a different payload.
- [ ] `register_request` runs once per payload, and its anchor is persisted.
- [ ] Every registration notifies a human out of band from the system that created
      it. An unwatched window is a delay, not a control.
- [ ] Approvals carry `approved_digest`; `require_payload_binding` is left `True`.
- [ ] The approval channel is authenticated — the engine records *which* payload
      was approved, not *that* the signer approved it.
- [ ] At least one role verifies destination and amount out of band, on a separate
      device, against a separately-sourced address book, before approving.
- [ ] The caller checks `report.is_approved`, not merely the absence of an
      exception.
- [ ] `MultiSigApprovalError` propagates; it is never swallowed into a retry.
- [ ] `mark_executed(report.transfer_digest)` runs at submission, not at
      confirmation, and the executed set is persisted.

## Deployment

- [ ] Exactly one process owns the engine, or the roster, anchors, revocations and
      executed digests are backed by a shared store.
- [ ] Anchors and executed digests survive a restart.
- [ ] A revocation path exists and someone on call knows how to invoke
      `revoke_request` and `suspend_signer` inside the window.

## Verification before go-live

- [ ] Three unregistered signer ids do **not** form a quorum.
- [ ] Approvals collected for one destination do **not** authorise another.
- [ ] A `creation_timestamp` a billion seconds in the past still owes the full
      timelock.
- [ ] `current_time=0.0` is honoured as a real clock, not treated as missing.
- [ ] Three approvals sharing one role return `INSUFFICIENT_DISTINCT_ROLES`.
- [ ] Suspending an approving signer mid-window blocks the release.
- [ ] A revoked request stays revoked after a nonce bump.
- [ ] An executed digest returns `ALREADY_EXECUTED` on replay.
- [ ] `amount_usd` of `nan`, `inf`, `0.0`, `-1.0`, and a blank destination each
      raise rather than produce a decision.
- [ ] A test transaction has been sent and confirmed before the first large
      transfer to any new destination — see
      `test-transaction-verification-before-large-transfers`.

## Monitoring

- [ ] Alerts fire on `SIGNER_NOT_ON_ROSTER`, `ROLE_MISMATCH_WITH_ROSTER`,
      `APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD`, `APPROVAL_TIMESTAMP_IN_FUTURE`,
      and every `MULTISIG ANCHOR RESTORED` line.
- [ ] Every `REQUEST_REVOKED` and `suspend_signer` call pages a human.
- [ ] `report.rejected_approvals` is persisted with the decision, not just logged.

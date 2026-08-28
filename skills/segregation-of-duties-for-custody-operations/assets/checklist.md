# Pre-Flight Checklist — Segregation of Duties for Custody Operations

## Before this gate matters at all

- [ ] The **custodian's policy engine, HSM quorum, or on-chain multisig** enforces
      a threshold independently of this code. This engine is a governance gate
      inside your own process; an attacker who controls it skips it entirely.
- [ ] No single key or credential reachable by this system can move funds alone.
- [ ] The identity layer authenticates the caller. The engine trusts whatever
      `approver_id` it is handed — it verifies no signatures.

## Policy configuration (all of these are firm decisions, not regulation)

- [ ] `large_transfer_threshold_usd` is recorded as firm policy with a named
      owner. **No regulator prescribes a dollar threshold.**
- [ ] `approvals_below_threshold` and `approvals_at_or_above_threshold` are
      deliberate. Someone has confirmed that a transfer of *exactly* the
      threshold falling in the higher tier is intended.
- [ ] `min_distinct_approver_departments` is either 1 (accepted: two approvals
      from one desk count as two) or ≥ 2 **and** every tier's approval count is
      at least that large.
- [ ] `forbid_approver_from_initiator_department` is a deliberate choice, and
      `department` values come from HR/IdP rather than being typed per call.
- [ ] `clock=` is wired to a trusted server clock, not to the requester.

## Roster and roles

- [ ] Every identity is registered from the identity provider, not a hard-coded
      list, and `register_user` is the only path that grants a role.
- [ ] The role-conflict matrix in use is a deliberate choice between
      `DEFAULT_INCOMPATIBLE_ROLE_PAIRS` and `STRICT_INCOMPATIBLE_ROLE_PAIRS`.
- [ ] Under the default matrix, someone has accepted that `INITIATOR` +
      `APPROVER` is permitted, relying on the per-proposal self-approval block.
- [ ] Nobody's `UserIdentity` object is mutated after registration and expected
      to take effect — the engine holds a `frozenset` snapshot.
- [ ] Every role change goes through `replace=True` and is reviewed, not
      applied by editing `engine.users` in place.
- [ ] Access privileges are reviewed at least annually and revoked promptly on
      departure (23 NYCRR 500.7 if you are a NYDFS covered entity), wired to
      `employee-offboarding-procedure-for-custody-access`.
- [ ] Roles map to genuinely separate functions, not to job titles on one desk.
- [ ] `user_id` values are canonical and unique per human. The engine compares
      them **exactly**: registering both `alice` and `Alice` creates two
      identities, and the second can approve the first's proposal without
      tripping the self-approval block. Normalise at the identity provider.

## Per-transfer

- [ ] The destination address was verified **out of band** by a checker, not
      just read off the same screen the maker used. A valid quorum signing a
      falsified presentation is how $1.46bn left Bybit in February 2025.
- [ ] Callers read `refresh_status()` — never a cached `proposal.status` — before
      releasing funds.
- [ ] `mark_executed()` is called at **submission**, not at confirmation.
- [ ] Retries re-submit the identical proposal rather than minting a new
      `proposal_id`, and a `DUPLICATE_PROPOSAL_ID` error is escalated rather
      than worked around with a fresh id.
- [ ] `SoDConflictError.violation_type` is routed somewhere a human reads —
      `SELF_APPROVAL_ATTEMPT` and `ROLE_CONFLICT*` are security events, not
      validation noise.

## Audit evidence

- [ ] `verify_audit_chain()` is run before any report is relied on.
- [ ] `chain_head_hash` is published to append-only storage (WORM, object-lock,
      or a write-only sink this host cannot delete from) on a defined cadence.
- [ ] Nobody describes `signature_hash` to an auditor as proof that a named
      person approved. It is an unkeyed chain link — it evidences integrity,
      not identity.
- [ ] Nobody describes the in-memory chain as immutable. It is tamper-*evident*.
- [ ] The retention period for approval records was confirmed against the
      entity's actual regime, not copied from a code comment — see
      `record-retention-periods-by-jurisdiction`.
- [ ] Flagged events are reviewed on a cadence by someone who did not initiate
      or approve them.

## Known limits accepted in writing

- [ ] Two colluding, correctly-roled, distinct people defeat every control here.
      NIST SP 800-53 AC-5 is explicit that separation of duties reduces
      malevolent activity *without collusion*.
- [ ] State is in process memory; two workers can each release the same
      approved transfer. Execution is serialised externally, or this is an
      accepted risk with a named owner.
- [ ] This engine provides no timelock, no abort window, and no distinct-role
      quorum — compose `multi-signature-approval-for-large-transfers` if those
      are required.

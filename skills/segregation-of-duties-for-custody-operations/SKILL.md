---
name: segregation-of-duties-for-custody-operations
description: >-
  Maker-checker and Segregation of Duties gate for institutional custody transfers — an RBAC role-conflict matrix applied at registration, a self-approval block that cannot be bypassed by mutating a role set, approvals cryptographically bound to the payload that was reviewed, notional-tiered approval counts, optional departmental independence, and a tamper-evident SHA-256 audit chain for SOC 2 evidence.
domain: Crypto Custody & Security
subdomain: Segregation of Duties & Governance Controls
tags: ["segregation-of-duties", "maker-checker", "dual-control", "m-of-n-approval", "crypto-custody", "rbac", "soc-2-evidence", "payload-binding", "tamper-evident-audit"]
brokers_frameworks: ["AICPA Trust Services Criteria (CC1.3, CC5.1, CC6.3)", "NIST SP 800-53 Rev. 5 AC-5", "BCBS d515 Principle 9", "23 NYCRR 500.7", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a human or a bot can move assets out of custody and you need
the release decision to require more than one person, in a form an auditor can
later read. The engine enforces four distinct controls that are easy to confuse
with each other:

1. **Maker-checker** — the initiator of a proposal may never approve it.
2. **A role-conflict matrix** — no single identity holds a role combination that
   would let one person create and bless the same work, screened at registration.
3. **Payload binding** — an approval is consent to a specific destination,
   asset, amount and approval threshold, not to a proposal id.
4. **A tamper-evident audit chain** — every registration, proposal, approval,
   rejection and execution is a SHA-256 link over its predecessor.

It exists because "two people approved it" is worth nothing if one of them
proposed it, if the second was the same person under a second role, if the
destination address changed after both approved, or if the only record is a
chat message.

**On the compliance framing, be precise.** SOC 2 is an *attestation engagement*
against the AICPA Trust Services Criteria, not a certification and not a rule
book of thresholds. The TSC ask for segregation of duties in general terms —
CC5.1's point of focus "Addresses Segregation of Duties" and CC6.3's requirement
that access be granted "giving consideration to the concepts of least privilege
and segregation of duties". **No criterion prescribes a dollar threshold, an
approval count, or a hash algorithm.** Every number in the config is your firm's
policy, which you must set and defend. See `references/standards.md`.

## When NOT to Use

- **As the thing that actually stops a transfer.** This is an off-chain gate in
  your own process. An attacker who controls this code skips it. The
  authoritative enforcer must be the custodian's policy engine, the HSM quorum
  policy, or the on-chain multisig threshold. This layer runs first and produces
  the record.
- **As a signature verifier.** `ApprovalRecord.signature_hash` is an unkeyed
  SHA-256 chain link. It makes a later edit *detectable*; it does not prove the
  named approver approved anything. Authenticity is the identity layer's job —
  the engine trusts whatever `approver_id` its caller passes.
- **As the M-of-N quorum and timelock layer.** This engine has no timelock, no
  distinct-role quorum requirement, and no abort window. Use
  `multi-signature-approval-for-large-transfers` for that; the two compose.
- **As a velocity, whitelist, or anomaly control.** Nothing here caps how many
  approved transfers leave per hour or scores a destination as unusual. See
  `withdrawal-velocity-limits-and-anomaly-detection` and
  `exchange-withdrawal-whitelist-enforcement`.
- **As the system of record.** Proposals, users and the chain live in process
  memory. It is tamper-*evident*, not immutable: anything that can rewrite this
  process can recompute the whole chain. Persist entries and publish
  `chain_head_hash` to append-only storage.
- **Across processes as written.** State is guarded by an in-process lock, so one
  engine instance is thread-safe. Two workers each holding their own engine will
  each see the same proposal as `APPROVED` and can release the same transfer
  twice.

## Prerequisites

- **A registered roster.** `register_user(UserIdentity(user_id, username,
  department, roles))` for every identity, sourced from your identity provider.
  Roles are snapshotted into a `frozenset` at registration; mutating the
  caller's set afterwards does not change the engine's view. Re-registering an
  existing `user_id` raises unless `replace=True`.
- **A role-conflict matrix.** `DEFAULT_INCOMPATIBLE_ROLE_PAIRS` forbids
  `SECURITY_ADMIN` with `INITIATOR`, `APPROVER` or `AUDITOR`, and `AUDITOR` with
  `INITIATOR` or `APPROVER`. It deliberately permits `INITIATOR` + `APPROVER`,
  because many firms staff one person as maker on one workflow and checker on
  another; the per-proposal self-approval block still holds. Pass
  `STRICT_INCOMPATIBLE_ROLE_PAIRS` if your policy forbids that combination
  outright.
- **Tier policy in the config.** `large_transfer_threshold_usd` (default
  `50000.0`), `approvals_below_threshold` (default 1),
  `approvals_at_or_above_threshold` (default 2). **These defaults are
  illustrative, not regulatory.** The constructor rejects an approval count
  below 1, a negative or non-finite threshold, and a departmental minimum that
  would make a tier unreachable.
- **Optional departmental independence.**
  `min_distinct_approver_departments` (default 1, off) and
  `forbid_approver_from_initiator_department` (default `False`). Both are off by
  default because `department` is free text your firm defines.
- **A trusted clock.** Pass `clock=` for reproducible audit chains; it defaults
  to `time.time`.
- **Append-only storage** for the persisted chain, and a serialisation point
  around the approve-then-submit sequence if more than one worker can execute.

## Workflow

1. **Register Identities Before Anything Else, and Treat a Role Grant as an
   Event**: `register_user` screens the role-conflict matrix *before* storing,
   so a rejected registration leaves no user and does not advance the chain. It
   raises on a duplicate `user_id` rather than silently overwriting, because a
   silent overwrite is how a checker quietly becomes a maker.
2. **Propose (Maker Step), and Let Malformed Notionals Raise**: `propose_transfer`
   requires the `INITIATOR` role and validates the notional first. NaN, Inf,
   zero and negative amounts raise `SoDConflictError`. This matters more than it
   looks: `float('nan') >= threshold` is `False`, so an unvalidated NaN would be
   classified as a *small* transfer and routed to the lower approval
   requirement. The threshold boundary is inclusive — exactly
   `large_transfer_threshold_usd` is a large transfer.
3. **Treat a Retry as a Retry, Not a New Proposal**: Re-submitting an identical
   proposal returns the existing one with its approvals intact. Re-using a
   `proposal_id` with *different* content raises `DUPLICATE_PROPOSAL_ID` rather
   than replacing a proposal that may already carry approvals.
4. **Approve (Checker Step) — and Note the Order of the Checks**: The
   self-approval block runs *first*, before the role check, so an initiator who
   has since been granted `APPROVER` is refused on their own proposal with
   `SELF_APPROVAL_ATTEMPT` rather than a misleading role error. Then: terminal
   status, `APPROVER` role, departmental independence, duplicate approval.
5. **Bind Each Approval to the Payload That Was Reviewed**:
   `compute_proposal_digest` hashes the proposal id, initiator, destination,
   asset, amount and `required_approvals` under a domain separator, each field
   length-prefixed so no field-boundary shuffle collides. The approval stores
   that digest. `required_approvals` is inside the digest deliberately: lowering
   the threshold after approvals are in is as much an escalation as changing the
   destination.
6. **Re-derive the Status Before You Act on It**: `refresh_status()` recounts
   only the approvals still bound to the current payload. If a field changed
   after approval, the count drops and the status falls back to `PENDING`, with
   an error-level log. Revert the field and the original approvals count again —
   the digest is a function of content, not a one-way latch.
7. **Close the Loop Exactly Once at Submission**: `mark_executed(proposal_id,
   executor_id)` re-derives the status first, refuses anything not currently
   `APPROVED`, and refuses a second call. Call it when the transfer is
   *submitted*, not when it confirms: a crash in between must not be resolved by
   releasing the transfer again. `reject_transfer` is the terminal negative path
   and stays open to a checker who already approved — withdrawing consent must
   always be available.
8. **Verify and Publish the Chain**: `verify_audit_chain()` returns
   `(ok, reason)` and names the offending sequence number. Publish
   `chain_head_hash` to append-only storage on a cadence.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Believing a Proposal Object Is Frozen Once Approved**: `CustodyTransferProposal`
  is a plain mutable dataclass. Nothing stops calling code from rewriting
  `destination_address` after two people approved — and in v1.0.0 the status
  stayed `APPROVED` and the two approvals still counted. That is the Bybit shape
  of failure: a legitimate quorum, a different payload. Always read
  `refresh_status()` (or `mark_executed`, which calls it) rather than
  `proposal.status` when deciding to release funds.
- **Calling an Unkeyed Hash a Signature**: `signature_hash` is a chain link, not
  a signature and not evidence of identity. Anyone who can call
  `approve_transfer` can produce a valid-looking one under any `approver_id`.
  Presenting it to an auditor as proof that a named person approved is a claim
  the artefact does not support; what it supports is that the record has not
  been edited since.
- **Treating a Role Set as Trusted After Registration**: v1.0.0 stored the
  caller's `set` by reference, so `user.roles.add(APPROVER)` silently granted
  approval rights inside the engine and bypassed the registration-time conflict
  screen entirely. The roles are now snapshotted. If you keep your own mutable
  identity objects, they are a cache, not the authority.
- **Reading a Role-Conflict Matrix as Maker-Checker, or Vice Versa**: They are
  different controls. The matrix asks "may one person hold both these roles at
  all?"; maker-checker asks "did the person who proposed this approve it?" A
  firm that only implements the matrix still lets two people on the same desk
  rubber-stamp each other. A firm that only implements maker-checker still lets
  the security admin who manages the roster approve transfers.
- **Counting Approvals Instead of Counting Independent Approvals**: Two
  approvals from one desk is one compromised desk. `department` existed in
  v1.0.0's model and was never read by anything, which is worse than absent —
  it implies a control that was not running. Set
  `min_distinct_approver_departments` if independence is what you actually
  wanted, and remember the comparison is case-insensitive but otherwise exact:
  "Trading" and "Trading Ops" are two departments.
- **Assuming a Single Approval Tier Is a Weaker Control**: With
  `approvals_below_threshold=1`, a small transfer still needs one checker who is
  not the maker. That is dual control. What it is not is *M-of-N*; if you want a
  quorum with a timelock and an abort window, compose
  `multi-signature-approval-for-large-transfers`.
- **Deploying Two Workers Against One Wallet**: The engine's lock is
  in-process. Two processes each see `APPROVED` and each call `mark_executed`
  successfully on their own copy. Serialise execution externally or persist the
  proposal state.
- **Quoting a Threshold as a Requirement**: `$50,000` and "2 approvals" are this
  module's illustrative defaults. No source in `references/standards.md` — AICPA
  TSC, NIST SP 800-53, BCBS d515, or 23 NYCRR 500 — prescribes either. Record
  them as firm policy with a named owner.

## Verification

- Propose $100,000 and confirm `required_approvals == 2`; propose $49,999.99 and
  confirm `1`; propose exactly `$50,000` and confirm `2` (inclusive boundary).
- Propose with `float('nan')` and confirm `SoDConflictError` with
  `violation_type == INVALID_PAYLOAD`, and that no proposal was stored — not a
  single-approval proposal.
- Have the maker attempt to approve their own proposal and confirm the
  `violation_type` is `SELF_APPROVAL_ATTEMPT`, not `UNAUTHORIZED_ROLE`, even
  after adding `APPROVER` to the caller's own role set.
- Collect both approvals, then rewrite `destination_address` on the returned
  object, and confirm `refresh_status()` returns `PENDING` and `mark_executed`
  raises `THRESHOLD_NOT_MET`. Restore the field and confirm it returns to
  `APPROVED`.
- Lower `required_approvals` on an approved proposal and confirm it also drops
  to `PENDING` — the threshold is inside the digest.
- Re-submit an identical proposal and confirm the approvals already collected
  survive; re-submit the same id with a different destination and confirm
  `DUPLICATE_PROPOSAL_ID` and that the stored proposal is unchanged.
- Register `SECURITY_ADMIN` + `APPROVER` and `AUDITOR` + `APPROVER` and confirm
  both are rejected; confirm `INITIATOR` + `APPROVER` is accepted by default and
  rejected under `STRICT_INCOMPATIBLE_ROLE_PAIRS`.
- Call `mark_executed` twice and confirm the second raises
  `PROPOSAL_NOT_PENDING`.
- Mutate a stored `AuditEntry` in place and confirm `verify_audit_chain()`
  returns `(False, reason)` naming the sequence number; delete one and confirm
  the same.
- Attempt a registration that violates the matrix and confirm `chain_head_hash`
  did not advance.
- Run `python -m unittest discover -s skills/segregation-of-duties-for-custody-operations/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `multi-signature-approval-for-large-transfers`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `employee-offboarding-procedure-for-custody-access`
- `risk-control-bypass-audit-logging`
- `risk-control-configuration-change-approval-workflow`
- `emergency-manual-override-access-control`
- `exchange-withdrawal-whitelist-enforcement`
- `test-transaction-verification-before-large-transfers`
- `regulatory-custody-requirements-by-jurisdiction`

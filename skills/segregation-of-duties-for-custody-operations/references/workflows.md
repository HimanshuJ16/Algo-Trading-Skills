# Workflows — Segregation of Duties for Custody Operations

## 0. Build the roster before anything else

```python
from segregation_of_duties_for_custody_operations import (
    CustodyRole,
    SegregationOfDutiesForCustodyOperationsConfig,
    SegregationOfDutiesForCustodyOperationsEngine,
    STRICT_INCOMPATIBLE_ROLE_PAIRS,
    UserIdentity,
)

engine = SegregationOfDutiesForCustodyOperationsEngine(
    SegregationOfDutiesForCustodyOperationsConfig(
        approvals_below_threshold=1,
        approvals_at_or_above_threshold=2,
        min_distinct_approver_departments=1,          # raise to 2 for cross-desk quorums
        forbid_approver_from_initiator_department=False,
    ),
    large_transfer_threshold_usd=50_000.0,            # firm policy, not regulation
    clock=trusted_now,                                # reproducible audit chain
)

for identity in roster_from_your_identity_provider():
    engine.register_user(identity)
```

The constructor validates the policy up front rather than at the first transfer:
an approval count below 1, a negative or non-finite threshold, and a
`min_distinct_approver_departments` that exceeds a tier's approval count each
raise `SoDConflictError` at construction. That last one is the easy mistake —
`min_distinct_approver_departments=2` with the default
`approvals_below_threshold=1` would make every small transfer permanently
unapprovable, silently, until someone noticed a backlog.

`register_user` snapshots `roles` into a `frozenset`. Keep your own identity
objects if you like, but they are a cache; the engine's copy is the authority.
Re-registering an existing `user_id` raises unless you pass `replace=True`.

### Choosing the role-conflict matrix

`DEFAULT_INCOMPATIBLE_ROLE_PAIRS` forbids:

| Pair | Why |
|---|---|
| `SECURITY_ADMIN` + `INITIATOR` | Whoever manages the roster could grant themselves an approver and release their own transfer |
| `SECURITY_ADMIN` + `APPROVER` | Same, one step shorter |
| `SECURITY_ADMIN` + `AUDITOR` | NIST AC-5: "security personnel who administer access control functions do not also administer audit functions" |
| `AUDITOR` + `INITIATOR` | Auditing your own transfers |
| `AUDITOR` + `APPROVER` | Auditing your own approvals |

It deliberately permits `INITIATOR` + `APPROVER`. Many firms legitimately staff
one person as maker on one workflow and checker on another, and the per-proposal
self-approval block still holds in that case. If your policy forbids the
combination outright, pass `incompatible_role_pairs=STRICT_INCOMPATIBLE_ROLE_PAIRS`.

## 1. Propose (the maker step)

```python
proposal = engine.propose_transfer(
    proposal_id="PROP_2026_0142",
    initiator_id="USR_MAKER_1",
    destination_address="0xColdVault",
    asset_symbol="BTC",
    amount_usd=100_000.0,
)
# proposal.required_approvals == 2, status == "PENDING"
# proposal.payload_digest is what the checkers are being asked to consent to
```

Validation runs before classification. NaN, Inf, zero and negative notionals
raise `SoDConflictError(violation_type=INVALID_PAYLOAD)`. This ordering is not
cosmetic: `float('nan') >= threshold` is `False`, so an unvalidated NaN falls
through to the *small*-transfer branch and lands on the lower approval
requirement. The same applies to a blank `proposal_id`, `destination_address` or
`asset_symbol` — an unattributable record is not an audit record.

The threshold boundary is inclusive: exactly `large_transfer_threshold_usd` is a
large transfer.

### Retries

Re-submitting a byte-identical proposal returns the existing object with its
approvals intact, so a lost acknowledgement does not cost you the approvals
already gathered:

```python
again = engine.propose_transfer("PROP_2026_0142", "USR_MAKER_1", "0xColdVault", "BTC", 100_000.0)
assert again is proposal
```

Re-using the same id with *different* content raises `DUPLICATE_PROPOSAL_ID`.
In v1.0.0 it silently replaced the stored proposal — discarding the approval
history and substituting a new destination under an id that reviewers had
already seen.

## 2. Approve (the checker step)

```python
engine.approve_transfer("PROP_2026_0142", "USR_CHECKER_1")
proposal = engine.approve_transfer("PROP_2026_0142", "USR_CHECKER_2")
assert proposal.status == "APPROVED"
```

The checks run in this order, and the order is part of the contract:

| # | Check | Violation type on failure |
|---|---|---|
| 1 | Approver is not the initiator | `SELF_APPROVAL_ATTEMPT` |
| 2 | Proposal is not `REJECTED` / `EXECUTED` | `PROPOSAL_NOT_PENDING` |
| 3 | Approver is registered and holds `APPROVER` | `UNAUTHORIZED_ROLE` |
| 4 | Approver is not on the initiator's desk (if configured) | `INSUFFICIENT_DEPARTMENT_SEPARATION` |
| 5 | Approver has not already approved | `DUPLICATE_APPROVAL` |

Self-approval is checked **first** so that an initiator who has since been
granted `APPROVER` is refused with the SoD violation rather than a role error.
The distinction matters when someone reads the log six months later: "no
APPROVER role" and "tried to approve their own transfer" describe very different
events.

Every refusal carries a machine-readable `violation_type`:

```python
try:
    engine.approve_transfer("PROP_2026_0142", "USR_MAKER_1")
except SoDConflictError as exc:
    route_to_security(exc.violation_type)   # SoDViolationType.SELF_APPROVAL_ATTEMPT
```

## 3. Payload binding — the control that matters most

`CustodyTransferProposal` is a plain mutable dataclass, so nothing in Python
stops calling code from rewriting a field after the approvals are in. The
defence is that approvals are bound to a digest, not to the proposal id:

```python
digest = compute_proposal_digest(proposal)   # domain-separated, length-prefixed
```

covering `proposal_id`, `initiator_id`, `destination_address`, `asset_symbol`,
`amount_usd` and `required_approvals`. Each field is length-prefixed so
`("0xAB", "CD")` and `("0xABC", "D")` cannot collide.

`required_approvals` is inside the digest on purpose. Lowering the threshold
after approvals arrive is an escalation of exactly the same kind as changing the
destination, and it would otherwise be invisible.

```python
proposal.destination_address = "0xAttacker"       # tampering, from anywhere in-process
engine.valid_approvals(proposal)                  # []
engine.refresh_status("PROP_2026_0142").status    # "PENDING"  (+ an ERROR log line)
```

The digest is a function of content, not a one-way latch — restore the field and
the original approvals count again. That is correct: they were consent to *that*
payload, and that payload is once again what is on the table.

**Read `refresh_status()`, not `proposal.status`, when deciding to release
funds.** `mark_executed` calls it for you.

## 4. Close the loop — exactly once

```python
released = engine.mark_executed("PROP_2026_0142", executor_id="USR_ADMIN_1")
assert released.status == "EXECUTED"
```

Call this at **submission**, not at confirmation. A crash between submitting a
transfer and seeing it confirm must not be resolved by submitting it again;
`mark_executed` refuses a second call with `PROPOSAL_NOT_PENDING`. It also
re-derives the status first, so a proposal whose payload changed after approval
raises `THRESHOLD_NOT_MET` instead of releasing.

The negative path is terminal too:

```python
engine.reject_transfer("PROP_2026_0142", "USR_CHECKER_1", "destination not verified out-of-band")
```

A checker who already approved may still reject — withdrawing consent has to
stay available to someone who spots a problem afterwards. The initiator may
withdraw their own proposal, which is not self-approval because a rejection
cannot release funds. An `AUDITOR` may not reject: read-only means read-only.
A reason is mandatory.

## 5. Audit evidence

```python
ok, reason = engine.verify_audit_chain()
publish_to_worm_storage(engine.chain_head_hash)     # on a cadence
for entry in engine.audit_trail():
    archive(entry)
```

Every registration, proposal, approval, rejection and execution is one link.
A rejected action — a role-conflict registration, a self-approval attempt —
does **not** advance the chain, because it produced no state change to attest to.

Two honest limits, both worth stating to an auditor before they ask:

- `signature_hash` on an `ApprovalRecord` is the audit-chain link for that
  approval. It is an **unkeyed** SHA-256 hash. It shows the record has not been
  edited since; it does not show that the named person approved anything. Anyone
  who can call `approve_transfer` can mint one under any `approver_id`.
  Authenticity belongs to whatever authenticated the caller.
- The chain lives in one process. `verify_audit_chain()` detects an edited,
  deleted or reordered entry; it cannot detect an attacker who edits an entry
  and recomputes every hash after it. That is what publishing the head to
  append-only storage is for.

## 6. Operating notes

- **Threading.** All state mutation is under a re-entrant lock, so one engine
  instance is safe across threads. Two *processes* each holding their own engine
  will each see `APPROVED` and can each release the same transfer. Serialise
  execution externally or persist proposal state.
- **Clock.** Pass `clock=` for reproducible chains; the default is `time.time`.
  Two engines driven by the same deterministic clock over the same event
  sequence produce the same `chain_head_hash`.
- **Offboarding.** This engine has no revocation call by design. Removing a
  departed employee is `employee-offboarding-procedure-for-custody-access`;
  re-register the roster from the identity provider rather than editing
  `engine.users` in place.
- **Composition.** For an M-of-N quorum with a timelock and an abort window, put
  `multi-signature-approval-for-large-transfers` in front of this gate. For
  destination allowlisting, `exchange-withdrawal-whitelist-enforcement`. For a
  first small transfer to a new address,
  `test-transaction-verification-before-large-transfers`.

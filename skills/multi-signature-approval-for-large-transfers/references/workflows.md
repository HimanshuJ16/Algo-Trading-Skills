# Workflows for Multi-Signature Approval for Large Transfers

## 0. Wire the roster before anything else

```python
engine = MultiSigApprovalEngine(MultiSigConfig(
    auto_approve_threshold_usd=10_000.0,
    high_value_threshold_usd=100_000.0,
    high_value_timelock_seconds=3_600.0,
))
for signer_id, role in roster_from_your_identity_provider():
    engine.register_signer(signer_id, role)
```

One role per signer. If two people share a role, the quorum they form counts as
one control for the distinct-role check, which is the intended reading: three
approvals from one desk is one desk.

The constructor validates the policy. `high_m_required > high_n_total`,
`high_value_threshold_usd < auto_approve_threshold_usd`, a distinct-role minimum
above $M$, a negative timelock, or a non-finite threshold each raise
`MultiSigApprovalError` at construction — not silently at the first transfer.

## 1. Create and anchor the request

```python
request = TransferRequestPayload(
    request_id="TX_TREASURY_9",
    amount_usd=500_000.0,
    source_wallet="0xTreasury",
    destination_address="0xColdVault",
    initiated_by="BOT_REBALANCER",
    creation_timestamp=trusted_now(),
    asset_symbol="USDC",
    asset_quantity=500_000.0,
    chain="ethereum",
    nonce=1,
)
anchor = engine.register_request(request, current_time=trusted_now())
digest = compute_transfer_digest(request)
persist(request.request_id, digest, anchor)     # survives a restart
notify_humans_out_of_band(request, digest)      # the window is only protective if watched
```

`register_request` sets the timelock anchor from the engine's clock.
`creation_timestamp` is carried for the audit record and skew-checked, and is
never used to compute elapsed time — a requester who could back-date it would
otherwise open the lock instantly.

Persisting the anchor matters: if the process restarts and the anchor is lost,
the next evaluation re-anchors to *then* and the window starts over. Replay it
with `engine.restore_timelock_anchor(digest, stored_anchor)`, which is the only
path that may set an anchor into the past and is deliberately a named
administrative call.

## 2. Collect approvals bound to the digest

Present the digest to each signer alongside the destination, chain, asset and
quantity, and record what they approved:

```python
approval = SignerApproval(
    signer_id="CFO_A",
    role="CFO",
    timestamp=trusted_now(),
    approved_digest=digest,
)
```

`approved_digest` is what turns this into an approval *of something*. Without it
the approval endorses a `request_id` whose contents can change before execution,
and the engine rejects it as `APPROVAL_NOT_BOUND_TO_PAYLOAD`.

The digest is not a signature. If the approval channel is not itself
authenticated, an approval object proves only that something claiming to be
`CFO_A` sent it. Where the stakes justify it, have signers produce a real
signature over the digest and verify it before constructing `SignerApproval`.

**Out-of-band destination check.** At least one role should verify the
destination and amount on a separate device against a separately-sourced address
book before approving. Bybit's quorum was intact and its keys were never stolen;
what failed was that every signer read the same compromised screen.

## 3. Evaluate

```python
report = engine.evaluate_transfer_approval(request, approvals, current_time=trusted_now())
```

Screening order per approval — first failure wins, and every failure is recorded
in `report.rejected_approvals`:

| Check | Rejection reason |
|---|---|
| Non-empty signer id | `BLANK_SIGNER_ID` |
| Not the initiator (above `LOW_AUTO`) | `SELF_APPROVAL_BY_INITIATOR` |
| On the roster, not suspended | `SIGNER_NOT_ON_ROSTER` / `SIGNER_SUSPENDED` |
| Declared role matches the roster | `ROLE_MISMATCH_WITH_ROSTER` |
| Digest present and matching | `APPROVAL_NOT_BOUND_TO_PAYLOAD` / `APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD` |
| Finite timestamp | `NON_FINITE_APPROVAL_TIMESTAMP` |
| Not ahead of the clock beyond skew | `APPROVAL_TIMESTAMP_IN_FUTURE` |
| Within `approval_validity_seconds` | `APPROVAL_EXPIRED` |
| Signer not already counted | `DUPLICATE_APPROVAL_FROM_SAME_SIGNER` |

Decision ladder — the most decisive blocker wins, so the operator sees the
reason that matters rather than the last one checked:

1. `REQUEST_REVOKED` — someone aborted this `request_id`.
2. `ALREADY_EXECUTED` — this digest was already released.
3. `INSUFFICIENT_SIGNATURES` — fewer than $M$ approvals survived screening.
4. `INSUFFICIENT_DISTINCT_ROLES` — quorum met, but too few distinct roles.
5. `TIMELOCK_PENDING` — quorum and roles met, window not elapsed.
6. `TRANSFER_APPROVED`.

Tier boundaries, both exclusive at the top end:

| Notional | Tier | $M$-of-$N$ | Distinct roles | Timelock |
|---|---|---|---|---|
| `< auto_approve_threshold_usd` | `LOW_AUTO` | 1-of-1 | 1 | none |
| `>= auto` and `<= high` | `MEDIUM_MULTISIG` | `med_m_required`-of-`med_n_total` | `med_distinct_roles_required` | none |
| `> high_value_threshold_usd` | `HIGH_MULTISIG_TIMELOCK` | `high_m_required`-of-`high_n_total` | `high_distinct_roles_required` | `high_value_timelock_seconds` |

The unlock boundary is inclusive: `elapsed >= timelock` approves. Exactly
`high_value_threshold_usd` is the medium tier and carries no timelock — if that
is not what your policy means, set the threshold one cent lower.

## 4. Abort, if the window turns up something

```python
engine.revoke_request(request.request_id, revoked_by="SEC_C", reason="destination unrecognised")
engine.suspend_signer("RISK_B", reason="suspected key compromise")
```

`revoke_request` is keyed on `request_id`, so an attacker cannot resurrect a
revoked request by bumping the nonce or nudging the amount into a new digest.
`suspend_signer` takes effect on the next evaluation, so a quorum that included
the suspended signer drops back below $M$ before release.

## 5. Release and record

```python
if report.is_approved:
    submit_to_vault(request, report.transfer_digest)
    engine.mark_executed(report.transfer_digest, current_time=trusted_now())
```

Mark at submission, not at confirmation: a crash in between must not be
resolvable by releasing the transfer a second time. `mark_executed` raises if the
digest was already marked, and a later evaluation of that digest returns
`ALREADY_EXECUTED`.

## 6. What must be true outside this module

- The vault itself enforces a threshold. This gate constrains your code; it does
  not constrain an attacker who controls your code. See `references/standards.md`.
- The engine's state is in-process. The internal lock serialises callers within
  one process; two processes each hold their own roster, anchors and executed set,
  and will each release the same transfer. Back this state with a shared store
  before running more than one worker.
- Persist anchors and executed digests. Both are what stop a restart from
  reopening a window or re-releasing a payload.
- Alert on `SIGNER_NOT_ON_ROSTER`, `ROLE_MISMATCH_WITH_ROSTER`,
  `APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD`, `APPROVAL_TIMESTAMP_IN_FUTURE`, and any
  `MULTISIG ANCHOR RESTORED` line. None of these occurs in normal operation.

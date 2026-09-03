---
name: multi-signature-approval-for-large-transfers
description: >-
  Use when a large crypto transfer needs independent human authorisation before it
  proceeds, binding each approval to a hash of the exact payload reviewed and counting
  only registered signers across distinct devices.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: multisig, transfer-approval, m-of-n, timelock, governance, crypto-custody, role-based-access, payload-binding
  brokers_frameworks: "Multisig Policy Engine; Safe{Wallet} Smart Account; Role-Based Access Control (RBAC); CCSS v9; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a bot, treasury job, or ops script can move a material amount
of crypto and you need a policy gate deciding whether that transfer has collected
enough independent human authorisation to proceed. It classifies a request into a
risk tier by USD notional, requires an $M$-of-$N$ quorum of **registered** signers
spanning a minimum number of **distinct roles**, requires each approval to be
bound to the exact payload that signer reviewed, and holds high-value transfers
for a timelock window during which any authorised party can abort.

It exists because a single-signature automated withdrawal path converts one
compromised key or one compromised bot into a total loss of the wallet it can
reach.

## When NOT to Use

- **As the only thing standing between an attacker and the funds.** This is an
  off-chain gate inside your own infrastructure. If the vault itself will release
  funds on one signature, an attacker who owns your code skips this module
  entirely. The authoritative enforcer must be the on-chain multisig threshold,
  the HSM quorum policy, or the custodian's own policy engine; this gate runs
  first and produces the audit record.
- **As a substitute for reviewing what is being signed.** In the February 2025
  Bybit incident, $1.46bn left a multisig cold wallet without a single key being
  stolen: the signing interface was compromised, and a legitimate quorum signed a
  payload that was not the one displayed to them. Quorum size does not help when
  every signer reads the same falsified presentation — see the pitfalls below.
- **As a velocity, exposure, or anomaly control.** Nothing here caps how many
  approved transfers may leave per hour, or scores a destination as unusual. See
  `withdrawal-velocity-limits-and-anomaly-detection` and
  `exchange-withdrawal-whitelist-enforcement`.
- **As a cryptographic verifier.** `SignerApproval.approved_digest` records *which
  payload* a signer approved; it does not prove *that they approved it*.
  Authenticity comes from your identity layer or from real signatures over the
  digest. Treat an approval object as attested only as strongly as the channel it
  arrived on.
- **Across processes or hosts as written.** The roster, timelock anchors,
  revocations, and executed digests live in process memory. Two workers each
  holding their own engine will each see an unexecuted digest and release the same
  transfer twice.

## Prerequisites

- A **registered signer roster**: `register_signer(signer_id, role)` for every
  eligible signer, each with exactly one role. An approval from an id that is not
  on the roster is not counted, because without a roster "3-of-5" is only "any
  three strings".
- **Tier policy** in `MultiSigConfig`: `auto_approve_threshold_usd`,
  `high_value_threshold_usd`, `(med_m_required, med_n_total)`,
  `(high_m_required, high_n_total)`, `high_value_timelock_seconds`, and the
  distinct-role minimums. The constructor rejects $M > N$, inverted thresholds,
  a role minimum above $M$, and non-finite values.
- A **transfer payload** (`TransferRequestPayload`) carrying `request_id`,
  `amount_usd`, `source_wallet`, `destination_address`, `initiated_by`, and — for
  anything above the low tier — `asset_symbol`, `asset_quantity`, `chain` and a
  `nonce`. Omitting `asset_quantity` is allowed but reported as a warning: the
  quorum is then approving a USD valuation while the on-chain amount stays
  unconstrained by the digest.
- A **trusted clock** for `current_time`. Pass it explicitly for reproducible
  audits. `0.0` is honoured as a real timestamp.
- **Durable storage for timelock anchors** if the process can restart mid-window.
  Persist `report.timelock_anchor_timestamp` and replay it through
  `restore_timelock_anchor()`; otherwise a restart re-anchors and the window
  starts again.
- A **serialisation point** around the audit-then-submit sequence, and
  `mark_executed()` called at submission.

## Workflow

1. **Classify by Notional, Then Validate Before Classifying Anything Else**:
   Tiers are `LOW_AUTO` (`< auto_approve_threshold_usd`), `MEDIUM_MULTISIG`
   (up to and including `high_value_threshold_usd`), and
   `HIGH_MULTISIG_TIMELOCK` above it. Both boundaries are decided *before* any
   comparison runs, because a non-finite `amount_usd` compares `False` against
   every threshold and would otherwise land in whichever branch the `if/elif`
   chain ends on. NaN, Inf, zero, and negative amounts raise
   `MultiSigApprovalError` rather than producing a decision.
2. **Derive the Payload Digest and Make That the Thing Being Approved**:
   `compute_transfer_digest()` hashes the destination, chain, asset, quantity,
   USD valuation and nonce under a domain separator, with each field
   length-prefixed so no field-boundary shuffle can produce a colliding digest.
   Signers approve *that*, exactly as a Safe owner signs a `safeTxHash` covering
   `to`/`value`/`data`/`operation`/`nonce` rather than a transaction id. Changing
   any bound field yields a new digest, which invalidates every approval already
   collected and starts a fresh timelock.
3. **Screen Each Approval, and Record Why Each One Failed**: An approval is
   counted only if the signer is on the roster and not suspended, the role it
   declares matches the roster (a mismatch is a tamper signal, not a typo), its
   digest matches the request, its timestamp is finite and not in the future
   beyond the skew tolerance, it has not expired under
   `approval_validity_seconds`, and the same signer has not already been counted.
   Everything rejected lands in `report.rejected_approvals` with a reason.
4. **Require Distinct Roles, Not Just Distinct Ids**: Three approvals from three
   people on the same desk is one compromised desk, not three independent
   controls. `high_distinct_roles_required` (default 3) makes
   `INSUFFICIENT_DISTINCT_ROLES` a distinct outcome from
   `INSUFFICIENT_SIGNATURES` so the operator can see which control failed.
5. **Anchor the Timelock to the Engine's Clock, Never the Request's**:
   `creation_timestamp` travels with the request; a requester who can back-date
   it can open the timelock instantly. The anchor is the moment the engine first
   observed *this digest* — set by `register_request()`, or by the first
   evaluation if that call never happened. Re-registering the same payload keeps
   the original anchor. `creation_timestamp` is recorded for audit and
   skew-checked only. The unlock boundary is inclusive: `elapsed >= timelock`
   approves.
6. **Give the Window Something to Do**: A timelock whose only outcome is "wait,
   then release" buys nothing. `revoke_request()` aborts a request permanently
   and is keyed on `request_id`, so it survives a nonce bump or an amount nudge.
   `suspend_signer()` takes a suspected-compromised signer's approvals back out
   of the count on the next evaluation, before the transfer can be released.
7. **Close the Loop at Submission**: On `TRANSFER_APPROVED`, submit, then call
   `mark_executed(report.transfer_digest)` — at submission, not at confirmation,
   so a crash between the two cannot be resolved by releasing the transfer again.
   A later evaluation of that digest returns `ALREADY_EXECUTED`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting Approvals Without a Roster**: If any `signer_id` string counts, an
  attacker who can submit approvals invents three names and satisfies 3-of-5.
  The threshold is meaningful only relative to a fixed, enumerated set of
  eligible signers, and $N$ must actually exist — the engine warns when the
  eligible roster is smaller than the $N$ the policy claims.
- **Approving a Request Id Instead of a Payload**: An approval that names only
  `request_id` authorises whatever that request contains at execution time. Bind
  the approval to a digest of the destination, chain, asset and amount, and treat
  a changed digest as a loss of quorum rather than an update to an approved
  request.
- **Assuming Quorum Beats a Compromised Signing Surface**: Bybit's signers held
  their keys throughout; the interface they read was the thing that lied. $M$
  independent approvals of one falsified screen is one approval. Have at least
  one role verify the destination and amount out of band — on a separate device,
  against a separately-sourced address book — before approving.
- **Measuring the Timelock From a Caller-Supplied Timestamp**: If elapsed time is
  `now - request.creation_timestamp`, anything that can build a request can
  open the lock by writing an old number into it. Use a clock the request cannot
  influence, and treat any restore of a past anchor as a named administrative
  action.
- **`current_time or time.time()`**: a legitimate `0.0` is falsy, so this idiom
  silently swaps a caller's explicit epoch-zero clock for the wall clock and
  turns a deterministic audit into a live one. Test `is None`.
- **Letting NaN Reach a Threshold Comparison**: `float('nan') < 10_000.0` and
  `float('nan') <= 100_000.0` are both `False`, so a NaN notional falls through
  the tier ladder into whatever the final `else` is, and `nan >= timelock` is
  `False` in a check that gates on `not timelock_ok`. Validate for finiteness at
  the boundary and raise.
- **Treating the Timelock as Time-Since-Creation Rather Than Time-Visible**: the
  window is only protective if a human can actually see the pending request
  during it. The anchor starts when the engine first observes the payload, so
  every registration must also notify a human out of band from the system that
  created it. A window nobody is watching is a delay, not a control.
- **Self-Approval by the Initiator**: blocked above the low tier. At `LOW_AUTO`
  the initiator may self-serve by design — that is what makes it the automation
  tier — so set `auto_approve_threshold_usd` to the largest loss you are willing
  to absorb without review, or to `0.0` to remove the tier, and set
  `low_tier_allows_self_approval=False` if even that is too much.
- **Re-Approving an Executed Payload**: without an executed-digest record, one
  approved report can be replayed to release the same transfer repeatedly. Mark
  execution, and hold that record somewhere that survives a restart.
- **Presenting the Tiers as Compliance**: the $10k/$100k thresholds, the 2-of-3
  and 3-of-5 splits, and the one-hour timelock are firm policy. No regulator
  prescribes them — see `references/standards.md`.

## Verification

- Register a 5-signer, 5-role roster; submit a $250,000 request, three
  digest-bound approvals from three roles, and confirm `TIMELOCK_PENDING` before
  the window elapses and `TRANSFER_APPROVED` at exactly `anchor + 3600`.
- Submit three approvals from ids that are not on the roster and confirm
  `submitted_approvals_count == 0` with every entry rejected as
  `SIGNER_NOT_ON_ROSTER`.
- Collect a full quorum for one destination, then evaluate the same approvals
  against a request that differs only in `destination_address`, and confirm every
  approval is rejected as `APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD`.
- Submit a request whose `creation_timestamp` is a billion seconds in the past and
  confirm the full timelock is still owed.
- Pass `current_time=0.0` and confirm the anchor is `0.0` rather than the wall
  clock.
- Submit three approvals from three signers sharing one role and confirm
  `INSUFFICIENT_DISTINCT_ROLES`, distinct from `INSUFFICIENT_SIGNATURES`.
- Suspend one of three approving signers mid-window and confirm the transfer
  drops back to `INSUFFICIENT_SIGNATURES` before release.
- Revoke a request, re-submit it with a bumped nonce, and confirm it is still
  `REQUEST_REVOKED`.
- Approve, `mark_executed`, re-evaluate, and confirm `ALREADY_EXECUTED`.
- Submit `amount_usd` of `nan`, `inf`, `0.0` and `-1.0`, and a blank destination,
  and confirm each raises `MultiSigApprovalError` rather than producing a report.
- Construct `MultiSigConfig(high_m_required=9, high_n_total=2)` and confirm it
  raises.
- Run `python -m unittest discover -s skills/multi-signature-approval-for-large-transfers/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `segregation-of-duties-for-custody-operations`
- `multi-party-computation-mpc-custody-solutions`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `exchange-withdrawal-whitelist-enforcement`
- `test-transaction-verification-before-large-transfers`
- `hardware-security-module-hsm-for-signing-keys`
- `air-gapped-signing-workflow-for-cold-storage`
- `emergency-manual-override-access-control`

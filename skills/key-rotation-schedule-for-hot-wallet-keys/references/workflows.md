# Workflows for Hot Wallet Key Rotation

The engine is a policy evaluator. Each step below describes what the evaluator decides and
what the operator must actually do — those are different things, and conflating them is how
a key gets marked shredded while it still controls money.

## 0. Set the policy before auditing anything

Choose `max_key_age_days`, `max_signatures_limit`, `max_volume_usd_limit` and
`grace_period_hours` deliberately and record the reasoning. None of the defaults is mandated
(see `standards.md`). Anchor the age threshold on NIST SP 800-57 §5.3.1 factors that apply
to *this* key: how it is embodied (process memory vs HSM), how exposed its host is, and its
transaction volume. A key in an HSM on a segmented host does not need the same cadence as one
in an environment variable on an internet-facing box.

Balance this against §5.3.2's warning that over-frequent rotation is itself a risk when
re-keying is error-prone. For a trading bot the failure mode is an unhedged book, and each
on-chain rotation costs fees and publishes a new address.

## 1. Classify the key

- `KEY_CLASS_ONCHAIN_SIGNING` — a blockchain private key. **Irrevocable.** Destruction is
  gated on `residual_balance_usd` reaching zero.
- `KEY_CLASS_EXCHANGE_API` — a venue credential. Revocable server-side; the account balance
  is not controlled by the key, so no sweep gate applies.

This is the default-safe direction: `ONCHAIN_SIGNING` is the default because mis-classifying
an on-chain key as an API key clears it for destruction while it still holds funds.

## 2. Audit the key metadata

Supply timestamps as POSIX epoch **seconds**. The engine rejects millisecond values, a
creation date beyond the clock-skew tolerance in the future, a `last_used` earlier than
creation, NaN/negative amounts, fractional signature counts, empty ids, unknown states and
unknown key classes — all with `KeyRotationError`. It fails loudly rather than emitting a
`KEY_HEALTHY_ACTIVE` verdict over unusable data.

Triggers are evaluated on the **unrounded** age, in precedence order:

1. `age_days >= max_key_age_days` → `ROTATION_INITIATED_AGE_EXPIRED`
2. `total_signatures_count >= max_signatures_limit` → `ROTATION_INITIATED_USAGE_EXPIRED`
3. `total_volume_usd_signed >= max_volume_usd_limit` → `ROTATION_INITIATED_VOLUME_EXPIRED`

`key_age_days` on the report is rounded to 2dp for display only. A key one second short of
the limit stays healthy even though it prints as `90.0`.

## 3. Compromise path — no grace period

`is_compromised` short-circuits every threshold. A leaked key is being used against you, so
there is no drain window.

- On-chain key with `residual_balance_usd > 0` → `EMERGENCY_SWEEP_REQUIRED`, state
  `PENDING_FUND_SWEEP`. **Sweep the address first.** The key material must survive until the
  sweep confirms, because it is the only thing that can move those funds — and until it does,
  the attacker has equal authority over them.
- Otherwise → `EMERGENCY_REVOKED_COMPROMISED`, state `REVOKED_SHREDDED`. Revoke at the venue
  and destroy the local material.

Operator actions the engine does not perform: revoking at the venue, broadcasting the sweep,
rotating any downstream credential the key protected, and the forensic process — see
`post-incident-forensics-for-suspected-key-compromise`.

## 4. Grace period — a drain window, not a deployment window

On a trigger the engine records `grace_period_started_epoch = now` and moves the key to
`DEPRECATED_GRACE_PERIOD`, proposing the label `<key_id>_V2`. **The engine does not generate
that key** — an operator or KMS must.

Re-auditing inside the window returns `GRACE_PERIOD_ACTIVE` idempotently: same label, no new
rotation event, clock unchanged. This matters because an audit that re-triggers on every call
never advances the key out of the window at all.

Size the window above the slowest settlement path in use — Ethereum finalises across two
32-slot epochs (~13 min) but mempool residency runs longer; exchange reconciliation is often
longer still. The window exists to let *already-authorised* work land. If
`last_used_timestamp_epoch` advances past the grace start, the bot is still signing with the
old key: the cutover did not happen, and the report says so in `warnings`. Deployment of the
new key is a separate concern — see `secrets-rotation-without-bot-downtime`.

## 5. Sweep gate and terminal state

When the window closes:

- Residual balance on an on-chain key → `GRACE_PERIOD_ELAPSED_PENDING_SWEEP`, state
  `PENDING_FUND_SWEEP`. The key stays here across repeated audits until the balance is zero.
  On-chain dust would stall that indefinitely, so `dust_threshold_usd` (default `0.0`,
  strictly fail-closed) can be raised as a deliberate, recorded decision — which is the
  honest alternative to writing a false zero balance into the audit trail.
- Otherwise → `ROTATION_COMPLETE_KEY_SHREDDED`, state `REVOKED_SHREDDED`, which satisfies
  NIST SP 800-57 §5.3.6(1)(b): a private signature key **shall** be destroyed at the end of
  its cryptoperiod.

`REVOKED_SHREDDED` is terminal. A re-audit returns `KEY_ALREADY_REVOKED` and never `ACTIVE`,
so an automated caller cannot resume signing with a key whose material is supposed to be gone.
If such a key is still reported holding a balance, the report carries a stranded-funds
warning: either the funds are unrecoverable, or the material was not actually destroyed.

## 6. What the operator still owns

The engine advances a state field. It does not:

- generate, store, load or destroy key material, or zeroize memory;
- verify that a sweep confirmed on-chain, or that a venue honoured a revocation;
- check that at least one active key remains for the wallet — a cross-key invariant the
  caller must enforce before shredding anything;
- handle multi-signature or MPC resharing, where "rotating a key" means a quorum protocol
  rather than a replacement.

Record the report against the key id in an append-only audit log. The report is the decision;
evidence that the decision was executed has to come from the KMS, the chain, and the venue.

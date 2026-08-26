# Workflows — multi-party-computation-mpc-custody-solutions

The engine sits **in front of** the MPC library, not inside it. It answers one
question — may this signing ceremony start? — and records why. It never sees key-share
material and never returns a signature.

## 0. Build the shard roster (once, at key generation)

1. Generate the key with a maintained MPC library. Record `N` and `t`; the engine
   derives `N` from the roster so the two can never drift apart.
2. Assign every node an explicit `failure_domain`: the cloud account, hosting provider,
   or physical operator whose compromise would yield control of it. Two regions of one
   AWS account are **one** domain. Getting this wrong silently disables the
   independence check.
3. Set `current_key_epoch = 1` and `last_key_refresh_date` to the key generation date.
4. Confirm the library's patched version against CVE-2023-33241 (GG18/GG20) and
   TSSHOCK (all three protocols), then set the two hardening flags. Both default to
   `False`, so an unattested configuration denies every ceremony — that is the intent,
   and clearing the denial without checking the version defeats the control.
5. Construct `MPCCustodyEngine`. Policy validation runs here and raises
   `MPCCustodyConfigError` on anything unsatisfiable. Mutating the config afterwards is
   unsupported: validation does not run again.

## 1. Collect shard attestations (per ceremony)

Each participating node reports that it is present and willing, and **nothing about
its secret share**. `MPCShardAttestation` carries:

- `node_id` — must be in the roster.
- `share_commitment` — a non-secret hex commitment derived deterministically from the
  node's **public** key share via `derive_shard_attestation_commitment(public_share,
  key_epoch, tx_hash)`. Determinism is what makes cloned-shard detection work: two
  hosts holding the same shard produce the same value. A randomised per-ceremony nonce
  passes validation but silently defeats the check.
- `key_epoch` — the PSS refresh generation this node's share belongs to.

Attestations must reach the engine over authenticated transport. The engine trusts
that an attestation came from the node it names; mutual TLS or signed attestations
belong underneath it.

## 2. Evaluate authorisation

`evaluate_signing_authorization(request, evaluation_date=...)` runs every check and
reports every failure. `status` names the highest-priority one so an alert routes on a
single field. Pass `evaluation_date` explicitly for reproducible audit output.

| Order | Check | Denial status |
|---|---|---|
| 1 | Every attesting `node_id` is in the roster | `MPC_UNAUTHORIZED_NODE` |
| 2 | One attestation per node; no shared commitment across node ids | `MPC_DUPLICATE_SHARD_ATTESTATION` |
| 3 | Every attestation on `current_key_epoch` | `MPC_KEY_EPOCH_MISMATCH` |
| 4 | Accepted attestations `>= threshold_t` | `MPC_THRESHOLD_NOT_MET` |
| 5 | Accepted quorum spans `>= min_distinct_failure_domains` | `MPC_FAILURE_DOMAIN_CONCENTRATION` |
| 6 | CVE-2023-33241 and TSSHOCK mitigations attested | `MPC_PROTOCOL_NOT_HARDENED` |
| 7 | Proactive refresh current (denies only if `deny_on_overdue_refresh`) | `MPC_KEY_REFRESH_OVERDUE` |

An attestation only counts toward step 4 if it survived steps 1–3, so
`accepted_attestation_count` is the number of shards genuinely eligible to sign — not
the number of messages received.

**Why step 1 fails the whole request.** Removing a party from a t-of-N group requires
a resharing, not an allowlist edit, so an attestation from outside the roster is
either a live misconfiguration or an adversary probing the ceremony. Dropping it and
proceeding on the remaining shards discards the only signal that either happened.

**Why step 7 warns by default.** An overdue refresh is a hygiene defect, not evidence
that this ceremony is compromised. Hard-denying can strand a treasury mid-incident, so
the trade-off is the operator's: set `deny_on_overdue_refresh=True` where availability
is subordinate to freshness.

## 3. Run the ceremony

`is_authorized=True` means *start the protocol in the MPC library*. It is not evidence
that a signature exists, and the report deliberately carries no signature fields.
Hand the transaction to the library, then:

- On **identifiable abort** (CGGMP21 and GG20 provide it; GG18 does not), record which
  party the protocol blamed. A repeat offender is a compromise indicator, not a flaky
  node — see `post-incident-forensics-for-suspected-key-compromise`.
- The engine performs **no replay detection**. The same `tx_hash` may be authorised
  twice; idempotency belongs to the caller — see `order-placement-idempotency` for the
  pattern.

## 4. Proactive secret sharing refresh

Refresh on cadence so an attacker cannot accumulate shards across an unbounded window.
Each refresh re-randomises every share **without changing the public key or wallet
address**, so downstream address allowlists are unaffected.

1. Run the library's refresh/resharing protocol across all `N` nodes.
2. Increment `current_key_epoch` and set `last_key_refresh_date`.
3. Every node re-attests on the new epoch. Until it does, a lagging node's attestation
   is denied at step 3 — which is the intended behaviour, not a bug: a share from a
   superseded generation cannot participate.
4. Adding or removing a party is a **resharing**, not a roster edit. Update the roster
   only alongside the protocol run that actually changed the group.

## 5. Audit

Persist every `MPCSigningAuthorizationReport`, denials included. The denials are the
valuable half: `MPC_UNAUTHORIZED_NODE` and `MPC_DUPLICATE_SHARD_ATTESTATION` are
attack indicators, and because MPC output is an ordinary single signature, this
off-chain record is the *only* evidence of who authorised what. See
`structured-logging-for-post-incident-forensics`.

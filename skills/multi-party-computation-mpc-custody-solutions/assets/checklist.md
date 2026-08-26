# Pre-Flight / Sign-off Checklist — multi-party-computation-mpc-custody-solutions

## Architecture boundary

- [ ] The signing protocol runs in a **maintained MPC library**, not in hand-rolled code.
- [ ] The policy layer produces **no signature and no signature-like value** — a
      hashed `(r, s)`-shaped pair is a fabricated value, not a signature.
- [ ] No component outside the shard nodes can receive key-share material. Attestations
      carry a non-secret commitment over the **public** key share only.
- [ ] Attestations arrive over authenticated transport (mutual TLS or signed
      attestations). The engine does not verify node identity.

## Shard roster and topology

- [ ] `N >= 3` and `2 <= t <= N`, recorded with the rationale for the chosen `t`.
- [ ] Every node carries an explicit `failure_domain` — the cloud **account**,
      provider, or operator whose compromise yields control. Two regions of one account
      are one domain.
- [ ] At least `t` distinct failure domains exist across the roster, so a compliant
      quorum is actually assemblable.
- [ ] `t = N` avoided, or the permanent-loss risk explicitly accepted: losing more than
      `N - t` shards makes the wallet unspendable forever.
- [ ] Confidentiality bound understood and recorded: an attacker holding `t` shards has
      full, silent signing capability.

## Library vulnerability posture

- [ ] Library and **exact version** recorded, not just the protocol name.
- [ ] If GG18 or GG20: patched against **CVE-2023-33241** — key generation validates a
      counterparty's Paillier modulus as a small-factor-free biprime via zero-knowledge
      proof. Unpatched, as few as 16 signatures leak the key.
- [ ] If GG18 or GG20: confirmed the library is still **maintained**; many affected
      implementations are not.
- [ ] Patched against **TSSHOCK** for any of CMP / GG18 / GG20 — audited CGGMP21
      libraries were also broken, so choosing CMP is not a mitigation.
- [ ] `implementation_hardened_against_cve_2023_33241` and
      `implementation_hardened_against_tsshock` set **only after** verifying the
      version — never to clear a denial.
- [ ] Understood that **no NIST-approved threshold signature scheme exists** (IR 8214C
      is a call for submissions, final January 2026); a vendor's FIPS certificate does
      not certify the MPC protocol.

## Proactive secret sharing (PSS)

- [ ] Refresh interval calibrated and recorded (`refresh_interval_days` default 90.0 has
      **no regulatory basis**).
- [ ] Refresh confirmed **not** to change the public key or wallet address, so
      downstream address allowlists are unaffected.
- [ ] `current_key_epoch` incremented and `last_key_refresh_date` set on every refresh.
- [ ] Every node re-attests on the new epoch after a refresh.
- [ ] Adding or removing a party done by **resharing**, never by editing the roster
      alone.
- [ ] Presignature stockpiles bounded and their lifetime recorded — a presignature is
      signing capability at rest.

## Per-ceremony gates

- [ ] Out-of-roster attestation **denies the whole request**, even when the remaining
      shards would have met the threshold.
- [ ] One attestation per node; a shared `share_commitment` across node ids denies as a
      cloned shard.
- [ ] Commitments derived deterministically from the public share
      (`derive_shard_attestation_commitment`) — a random per-ceremony value silently
      defeats cloned-shard detection.
- [ ] All attesting shards on the current key epoch.
- [ ] Attesting quorum spans the required number of distinct failure domains.

## Controls this engine does NOT provide

- [ ] Value tiering, timelock, and approver governance handled by
      `multi-signature-approval-for-large-transfers`.
- [ ] Destination allow-listing handled by `exchange-withdrawal-whitelist-enforcement`.
- [ ] Idempotency / replay protection handled by the caller — the engine is stateless
      and will authorise the same `tx_hash` twice.
- [ ] Accepted that policy is enforced **off-chain only**: the chain sees an ordinary
      single signature and records neither the quorum nor which shards signed.

## Run discipline

- [ ] `evaluation_date` passed explicitly so audit output is reproducible.
- [ ] Every report persisted, **denials included** — `MPC_UNAUTHORIZED_NODE` and
      `MPC_DUPLICATE_SHARD_ATTESTATION` are attack indicators, and this record is the
      only evidence of who authorised what.
- [ ] Identifiable-abort blame recorded where the protocol provides it (CGGMP21, GG20;
      GG18 does not) and repeat offenders escalated.
- [ ] Automated Testing: run
      `python -m unittest discover -s skills/multi-party-computation-mpc-custody-solutions/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Security / key-management owner sign-off: ___________________________
- Library version verified against CVE-2023-33241 and TSSHOCK by: ___________________________
- Date: ___________________________

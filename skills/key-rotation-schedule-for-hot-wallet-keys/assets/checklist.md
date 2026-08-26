# Pre-Flight Checklist — Hot Wallet Key Rotation

## Policy

- [ ] Each threshold (age, signature count, signed volume, grace hours) chosen deliberately,
      with the reasoning recorded — **not** inherited as a "requirement"?
- [ ] Confirmed that no standard mandates these figures, and that any audit response
      describes them as the firm's own policy?
- [ ] Age threshold justified against the factors that actually apply — key embodiment
      (process memory vs HSM), host exposure, transaction volume?
- [ ] Considered the cost of over-rotation: re-keying error risk, on-chain fees, a new
      published address each cycle?

## Key classification

- [ ] Every key classified `ONCHAIN_SIGNING` or `EXCHANGE_API`?
- [ ] Confirmed no on-chain key is mis-classified as an API key — the error that clears an
      irrevocable key for destruction while it still holds funds?
- [ ] A live balance source wired to `residual_balance_usd` for every on-chain key?

## Data hygiene

- [ ] All timestamps POSIX epoch **seconds**, not milliseconds?
- [ ] Host clocks synchronised, and skew tolerance set above normal NTP jitter?
- [ ] `last_used_timestamp_epoch` genuinely reflects last signing, not last poll?
- [ ] Signature counts and signed volume aggregated per key, not per wallet?

## Rotation execution

- [ ] Replacement key actually generated out of band — the engine only proposes a label?
- [ ] Replacement key naming scheme handles repeat rotations (`K_V2_V2` is what the default
      label produces)?
- [ ] New key deployed and the bot confirmed to be using it before the window closes?
- [ ] Address book, withdrawal whitelists, and any on-chain allowlists updated to the new
      address?
- [ ] At least one active key confirmed to remain for the wallet before shredding?

## Grace period

- [ ] `grace_period_hours` set above the slowest settlement path in use — chain finality,
      mempool residency, and venue reconciliation?
- [ ] `grace_period_started_epoch` recorded when rotation was initiated?
- [ ] Re-audits confirmed idempotent — the clock does not restart on each call?
- [ ] Any `warnings` about a key still signing after its grace start investigated? That means
      the cutover did not happen.

## Sweep before shred

- [ ] For every on-chain key: address balance confirmed at zero **on-chain**, not merely
      asserted, before the material is destroyed?
- [ ] Sweep transaction confirmed to the depth the chain warrants?
- [ ] Checked for assets the balance figure may miss — tokens, NFTs, staked or locked
      positions, pending rewards, contract allowances granted by this address?
- [ ] Confirmed no key is sitting in `REVOKED_SHREDDED` while still reported to hold value?
- [ ] If `dust_threshold_usd` was raised above zero, the amount and the reason recorded —
      and confirmed nobody instead wrote a false zero balance to clear the gate?

## Compromise path

- [ ] Compromised keys confirmed to receive **no** grace period?
- [ ] For a compromised on-chain key: sweep executed first, key material retained until it
      confirms, destroyed only afterwards?
- [ ] Venue-side revocation actually performed for API keys, and confirmed?
- [ ] Downstream credentials the compromised key protected also rotated?
- [ ] Forensics and disclosure handled separately from this engine's verdict?

## Audit trail

- [ ] Every report persisted against the key id in an append-only log?
- [ ] Recorded that a report is a decision, not evidence of execution — with the KMS, chain
      and venue confirmations stored alongside it?
- [ ] Review cadence set to re-check the thresholds and the source material?

## Out of scope — confirm covered elsewhere

- [ ] Cold and air-gapped key lifecycle handled separately?
- [ ] Multi-signature / MPC share resharing handled by a quorum procedure, not this engine?
- [ ] Zero-downtime credential reload in the running bot handled separately?
- [ ] API key permission scope minimised — a key without withdrawal rights beats a key
      rotated on schedule?
- [ ] Jurisdictional custody obligations (NYDFS, MiCA, MAS, VARA) confirmed against the
      entity's own licence conditions?

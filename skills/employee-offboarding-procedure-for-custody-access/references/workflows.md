# Workflows for Employee Offboarding for Custody Access

## 0. Before the clock starts

- Fix `termination_time_epoch` as the end of the last authorised session, not the
  time HR filed the record. Every SLA in the engine is measured from it.
- Build the access inventory from system-of-record sources (IdP groups, exchange
  key listings, custody platform signer sets, device register), never from the
  departing individual's own recollection. The engine can only report on what the
  inventory contains.
- If access is suspected already abused, stop: run the incident path first so the
  forensic trail survives rotation.

## 1. Credential revocation — SLA `credential_revocation_sla_hours` (default: immediate)

1. `IDP_SSO_REVOKED` — deactivate the IdP account, terminate live sessions and
   refresh tokens, and remove VPN/network access. Session revocation matters: a
   disabled account with a live session is still an authenticated user.
2. `EXCHANGE_API_KEYS_REVOKED` — delete every exchange API key the individual
   created or held, including keys embedded in bot configuration, CI secrets and
   shared vault entries. These are bearer credentials and survive SSO removal.
3. `CUSTODY_PORTAL_REVOKED` — remove custody platform users, roles, approval
   quorum membership, and any address-whitelist change rights.

## 2. Signing material rotation — SLA `key_rotation_sla_hours` (default 24h)

4. `MULTISIG_MPC_KEY_ROTATED` — remove the individual from the signer set and
   rotate the material:
   - **MPC**: confirm in writing whether the platform performs a proactive key
     refresh that invalidates previously held shares. A signer-list change without
     a refresh leaves the old share usable with the old set.
   - **Native multi-sig**: re-key or migrate to a new signer set. If the address
     changes, propagate new deposit instructions to counterparties, exchanges and
     settlement agents before publishing the change internally.
   - Re-verify the surviving M-of-N threshold: removing a signer without adding a
     replacement can leave the quorum unmeetable, or leave the remaining signers
     able to move funds unilaterally.

## 3. Device sanitisation and residual-secret assessment

5. `HARDWARE_TOKEN_WIPED` — collect and sanitise YubiKeys, HSM cards and hardware
   wallets, recording the method per NIST SP 800-88 Rev. 1. Then answer separately:
   could the seed or recovery phrase have been copied off-device? If yes, the wipe
   does not close the exposure — move the assets to freshly generated material.

## 4. Evaluate, escalate, retain

- Call `evaluate_offboarding_status(record, current_time_epoch=...)` on a fixed
  clock and act on `key_exposure_risk`, not on the percentage.
- Escalate `CRITICAL_KEY_EXPOSURE_RISK` and `HIGH_CREDENTIAL_EXPOSURE_RISK` to the
  on-call security owner rather than leaving them in a ticket queue.
- Waive a step only via `not_applicable_steps` with a written justification; the
  justification is what an auditor reads.
- Persist each `CustodyOffboardingAuditReport` as evidence, and re-run until the
  record reaches `LOW_RISK`.

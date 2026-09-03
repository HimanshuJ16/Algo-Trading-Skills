---
name: employee-offboarding-procedure-for-custody-access
description: >-
  Use when someone with digital asset custody access leaves, scoring single sign-on
  revocation, exchange API key destruction, custody portal removal and signing-key
  rotation into an auditable attestation record.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: offboarding-procedure, custody-security, key-rotation, mpc-custody, multi-sig, api-key-revocation, soc-2
  brokers_frameworks: "NIST SP 800-53 Rev. 5 PS-4; AICPA Trust Services Criteria CC6.2/CC6.3; 23 NYCRR 500.7; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when someone with digital asset custody access leaves a crypto fund, a custodian, or an exchange-connected trading desk — a key custodian, a DevOps engineer holding exchange API credentials, a trader with custody portal rights. It turns the offboarding checklist into an auditable record: which steps were attested complete, which genuinely did not apply and why, and which live access has passed its revocation SLA and needs escalating rather than sitting in a ticket queue.

It is designed for the gap that actually causes incidents — the hours or days between HR recording a termination and every credential and signing key actually being dead.

## When NOT to Use

- **As an execution tool.** The engine revokes nothing. It calls no identity provider, exchange, or custody platform API, and cannot verify that an attested step happened. A tick is an operator's assertion; the evidence sits in the IdP log, the exchange key-deletion confirmation, and the custody platform's signer-set change record.
- **As a compliance determination.** It reports against *your* configured SLA. No regulator publishes a numeric deadline for revoking custody access (see `references/standards.md`), so a green report is evidence for a control review, not proof of compliance with any specific rule.
- **For involuntary or hostile departures where access is suspected already abused.** Treat that as an incident: freeze and investigate before rotating, so the forensic trail survives. See `post-incident-forensics-for-suspected-key-compromise`.
- **For contractor and vendor access, or service accounts the individual created.** These are outside the five-step model and are a frequent source of orphaned access; audit them separately with `api-key-least-privilege-audit-tool`.

## Prerequisites

- An access inventory built independently of the departing individual's own account listing: IdP groups, exchange API keys they created (including keys living in bot configs and CI secrets), custody portal roles, signer slots, issued hardware tokens.
- `employee_id`, `employee_name`, `role`, and `termination_time_epoch` — the moment access *should* have stopped working (normally the end of the last authorised session), not when HR filed the record.
- `held_custody_keys`: whether the individual held a private key, an MPC key share, or a multi-sig signer slot. This drives the critical escalation and cannot be waived once true.
- A documented SLA policy. The engine defaults — `credential_revocation_sla_hours=0.0` (immediate) and `key_rotation_sla_hours=24.0` — are **firm policy defaults with no regulatory basis**.

## Workflow

1. **Build the Record From Inventory, Not From the Leaver**: Populate `EmployeeOffboardingRecord` from the independent inventory. Anything not in the inventory will never appear as pending, so the engine's silence about an unknown API key is not assurance.
2. **Attest Steps Only Against Evidence**: Append to `completed_steps` only when the confirming artefact exists. An unrecognised step string raises `CustodyOffboardingError` rather than counting as progress — a typo must not become a green tick.
3. **Waive Deliberately, Never Silently**: A step that truly does not apply goes in `not_applicable_steps` with a written justification and leaves the denominator, so 100% means "everything applicable was done". `IDP_SSO_REVOKED` can never be waived, and `MULTISIG_MPC_KEY_ROTATED` cannot be waived while `held_custody_keys` is True.
4. **Evaluate on a Fixed Clock**: Call `evaluate_offboarding_status(record, current_time_epoch=...)` explicitly so the report is reproducible. A future-dated termination (pre-planned departure, or HR-to-host clock skew) yields negative elapsed hours and nothing overdue — access is not late before it is due.
5. **Act on the Risk Class, Not the Percentage**: `CRITICAL_KEY_EXPOSURE_RISK` (signing material un-rotated past SLA) outranks `HIGH_CREDENTIAL_EXPOSURE_RISK` (live SSO/API/portal credential past SLA), which outranks `ELEVATED_ROTATION_PENDING` (rotation open but within SLA) and `PENDING_LOW_RISK`. Only a fully attested record returns `LOW_RISK`. A 60% score with nothing overdue is a healthier state than an 80% score with a live exchange key.
6. **Retain the Report**: Persist each `CustodyOffboardingAuditReport` — completion, pending, overdue and waived steps with the evaluation clock — as the evidence a SOC 2 auditor samples against the termination list.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating SSO Revocation as Covering Exchange Keys**: An exchange API key is a standalone bearer credential. It keeps trading after the IdP account is disabled, because it never authenticated through the IdP. Enumerate keys at the exchange and in every bot config and secrets store — not from the leaver's memory.
- **Reporting an Un-Revoked Credential as Low Risk Because No Key Was Held**: A departing engineer with no key share but a live API key can still move assets to a whitelisted address. Risk follows what is still live, not the person's title.
- **Wiping the Hardware Wallet and Calling the Seed Retired**: Sanitising a YubiKey or resetting a Ledger destroys the device's copy — not any seed phrase, paper backup, or photograph the holder could have taken. If the seed was ever exportable or backed up by hand, assume it is retained and move the assets to freshly generated material; a wipe attestation alone does not close that exposure.
- **Assuming an MPC Reshare Invalidates the Old Share**: Removing a signer only helps if the protocol performs a proactive key refresh that renders previously held shares useless with the new set. If the platform merely changes a policy or signer list without refreshing shares, the old share still combines with the old set. Confirm which one your custody platform actually does, in writing.
- **Forgetting That On-Chain Re-Keying Changes Deposit Addresses**: Rotating a native multi-sig to a new signer set usually means a new address. Deposit instructions held by counterparties, exchanges and settlement agents must be updated, or funds arrive at an address whose signer set includes the person you just offboarded.
- **Back-Dating or Front-Dating the Termination Clock**: Using the HR record's creation time instead of the effective end of authorised access silently shifts every SLA measurement, in whichever direction flatters the report.
- **Reading 100% as Verified**: The score measures attestations, not reality. Auditors test the opposite direction — they take the termination list and hunt for still-active accounts.

## Verification

- Attest all five steps for a key custodian one hour after termination and confirm `completion_percentage == 100.0`, `is_fully_compliant`, `LOW_RISK`, and `hours_since_termination == 1.0`.
- Submit `completed_steps` containing the misspelling `"MULTISIG_MPC_ROTATED"` and confirm `CustodyOffboardingError` — the pre-2.0 engine counted it, reporting 120% completion alongside four pending steps.
- Leave `EXCHANGE_API_KEYS_REVOKED` pending 100 hours after termination for someone with `held_custody_keys=False` and confirm `HIGH_CREDENTIAL_EXPOSURE_RISK` with that step in `overdue_steps` — the pre-2.0 engine reported `LOW_RISK`.
- Hold rotation open at exactly 24.0 hours and confirm `ELEVATED_ROTATION_PENDING`; add one second and confirm `CRITICAL_KEY_EXPOSURE_RISK` — the SLA boundary is exclusive.
- Waive `EXCHANGE_API_KEYS_REVOKED` with a justification and confirm the denominator drops to four applicable steps; then waive it with a blank justification, waive `IDP_SSO_REVOKED`, or waive rotation for a key holder, and confirm each raises.
- Set `termination_time_epoch` a week in the future and confirm `overdue_steps == []` with negative `hours_since_termination`.
- Run `python -m unittest discover -s skills/employee-offboarding-procedure-for-custody-access/scripts` and confirm a 100% pass rate.

## Related Skills

- `key-rotation-schedule-for-hot-wallet-keys`
- `segregation-of-duties-for-custody-operations`
- `api-key-least-privilege-audit-tool`
- `post-incident-forensics-for-suspected-key-compromise`
- `hot-cold-wallet-split-for-trading-bots`

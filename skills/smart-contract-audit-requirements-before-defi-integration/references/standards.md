# Standards — smart-contract-audit-requirements-before-defi-integration

## Jurisdiction and scope

**No regulator mandates any threshold in this skill.** There is no rule requiring an
allocator to obtain a smart contract audit before integrating with a DeFi protocol,
no prescribed timelock duration, no prescribed multisig configuration, and no
prescribed bug bounty size. Everything below is either a firm policy input or an
industry convention with a traceable origin, and it is labelled as such.

The nearest regulatory touchpoints are indirect and do not supply numbers:
EU MiCA does not contain a smart contract audit obligation for DeFi integration, and
its DeFi exclusion turns on whether an offering is technically *and* governance-wise
decentralised — a treasury multisig or an upgrade authority typically defeats it. Do
not cite a regulator for any threshold here. Cite your own investment committee.

## Why an audit is not a safety property

Audit firms disclaim reliance in their own terms. From OpenZeppelin's Terms of
Service, section 8.8:

> "The content contained in the Reports is current as of the date appearing on the
> Report and are subject to change without notice."
>
> "The Reports and any related analysis of a Protocol or other project are provided
> for informational purposes only do not constitute statements, representations or
> warranties by OpenZeppelin in any respect, including regarding the security of such
> Protocol."
>
> "You may not rely on the Reports in any way, including for the purpose of making
> any decisions to use a Protocol, a product or service, or buy or sell any digital
> asset."

Two consequences the engine encodes:

- An audit is **current as of its date** and scoped to a **specific commit**. It says
  nothing about the bytecode deployed at your target address today. Hence
  `AuditReport.scope_covers_deployed_code`, which must be attested per report and
  does not qualify when left `None`.
- "Audited" is not a conclusion. It is one input to a judgement that remains yours.

There is also **no industry-standard ranking of audit firms** and no body that
certifies one. `AuditFirmTier` records *your roster's* judgement. Maintain the roster
explicitly and record why each firm sits where it does.

Source: [OpenZeppelin — Terms of Service](https://www.openzeppelin.com/tos).

## Timelock — where 48 hours comes from

Compound's `Timelock.sol` hard-codes the governance delay bounds:

| Constant | Value |
|---|---|
| `MINIMUM_DELAY` | 2 days |
| `MAXIMUM_DELAY` | 30 days |
| `GRACE_PERIOD` | 14 days |

The delay is settable by governance anywhere in that range and has historically sat
at the 2-day minimum. Because Governor Bravo / Timelock is one of the most-forked
governance stacks in DeFi, 2 days became the de facto floor — which is the entire
provenance of the 48-hour figure. It is a **convention, not a requirement**.

Calibrate it rather than inheriting it. Major protocols sit below 48 hours: Aave
governance imposes **either a 1-day or a 7-day delay** depending on the proposal
level, so a blanket 48h floor rejects its 1-day tier.

Two facts a delay value cannot express, both of which must be checked by hand:

- **The timelock must actually own the proxy admin.** A timelock contract that does
  not hold the admin role protects nothing.
- **A delay nobody watches is not protection.** The window is only useful if a queued
  upgrade raises an alert with enough lead time to withdraw.

Sources: [compound-finance/compound-protocol — `contracts/Timelock.sol`](https://github.com/compound-finance/compound-protocol/blob/master/contracts/Timelock.sol),
[Compound v2 Docs — Governance](https://docs.compound.finance/v2/governance/),
[Aave — Governance](https://aave.com/docs/ecosystem/governance).

## Emergency / guardian roles bypass the timelock by design

A pause or guardian role exists precisely so someone can act *without* waiting out
the delay. Aave runs two such multisigs: a **Protocol Emergency Guardian (4-of-7)**
holding the `EMERGENCY_ADMIN` role, and a **Governance Emergency Guardian (5-of-9)**
able to veto malicious payloads.

This is sound design, and the absence of a circuit breaker is its own exposure —
nothing can stop an exploit mid-drain. But it cuts both ways, which is why the engine
raises an advisory either way:

| Spec | Advisory | Why |
|---|---|---|
| `has_emergency_pause_circuit_breaker=True` | `UNTIMELOCKED_GUARDIAN` | Confirm by hand that the role is multisig-held and **pause-only**. A guardian that can also upgrade or set parameters makes the timelock decorative. |
| `has_emergency_pause_circuit_breaker=False` | `NO_CIRCUIT_BREAKER` | No way to halt an in-progress exploit; the delay slows your defensive response too. |

Sources: [Aave — ACL Manager](https://aave.com/docs/aave-v3/smart-contracts/acl-manager),
[Aave — Governance](https://aave.com/docs/ecosystem/governance).

## Multisig — M-of-N, not M

Floors follow the Security Alliance (SEAL) **Secure Multisig Best Practices**:

| SEAL guidance | Modelled as | Blocking? |
|---|---|---|
| Minimum of 3 signers | `min_multisig_threshold` (default 3) | Yes |
| Signing threshold of at least 50% | `min_multisig_threshold_ratio` (default 0.5) | Yes |
| "Avoid `N-of-N` schemes, as the loss of a single key would result in a permanent loss of access to all funds" | `threshold == signers` check | Yes |
| 7+ signers for multisigs holding $1M+ | `SEAL_LARGE_TREASURY_MIN_SIGNERS` | No — advisory `SMALL_SIGNER_SET` |
| Hardware wallets, different models/manufacturers, geographic separation, distinct individuals, ≥1 signer external to the organisation | **not modelled** | Advisory `SIGNER_INDEPENDENCE_UNVERIFIED`, always raised |

The `min_multisig_signers` default of 5 is this repo's own floor, matching the
"3-of-5" figure this skill has always documented; SEAL's own numeric minimum is 3
signers at ≥50%.

The last row is the one that matters most and the one no engine can check. Signer
*count* is not signer *independence*: an M-of-N whose keys sit with one team, on one
hardware model, in one location is effectively 1-of-1.

A spec where M exceeds N cannot exist on-chain and raises `DeFiDueDiligenceError`
rather than scoring — it is a reviewer data-entry error, not a protocol deficiency,
and an audit trail must never conflate the two.

Source: [SEAL Frameworks — Secure Multisig Best Practices](https://frameworks.securityalliance.org/wallet-security/secure-multisig-best-practices/).

## Bug bounty — an incentive, not a badge

Immunefi's **"A DeFi Security Standard: The Scaling Bug Bounty"** (published
2024-09-02) proposes pricing the maximum critical payout as a percentage of the
economic damage a bug would cause, with **10% of TVL at risk** as the starting point
— $1M for $10M at risk. Immunefi explicitly frames it as a proposal and an experiment
"to be adjusted up … or down", not a standard anyone is bound by.

The engine therefore treats the ratio as **advisory and the absolute floor as
blocking**:

| Check | Default | Blocking? |
|---|---|---|
| Active programme exists | — | Yes |
| Max critical payout ≥ `min_bug_bounty_usd` | $100,000 | Yes |
| Max critical payout ÷ TVL ≥ `min_bug_bounty_tvl_ratio` | 0.10 | No — advisory `BOUNTY_SMALL_VS_TVL` |

Blocking on the ratio would be false precision: 10% of a multi-billion-dollar pool is
a sum essentially no protocol funds, so the gate would reject every protocol worth
integrating with and train reviewers to switch it off. `bug_bounty_tvl_coverage_ratio`
is reported on every evaluation as a relative risk signal, never as a target.

Source: [Immunefi — A DeFi Security Standard: The Scaling Bug Bounty](https://immunefi.com/blog/industry-trends/a-defi-security-standard-the-scaling-bug-bounty/).

## Engineering defaults (no external basis)

| Parameter | Default | Provenance |
|---|---|---|
| `min_tier1_audits_required` | 2 | Diversification-of-reviewers heuristic. None. |
| `min_mainnet_days` | 90 | Battle-testing proxy. None. Measure from the **current implementation's** deployment, not the protocol's launch. |
| `min_timelock_hours` | 48.0 | Compound `Timelock.MINIMUM_DELAY = 2 days`, by convention. |
| `min_bug_bounty_usd` | 100,000.0 | Absolute floor. None. |
| `min_bug_bounty_tvl_ratio` | 0.10 | Immunefi scaling-bounty proposal. Advisory only. |
| `min_multisig_threshold` | 3 | SEAL minimum signer count. |
| `min_multisig_signers` | 5 | Repo floor ("3-of-5"). |
| `min_multisig_threshold_ratio` | 0.5 | SEAL ≥50% threshold. |
| `max_audit_age_days` | 365 | Staleness advisory only. None. |

`safety_score_pct` is an unweighted fraction of the six gates passed. All six are
mandatory, so the score is a **remediation progress** indicator, not a risk appetite
dial — an 83.33% protocol is blocked exactly as firmly as a 16.67% one. Do not build
a "score ≥ N" approval rule on top of it.

## Behaviour change in 2.0.0

`AuditReport.scope_covers_deployed_code` defaults to `None`, and `None` does not
qualify. Profiles written against 1.0.0 will newly fail `INSUFFICIENT_AUDITS` with a
message naming the unattested reports. This is deliberate fail-closed behaviour: the
previous version counted any Tier-1 audit regardless of whether it covered the code
being funded. Attest the field per report rather than working around it.

Also changed in 2.0.0: `evaluate_protocol` takes an optional `assessment_date`;
invalid specs raise `DeFiDueDiligenceError` instead of being scored; audits reporting
zero Critical/High findings are no longer flagged as having unresolved findings; and
`tvl_usd`, `admin_multisig_signers_count`, `has_emergency_pause_circuit_breaker`, and
`audit_date_iso` are now read (in 1.0.0 all four were declared and ignored).

---
name: smart-contract-audit-requirements-before-defi-integration
description: >-
  Use before routing capital through a DeFi protocol you do not control, checking that
  independent audits cover the code actually deployed at that address and that critical
  findings were remediated. It scores assertions, it finds no bugs.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: smart-contract-audit, defi-integration, audit-scope-verification, timelock-governance, multisig-threshold, bug-bounty, protocol-counterparty-risk
  brokers_frameworks: "Immunefi Scaling Bug Bounty; SEAL Secure Multisig Best Practices; Compound Timelock; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system is about to route capital through a DeFi protocol it does not control — a lending pool it supplies to, a DEX it holds LP positions in, a yield vault it deposits into, a staking contract it locks into. The protocol becomes an unsecured counterparty whose failure mode is total and instant, so the integration decision needs the same evidence trail as any other counterparty approval.

The engine turns a documented protocol profile into a pass/fail verdict across six mandatory gates, with named blocking violations and non-blocking advisories. It exists to make three specific things impossible to skip:

1. **Audit scope.** An audit reviews a *specific commit*. Nothing connects that commit to the bytecode at the address you are about to fund. `scope_covers_deployed_code` must be attested per report, and an unattested audit does not count.
2. **Governance reachability.** A timelock delay and an M-of-N multisig are only protective in combination with facts a boolean cannot express — that the timelock owns the proxy admin, that the N keys are independently held.
3. **Bounty economics.** A bug bounty is an incentive, not a badge. It only works when reporting pays better than exploiting, which is a question about TVL.

## When NOT to Use

- **As a security assessment.** This reads no bytecode, runs no simulation, and finds no vulnerabilities. It scores assertions a human has already verified against artefacts. Commission an audit; do not substitute this for one.
- **As grounds to treat an approved protocol as safe.** Audit firms disclaim exactly this. OpenZeppelin's Terms of Service (s. 8.8) state their reports "do not constitute statements, representations or warranties … including regarding the security of such Protocol" and that "[y]ou may not rely on the Reports in any way, including for the purpose of making any decisions to use a Protocol". An approval here means the protocol clears your firm's floor, nothing more.
- **For protocols you control.** Your own contracts need a development-lifecycle security programme, not a counterparty gate.
- **For custodians, CEXs, or bridges.** A centralised custodian is a legal-entity question (`custody-solution-vendor-due-diligence-checklist`); a bridge carries validator-set and message-verification risk this does not model (`cross-chain-bridge-risk-for-multi-chain-strategies`).
- **As continuous monitoring.** This is a point-in-time gate. Governance changes, proxies get upgraded, and bounties get quietly cancelled after approval — re-run it, and monitor the timelock queue in between.

## Prerequisites

- A `DeFiProtocolSpec` where every field traces to an artefact you have read, not to the protocol's landing page: the audit reports themselves, the verified source of the deployed contract, the governance contracts read on-chain.
- Per `AuditReport`, an explicit `scope_covers_deployed_code` decision. `None` means nobody checked and is treated as not qualifying.
- `tvl_usd` and `admin_multisig_signers_count`, both of which are now load-bearing.
- A calibrated threshold policy. **The engine defaults have no regulatory basis** — no regulator mandates a smart contract audit, a timelock duration, a signer count, or a bounty size for a fund integrating a DeFi protocol. Provenance for each default is in `references/standards.md`.
- An explicit `assessment_date` for reproducible output.

## Workflow

1. **Match each audit to the deployed bytecode before counting it.** Take the commit named in the report's scope section, and compare it against the verified source at `contract_address` — including the *implementation* behind any proxy, not the proxy itself. Set `scope_covers_deployed_code` from that comparison. A Tier-1 firm's logo on a protocol's website is not evidence that the firm reviewed the code you are funding. Tier assignment is your roster's judgement: there is no industry-standard ranking of audit firms and no body that certifies one.
2. **Separate "no findings" from "findings not fixed".** The engine only demands remediation and fix verification from audits that actually reported Critical or High findings; a clean report has nothing to remediate. Do not attest `all_critical_high_remediated` from the protocol's own changelog — require the auditor's fix-verification report.
3. **Check that the timelock is reachable and that the guardian is scoped.** A 48-hour delay is worthless if the proxy admin is still an EOA, if nobody monitors the queue (a delay you do not watch is a delay you do not benefit from), or if a guardian role can upgrade instead of merely pause. The engine raises `UNTIMELOCKED_GUARDIAN` whenever a pause exists precisely because that role acts *outside* the delay; confirm by hand that it is pause-only and multisig-held. Where a protocol has no pause at all, `NO_CIRCUIT_BREAKER` records the opposite exposure — nothing can stop an exploit mid-drain.
4. **Read the multisig as M-of-N, not as M.** The gate blocks a threshold below 3, a signer set below 5, any N-of-N scheme (one lost key permanently locks admin control), and any threshold below 50% of signers. A spec where M exceeds N raises rather than scores — that configuration cannot exist on-chain and is a data-entry error, not a finding. What the engine cannot see is key custody: an M-of-N whose keys sit with one team, on one hardware model, in one location is effectively 1-of-1, which is why `SIGNER_INDEPENDENCE_UNVERIFIED` is always raised.
5. **Judge the bounty against TVL, then decide with your eyes open.** The blocking check is the absolute floor; the TVL ratio is reported as `bug_bounty_tvl_coverage_ratio` and raises `BOUNTY_SMALL_VS_TVL` when it falls below the reference ratio. The ratio deliberately does not block — Immunefi's 10%-of-funds-at-risk proposal is an incentive-design argument, and 10% of a large pool is a number essentially no protocol funds. Blocking on it would reject every protocol worth integrating with and teach reviewers to disable the gate.
6. **Disposition every advisory, then record the verdict.** `is_approved` requires all six gates. `safety_score_pct` is remediation progress, not risk appetite: an 83% protocol is blocked exactly as firmly as a 17% one.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting an audit that never covered the deployed code.** This is the failure mode, not an edge case. Audits are scoped to a commit hash; protocols ship after the audit, upgrade proxies, patch parameters, and keep the badge. Verify the audited commit against the verified source of the *implementation* contract, and re-verify after every upgrade. An audit whose scope you have not checked is worth exactly as much as no audit.
- **Reading "audited by Trail of Bits" as "safe".** The firms themselves disclaim reliance (OpenZeppelin ToS s. 8.8). An audit is a time-boxed review by humans under a deadline, and coverage limitations are normal and disclosed. Two audits reduce the chance a whole class of bug was missed; they do not make the contract correct.
- **Counting the multisig threshold and ignoring the signer set.** 3-of-3 clears a naive "threshold ≥ 3" check while being strictly worse than 3-of-5: one lost key permanently locks admin control. 3-of-9 clears it too, at a 33% signing threshold. And 4-of-2 is not a strong multisig, it is a typo — the engine raises on it rather than approving it, which the previous version did.
- **Trusting a timelock nobody watches.** The delay buys you an exit window only if a queued malicious upgrade generates an alert with enough lead time to withdraw. Without monitoring on the timelock queue, a 48-hour delay and a zero-hour delay have identical outcomes for you.
- **Assuming a timelock covers every privileged action.** Guardian, pause, and emergency-admin roles exist precisely to act without delay — Aave's Protocol Emergency Guardian holds `EMERGENCY_ADMIN` behind a 4-of-7 multisig. That is a reasonable design, but you must confirm the role cannot upgrade or re-parameterise, or the delay is decorative.
- **Applying one timelock floor to every protocol.** 48 hours comes from Compound's `Timelock.sol`, which hard-codes `MINIMUM_DELAY = 2 days` and is widely forked. It is a de facto ecosystem convention, not a rule, and major protocols sit below it — Aave governance imposes either a 1-day or a 7-day delay depending on proposal level, so a blanket 48h floor rejects its 1-day tier. Calibrate deliberately rather than inheriting the default.
- **Measuring mainnet longevity from the brand, not the bytecode.** "Live since 2021" means nothing if the implementation behind the proxy was replaced last week. `mainnet_days_active` must be days since the *currently deployed implementation* went live.
- **Treating a headline bounty figure as coverage.** A $100k maximum payout beside a $5B pool means a researcher holding a critical bug is paid roughly 50,000× better by exploiting it. Also confirm the programme is funded and live on its platform, that the maximum is not "up to" language with a discretionary sub-limit, and that your integration is in scope — the assets at risk are yours whether or not the protocol's scope statement mentions them.

## Verification

- Evaluate a protocol clearing all six gates with a fixed `assessment_date` and confirm `is_approved is True`, `safety_score_pct == 100.0`, and no blocking violations.
- Flip one `scope_covers_deployed_code` to `None` and confirm `INSUFFICIENT_AUDITS` reporting "Found 1 Tier-1 audit(s)" and naming the unattested report; flip it to `False` and confirm the message instead names the deployed address.
- Submit two clean audits (0 Critical, 0 High) with `all_critical_high_remediated=False` and confirm **no** `UNRESOLVED_VULNERABILITIES` — a report that found nothing has nothing to remediate.
- Submit 5-of-5, 3-of-9, and 3-of-4 multisigs and confirm each raises `WEAK_MULTISIG` for N-of-N, sub-majority threshold, and too few total keys respectively; confirm 3-of-5 passes.
- Submit a 4-of-2 multisig, a negative `tvl_usd`, a NaN, an empty `audits` list, an unparseable `audit_date_iso`, or an audit dated after the assessment, and confirm `DeFiDueDiligenceError` rather than a score.
- Submit `has_active_bug_bounty=False` with a $500,000 payout and confirm the violation reads "No active bug bounty program" rather than comparing the payout figure.
- Submit a $1M bounty against $10B TVL and confirm `bug_bounty_tvl_coverage_ratio == 0.0001` with a `BOUNTY_SMALL_VS_TVL` advisory and no block; submit `tvl_usd=0.0` and confirm a `None` ratio and no division error.
- Run `python -m unittest discover -s skills/smart-contract-audit-requirements-before-defi-integration/scripts` and confirm a 100% pass rate.

## Related Skills

- `smart-contract-approval-scope-minimization`
- `custody-solution-vendor-due-diligence-checklist`
- `cross-chain-bridge-risk-for-multi-chain-strategies`
- `on-chain-transaction-monitoring-for-anomalies`
- `decentralized-exchange-dex-integration-uniswap-style`

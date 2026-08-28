# Pre-Flight / Sign-off Checklist — smart-contract-audit-requirements-before-defi-integration

## Target fixed

- [ ] `assessment_date` recorded and passed explicitly, so the run is reproducible.
- [ ] `contract_address` pinned to the contract capital actually reaches.
- [ ] For a proxy: the **implementation** address behind it recorded separately.

## Audit reports obtained (documents, not badges)

- [ ] Full audit reports obtained — not a summary page, a badge, or a firm's logo.
- [ ] At least 2 reports from firms on **your** Tier-1 roster (there is no
      industry-standard ranking of audit firms; the roster and its rationale are
      yours to maintain and record).
- [ ] Report date, Critical count, and High count taken from each PDF.
- [ ] Auditor's **fix verification / retest** report obtained for every Critical and
      High finding — the protocol's own changelog does not count.

## Audit scope matched to deployed code — the step most often skipped

- [ ] Commit hash or tag identified in each report's scope section.
- [ ] Verified source of the **implementation** contract fetched from a block
      explorer or source-verification service.
- [ ] Commit compared against deployment; `scope_covers_deployed_code` set to `True`
      or `False` from that comparison — **never left `None`**.
- [ ] Post-audit commits, unaudited library upgrades, and initialiser/parameter
      changes specifically checked for.

## Governance read on-chain

- [ ] Proxy admin owner read from the admin slot — confirmed to be the timelock and
      **not an EOA**.
- [ ] Delay taken from the timelock actually in the upgrade path →
      `admin_timelock_delay_hours`.
- [ ] Threshold **and** total signer count read from the multisig itself →
      `admin_multisig_threshold_required`, `admin_multisig_signers_count`.
- [ ] Not an N-of-N scheme (one lost key would permanently lock admin control).
- [ ] Threshold is at least 50% of signers.
- [ ] **Signer independence assessed by hand** — different individuals, hardware
      models, and locations; ideally ≥1 signer external to the team. The engine
      cannot see this and always raises `SIGNER_INDEPENDENCE_UNVERIFIED`.
- [ ] Every guardian / pause / emergency-admin role enumerated, confirmed
      multisig-held, and confirmed **pause-only** — a guardian that can upgrade or
      re-parameterise makes the timelock decorative.
- [ ] Timelock-queue monitoring and alerting in place. **A delay nobody watches is
      not protection.**

## Deployment age

- [ ] `mainnet_days_active` taken from the deployment/upgrade transaction of the
      **current implementation**, not from the protocol's launch date.

## Bug bounty

- [ ] Programme confirmed currently listed, active, and funded on its platform.
- [ ] Maximum critical payout for smart contract bugs recorded; "up to" language and
      discretionary sub-limits checked.
- [ ] Payout not denominated in the protocol's own token (which would correlate with
      the exploit that triggers the claim).
- [ ] Current `tvl_usd` recorded, and `bug_bounty_tvl_coverage_ratio` reviewed as a
      relative signal — the 10% reference ratio is advisory and essentially no
      protocol clears it.
- [ ] Your integration path confirmed to be within the programme's stated scope.

## Run discipline

- [ ] Threshold policy calibrated and the calibration recorded. **The engine defaults
      have no regulatory basis** — no regulator mandates an audit, a timelock
      duration, a signer count, or a bounty size here.
- [ ] Any `DeFiDueDiligenceError` treated as a reviewer data-entry error and fixed —
      never recorded as a protocol rejection.
- [ ] Every advisory dispositioned: `STALE_AUDIT`, `UNTIMELOCKED_GUARDIAN` /
      `NO_CIRCUIT_BREAKER`, `SMALL_SIGNER_SET`, `SIGNER_INDEPENDENCE_UNVERIFIED`,
      `BOUNTY_SMALL_VS_TVL`.
- [ ] `safety_score_pct` **not** used as an approval threshold — all six gates are
      mandatory, and the score is remediation progress, not risk appetite.
- [ ] Re-run scheduled on a fixed cadence, plus triggers on implementation upgrade,
      multisig owner/threshold change, and bounty delisting.
- [ ] Automated Testing: run `python -m unittest discover -s skills/smart-contract-audit-requirements-before-defi-integration/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Approved capital limit for this protocol: ___________________________
- Next scheduled re-run: ___________________________
- Date: ___________________________

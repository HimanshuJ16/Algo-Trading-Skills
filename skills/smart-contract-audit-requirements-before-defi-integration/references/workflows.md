# Workflows — smart-contract-audit-requirements-before-defi-integration

The engine scores a profile. This file describes the human work that produces the
profile, which is where the actual due diligence happens. Every boolean below is an
assertion you must be able to defend from an artefact.

## 1. Fix the assessment date and the target address

Record the `assessment_date` you are evaluating against, and pin `contract_address`
to the contract capital actually reaches. For a proxy, note **both** the proxy and
the implementation it currently points at — every scope check below is against the
implementation.

Passing `assessment_date` explicitly makes the run reproducible; leaving it to
default to today makes an audit trail that cannot be re-derived later.

## 2. Ingest audit reports as documents

For each report, from the PDF and not from a summary page:

- Firm name, and the tier **your roster** assigns it (there is no industry-standard
  ranking; record why the firm sits where it does).
- Report date → `audit_date_iso`.
- Count of Critical and High findings → `critical_findings_count`,
  `high_findings_count`.
- Whether the auditor issued a **fix verification / retest report** covering those
  findings → `fix_verification_confirmed`. The protocol's changelog is not evidence.

Reject reports you cannot obtain in full. A logo on a landing page is not an audit.

## 3. Match every audit's scope to the deployed implementation

This is the step that does the real work, and the one most often skipped.

1. Find the commit hash or tag named in the report's scope section.
2. Fetch the verified source of the **implementation** contract at
   `contract_address` (block explorer verified source, or a source-verification
   service).
3. Compare. Post-audit commits, unaudited library upgrades, and parameter or
   initialiser changes all break the link between the report and the deployment.

Then set `scope_covers_deployed_code`:

| Value | Meaning | Effect |
|---|---|---|
| `True` | You compared and they match | Counts toward the Tier-1 requirement |
| `False` | You compared and they differ | Excluded; message names the deployed address |
| `None` | Nobody has compared | Excluded; message says "never attested" |

`None` and `False` are both non-qualifying but are **different remediation items**:
one needs an hour of work, the other needs a new audit.

## 4. Read governance on-chain, not from documentation

- **Who owns the proxy admin?** If it is an EOA, the timelock is decorative
  regardless of its configured delay. Read the admin slot.
- **What delay does the timelock that owns the admin enforce?** →
  `admin_timelock_delay_hours`. Use the timelock in the upgrade path, not whichever
  timelock the docs mention.
- **What is the M-of-N of the multisig on that path?** →
  `admin_multisig_threshold_required`, `admin_multisig_signers_count`. Read the Safe
  (or equivalent) directly.
- **Which roles act outside the timelock?** Enumerate guardian / pause /
  emergency-admin holders and confirm each is scoped to pause-only and multisig-held.
  Set `has_emergency_pause_circuit_breaker` from whether a pause exists — then read
  the advisory the engine raises and act on it, because a guardian that can upgrade
  or re-parameterise defeats the delay entirely.
- **Who holds the N keys?** The engine cannot see this. Concentration by team,
  hardware model, or location collapses an M-of-N toward 1-of-1.

## 5. Establish deployment age, not brand age

`mainnet_days_active` is days since the **currently deployed implementation** went
live. Take it from the deployment or upgrade transaction of the implementation, not
from the protocol's launch announcement. A protocol "live since 2021" that upgraded
last week has one week of battle-testing on the code you are funding.

## 6. Verify the bounty programme is live, funded, and in scope

- Confirm the programme is currently listed and active on its platform.
- Take the **maximum critical payout** for smart contract vulnerabilities →
  `bug_bounty_max_payout_usd`. Watch for "up to" language with discretionary
  sub-limits, and for payouts denominated in the protocol's own token, which
  correlates with the exploit that would trigger the claim.
- Record current `tvl_usd`. The engine reports `bug_bounty_tvl_coverage_ratio` and
  raises `BOUNTY_SMALL_VS_TVL` below the reference ratio; the ratio is advisory
  because essentially no protocol funds 10% of TVL.
- Check whether your integration path is in the programme's stated scope. Your
  capital is at risk whether or not it is.

## 7. Run the gate and disposition the output

```python
from datetime import date
from smart_contract_audit_requirements_before_defi_integration import (
    SmartContractAuditRequirementsBeforeDeFiIntegrationEngine,
)

engine = SmartContractAuditRequirementsBeforeDeFiIntegrationEngine()
report = engine.evaluate_protocol(protocol, assessment_date=date(2026, 8, 28))
```

| Output | Meaning |
|---|---|
| `is_approved` | True only when all six gates pass |
| `blocking_violations` | Named codes: `INSUFFICIENT_AUDITS`, `UNRESOLVED_VULNERABILITIES`, `UNTESTED_CODEBASE`, `DANGEROUS_TIMELOCK`, `WEAK_MULTISIG`, `INADEQUATE_BUG_BOUNTY` |
| `advisories` | Non-blocking, but each needs a dispositioning decision |
| `safety_score_pct` | Fraction of gates passed. **Remediation progress, not risk appetite** — never build a "score ≥ N" approval rule on it |
| `bug_bounty_tvl_coverage_ratio` | Payout ÷ TVL, or `None` when TVL is zero or no programme exists |

A `DeFiDueDiligenceError` is a **reviewer error**, never a protocol finding: an
impossible multisig, a negative or non-finite figure, an empty audit list, an
unparseable date, or an audit dated after the assessment. Fix the profile and re-run;
do not record it as a rejection.

## 8. Re-run on cadence, and monitor in between

This is a point-in-time gate. Between runs:

- Alert on **timelock queue** activity for the protocol. The delay only helps if a
  queued upgrade reaches a human with time to withdraw.
- Alert on **implementation address changes** behind the proxy. Any upgrade resets
  both `mainnet_days_active` and every `scope_covers_deployed_code` attestation to
  unverified until re-checked.
- Alert on **multisig owner/threshold changes** and on **bounty programme
  delisting**.

Re-run the full gate on any of those, and on a fixed calendar cadence regardless.

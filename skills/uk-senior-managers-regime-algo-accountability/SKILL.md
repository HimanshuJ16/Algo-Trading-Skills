---
name: uk-senior-managers-regime-algo-accountability
description: "Institutional regulatory governance skill for the UK FCA Senior Managers & Certification Regime (SM&CR), mapping algorithmic trading strategies to Senior Management Functions under SUP 10C, tracking SYSC 27 certification F&P certificate validity, recording version-bound reasonable-steps deployment sign-offs, and producing SYSC 25 management responsibilities map evidence."
domain: Global Regulatory Compliance & Risk Governance
subdomain: Senior Managers Regime & Executive Accountability (UK FCA)
tags:
- smcr
- uk-fca
- sup-10c
- sysc-25
- sysc-27
- mar-7a
- reasonable-steps
- fitness-and-propriety
- algo-governance
brokers_frameworks:
- fca-handbook-sup10c
- fca-handbook-sysc
- fca-handbook-mar7a
- uk-assimilated-rts6
version: "2.0.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when establishing, managing, or auditing statutory accountability for algorithmic trading systems operated by a UK **SMCR firm** under the FCA **Senior Managers and Certification Regime**.

It provides mechanisms to:
- Map each algorithmic trading strategy to a named Senior Management Function holder, with the firm's SM&CR tier enforced (SMF24 Chief Operations and SMF4 Chief Risk exist only for enhanced scope and dual-regulated firms — see [SUP 10C](https://handbook.fca.org.uk/handbook/sup10c)).
- Track **certification function** F&P certificate validity for staff performing the FCA algorithmic trading certification function ([SYSC 27.8.23R](https://www.handbook.fca.org.uk/handbook/SYSC/27/8.html)), including automatic expiry at the statutory 12-month maximum.
- Record **version-bound** pre-production deployment sign-offs capturing the reasonable steps a Senior Manager took, so an amended algorithm cannot inherit its predecessor's approval.
- Produce governance reports supporting the **management responsibilities map** required by [SYSC 25.1.1R](https://www.handbook.fca.org.uk/handbook/SYSC/25/?view=chapter) for banking, Solvency II insurance, and enhanced scope SMCR firms.

### When NOT to Use

- **Non-UK firms.** SM&CR is a UK regime. EU firms fall under MiFID II RTS 6 and national conduct regimes; US firms under FINRA/SEC supervisory frameworks. Do not port these SMF codes to another jurisdiction.
- **As the algorithmic risk control itself.** This skill records *governance evidence*. The pre-trade controls, kill functionality, and surveillance it references are implemented in `uk-fca-algorithmic-trading-systems-controls` and `kill-switch-and-drawdown-circuit-breakers`.
- **As a compliance determination.** A green `verify_algo_deployment_readiness()` means the recorded evidence is complete. It does not establish that the Senior Manager discharged the statutory Duty of Responsibility — that is assessed against [DEPP 6.2.9-A to 6.2.9-E](https://handbook.fca.org.uk/handbook/depp6) and [FCA PS17/9](https://www.fca.org.uk/publication/policy/ps17-09.pdf).

## Prerequisites

- Python 3.9+ (standard library only).
- The firm's SM&CR classification (limited scope, core, enhanced, banking, or Solvency II insurance) — this determines which SMFs the firm may appoint and whether SYSC 25 applies.
- FCA Individual Reference Numbers for all Senior Managers, verified against the [FCA Register](https://register.fca.org.uk/). The FCA publishes no fixed IRN format, so the engine checks presence only; format validation would be a false assurance.
- Awareness of [MAR 7A](https://handbook.fca.org.uk/handbook/MAR/7A/) and UK-assimilated [RTS 6](https://www.legislation.gov.uk/eur/2017/589) obligations, which supply the underlying control evidence this skill records.

## Workflow

1. **Classify the firm.** Construct `SMCRAlgoAccountabilityEngine(firm_tier=...)`. Get this wrong and the register will accept SMFs the firm cannot legally appoint: a core firm has no SMF24 or SMF4, so its algorithmic trading responsibility must sit with an SMF the firm actually holds (typically SMF1 or SMF16). `register_senior_manager()` raises `SMCRError` on a tier mismatch rather than recording an unappointable holder.
2. **Register SMF holders.** Provide `smf_id`, `role`, verified `fca_irn`, and contact details. Re-registering an existing `smf_id` logs a warning — a genuine handover needs a handover certificate under SYSC 25.9, not a silent overwrite.
3. **Record certification function staff.** Register a `CertifiedDeveloper` for each person who *approves* deployment or amendment of an algorithm, or who has significant responsibility for monitoring or deciding its ongoing compliance. Writing or testing algorithm code does not by itself trigger SYSC 27.8.23R — over-certifying is a common scoping error. Set `certificate_validity_months` below 12 if the firm issues shorter certificates.
4. **Register the algorithm at its exact deployed version.** `register_algo_strategy()` records the responsible SMF, certification staff, and the RTS 6 evidence flags: pre-trade controls (Article 15), kill functionality testing (Article 12), and stress testing (Article 10).
5. **Execute the sign-off against that version.** The responsible Senior Manager calls `execute_deployment_sign_off()` with substantive reasonable-steps notes. A sign-off naming a version other than the registered one is rejected — register the version being deployed first. A `REJECTED` or `PENDING_SIGN_OFF` decision is recorded and logged as such; it never reads as approval.
6. **Verify and report.** Call `verify_algo_deployment_readiness(algo_id, as_of=...)` before release and `generate_mrm_report(as_of=...)` for the periodic audit. Pass an explicit `as_of` date wherever the result is compared, replayed, or archived, so certificate-expiry checks stay deterministic.

## Common Pitfalls

- **Assuming SMF24 exists at your firm.** SMF24 (Chief Operations) and SMF4 (Chief Risk) are enhanced-scope and dual-regulated functions under SUP 10C. A core firm that documents "algo trading owned by SMF24" has allocated responsibility to a function it does not hold, leaving the responsibility genuinely unallocated.
- **Letting an approval survive an amendment.** SYSC 27.8.23R(a)(ii) treats an amendment to a trading algorithm as a separately approvable act. Keying sign-offs by algorithm ID alone means v1.3.0 silently deploys under the v1.2.0 approval. This engine keys by `(algo_id, version)`; a re-registration at a new version invalidates the prior sign-off and logs a warning.
- **Treating F&P certification as permanent.** A certificate is valid for a maximum of 12 months from issue (FSMA s.63F, [SYSC 27.2](https://handbook.fca.org.uk/handbook/sysc27/sysc27s2)). Without an expiry check, a lapsed approver keeps clearing deployments indefinitely. `verify_algo_deployment_readiness()` fails on an expired certificate.
- **Content-free reasonable-steps notes.** "Approved" or "Signed off" is not an audit record. Notes must state which pre-trade controls were reviewed, which stress scenarios were run and their outcomes, and when the kill functionality drill took place. The engine rejects boilerplate, but a length check cannot establish sufficiency — that is a judgement under DEPP 6.2.9-E.
- **Inventing numeric control thresholds.** RTS 6 Article 12 requires a firm to be able to cancel unexecuted orders "immediately"; it sets no millisecond target. Documenting a fabricated benchmark as a regulatory requirement is worse than documenting none — set an internal target if the firm wants one, and label it as internal.
- **Confusing evidence with control.** A complete MRM proves the paperwork exists. The FCA's [August 2025 multi-firm review](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-controls-high-level-observations) found firms with formal sign-off procedures undermined by outdated policies and superficial compliance involvement.

## Verification

```bash
python -m unittest discover -s skills/uk-senior-managers-regime-algo-accountability/scripts
```

Covers tier scoping, certificate expiry including the exclusive expiry-day boundary and leap-day clamping, version-bound sign-off invalidation, rejected sign-offs, boilerplate note rejection, and MRM report field population.

## Related Skills

- `uk-fca-algorithmic-trading-systems-controls`
- `kill-switch-and-drawdown-circuit-breakers`
- `risk-control-configuration-change-approval-workflow`
- `record-retention-periods-by-jurisdiction`
- `sec-rule-15c3-5-risk-controls-us`

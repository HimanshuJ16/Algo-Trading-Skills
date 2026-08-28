---
name: singapore-mas-notice-on-cyber-hygiene-for-trading-systems
description: >-
  Compliance audit engine for the MAS Notice on Cyber Hygiene applied to trading infrastructure — the correct notice for the entity class (FSM-N22 for capital markets firms, FSM-N06 for banks), administrative account security, risk-commensurate patching with no invented 30-day SLA, written security standards, network perimeter defence, malware protection and multi-factor authentication scoped to critical systems and internet-facing customer-information systems.
domain: Compliance & Cybersecurity Governance
subdomain: MAS Cyber Hygiene Regulatory Controls
tags: ["mas-cyber-hygiene", "singapore-compliance", "fsm-n22", "mfa-scoping", "patch-management", "trading-infrastructure"]
brokers_frameworks: ["MAS Notice FSM-N22 (Cyber Hygiene, capital markets)", "MAS Notice FSM-N06 (Cyber Hygiene, banks)", "Financial Services and Markets Act 2022 (Singapore)", "MAS Technology Risk Management Guidelines", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing or hardening trading infrastructure — order routers, market data gateways, execution engines, trade databases, colocated hosts — for a financial institution regulated by the Monetary Authority of Singapore.

Start by getting the notice number right, because the most common error here is citing one that does not bind you. **There is no single "MAS Notice on Cyber Hygiene."** MAS issues the same six requirements as a separate Notice to each class of financial institution. On 10 May 2024 the earlier class notices (Notice 655 for banks, Notice CMG-N03 for capital markets entities, PSN06, TCA-N06 and others) were cancelled and reissued under the Financial Services and Markets Act 2022:

- **`FSM-N22`** — capital markets financial institutions, the successor to CMG-N03. **A trading firm holding a Capital Markets Services licence sits here.**
- **`FSM-N06`** — banks in Singapore, the successor to Notice 655.

The requirements are identical; the notice number in your audit file is not. The engine makes the caller declare its `entity_class` and stamps the applicable notice on every report.

The six requirements, all mandatory, are (paragraph numbers as originally issued):

| Para | Requirement | Scope qualifier |
|---|---|---|
| 4.1 | Every administrative account on any OS, database, application, security appliance or network device is secured against unauthorised access or use | Every system |
| 4.2 | (a) Security patches applied within a timeframe **commensurate with the risk each vulnerability poses**; (b) where **no patch is available**, controls instituted to reduce the risk | Every system |
| 4.3 | (a) A **written set of security standards** for every system; (b) every system conforms; (c) where a system cannot conform, controls instituted | Every system |
| 4.4 | Controls at the network perimeter restricting all unauthorised network traffic | Every system |
| 4.5 | One or more malware protection measures, **where such measures are available and can be implemented** | Every system, qualified |
| 4.6 | Multi-factor authentication for (a) all administrative accounts on a **critical system**; and (b) **all** accounts on any system used to access **customer information through the internet** | Scoped — not universal |

## When NOT to Use

- **As evidence that a control is in place.** The engine reads booleans the caller supplies. A clean report attests a control; it does not observe a host. Pair it with configuration scanning and the firm's own evidence collection.
- **As a source of patching deadlines.** The Notice fixes no number and neither does this skill. `PatchRemediationPolicy` is deliberately mandatory and has no default, because a default here would be a fabricated regulatory threshold.
- **For requirements outside the Notice.** Incident notification, technology risk management, outsourcing and business continuity live in the separate TRM Notices and the MAS TRM Guidelines. The Cyber Hygiene Notice is a six-requirement baseline, not the whole MAS technology framework.
- **For per-order trading controls.** Approved Trader registration, pre-execution value limits, the Forced Order Range and the SGX-ST circuit breaker are per-order gates — see `mas-singapore-algo-trading-guidelines`.
- **Outside Singapore.** These are MAS notices. The 4.6(a) "critical system" scoping and the 4.5 availability carve-out are Singapore drafting; do not carry them into a UK FCA or EU DORA assessment.

## Prerequisites

- Which **notice binds the entity**: `entity_class` of `"CAPITAL_MARKETS"` (→ FSM-N22) or `"BANK"` (→ FSM-N06).
- The firm's own **risk-commensurate patching deadlines** (`PatchRemediationPolicy: max_days_by_severity`). MAS publishes none — this is the firm's articulation of para 4.2(a).
- Per asset, a `TradingSystemAsset` carrying: `system_id`, `system_name`, `asset_type`; the two **scope determinants** `is_critical_system` and `accesses_customer_information_over_internet`; and the control attestations `administrative_accounts_secured`, `open_vulnerabilities`, `has_written_security_standards`, `conforms_to_security_standards`, `nonconformity_controls_in_place`, `network_perimeter_controls_implemented`, `malware_protection_implemented`, `malware_protection_unavailable_justification`, `mfa_on_administrative_accounts`, `mfa_on_customer_information_accounts`.
- Per open vulnerability, an `OpenVulnerability`: `vulnerability_id`, `severity`, `days_since_patch_released` (`None` means **no patch is available**, which moves it from 4.2(a) to 4.2(b)), `compensating_controls_in_place`.
- A **criticality determination** for each asset: a critical system is one whose failure will cause significant disruption to the entity's operations or materially impact its service to customers.

## Workflow

1. **Reject structurally invalid input before auditing anything.** Blank identifiers, an unknown severity label, a negative `days_since_patch_released` (it would compare below every deadline and pass silently), a boolean age (`True` is an `int` in Python and reads as 1 day), a mutable list of vulnerabilities, or duplicate vulnerability IDs all raise. These are caller bugs; reporting them as a clean audit would be worse than failing.
2. **Fix the notice.** Resolve `entity_class` to its notice number and stamp it on the report. Auditing a CMS licensee against "FSM-N06" produces a compliance file that cites the wrong instrument.
3. **Administrative accounts (4.1).** Not attested as secured $\implies$ breach. Grant on a need-to-use basis; the requirement covers the OS, database, application, security appliance and network device layers, not just the OS.
4. **Security patches (4.2) — split the two limbs, they are not interchangeable.**
   - A patch **exists** (`days_since_patch_released` is an int): compare against the firm's deadline for that severity. The deadline is **inclusive** — "within 7 days" is met at exactly 7. Over it $\implies$ breach of 4.2(a). Compensating controls **do not** excuse this; 4.2(b) is the answer to "no patch exists", not to "we did not apply one".
   - **No patch is available** (`None`): controls must be instituted to reduce the risk $\implies$ else breach of 4.2(b). With controls recorded, the asset is compliant and the exception is carried as a warning to be re-tested when a patch ships.
   - A severity the policy does not cover **raises** rather than passing an unmeasured vulnerability.
5. **Security standards (4.3) — three limbs.** No written standards $\implies$ breach of 4.3(a), and stop: conformance under 4.3(b) is unevaluable when there is nothing to conform to. Standards exist but the asset does not conform: with controls instituted $\implies$ compliant with a 4.3(c) warning; without $\implies$ breach of 4.3(b).
6. **Network perimeter defence (4.4).** No perimeter controls $\implies$ breach. The requirement follows the traffic, including traffic reaching the asset through third-party or overseas-hosted networks.
7. **Malware protection (4.5) — honour the carve-out.** Not implemented, with a recorded justification that measures are unavailable or cannot be implemented (a sealed vendor appliance, an FPGA feed handler) $\implies$ compliant with a warning. Not implemented with no justification, or a blank one $\implies$ breach.
8. **Multi-factor authentication (4.6) — scope it, do not universalise it.**
   - Limb (a) applies **only to critical systems**, and reaches administrative accounts.
   - Limb (b) applies to any system used to access customer information through the internet, and reaches **all** accounts on it, not just administrative ones. A non-critical system can be squarely in scope through this limb alone.
   - Neither limb applies $\implies$ the requirement is reported as **not applicable**, not as passed.
   - Unknown scope (`None`) resolves **conservatively to in scope**, with a warning, so an absent field can never make a breaching asset look compliant.
9. **Report.** Every requirement is evaluated; nothing short-circuits. Output a `MASCyberHygieneAuditReport` carrying the full `breaches` tuple (each pinned to its Notice paragraph), `warnings`, deduplicated `mandatory_remediations`, and `remediation_progress_pct` measured over the **applicable** requirements only. `is_compliant` is the only figure with regulatory meaning.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Shipping a "30-day MAS patching SLA".** No such requirement exists. Para 4.2(a) requires a timeframe *commensurate with the risks posed by each vulnerability*, and the MAS TRM Guidelines say the same in guidance form. A flat 30 days is wrong in both directions: far too slow for an actively exploited RCE on an order gateway, and an invented obligation for a low-severity issue on an isolated host. Worse, presenting it as a MAS figure means nobody ever calibrates it.
- **Treating compensating controls as a patching amnesty.** Para 4.2(b) applies *where no security patch is available*. An available patch left unapplied past the firm's deadline is a breach of 4.2(a) regardless of what controls sit around it.
- **Applying MFA universally.** Limb 4.6(a) is scoped to *critical systems*. A gate that demands MFA on every administrative account everywhere raises findings the Notice never raised, and firms learn to dismiss them.
- **Ignoring MFA limb 4.6(b).** A non-critical, internet-facing system that accesses customer information needs MFA on **all** its accounts. Auditing admin MFA alone passes exactly the asset most likely to be phished.
- **Citing FSM-N06 at a capital markets firm.** FSM-N06 is the banks' notice. A CMS licensee is under FSM-N22. Both replaced their predecessors on 10 May 2024, so citing Notice 655 or CMG-N03 cites a cancelled instrument.
- **Presenting CIS benchmarks as the MAS requirement.** Para 4.3 requires that a *written set of security standards* exist and be conformed to. CIS Benchmarks are a good basis for authoring one; they are not what the Notice mandates.
- **Failing an asset that legitimately cannot run malware protection.** Para 4.5 is qualified "where such measures are available and can be implemented". A sealed appliance with a recorded justification is compliant. Silently *passing* it without recording the justification, however, loses the audit trail for the exception.
- **Reading the percentage as a compliance grade.** Every requirement is mandatory: one breach means non-compliant. An asset at "83%" is not "mostly compliant". `remediation_progress_pct` is an internal tracking figure over the applicable requirements, and it divides by the applicable count — not a flat 6 — so an asset the MFA requirement never reached is not marked down for it.
- **Defaulting scope flags to permissive values.** Every control flag on `TradingSystemAsset` defaults to the non-compliant value, and unknown criticality resolves to critical. A partially onboarded asset must fail closed, not pass by omission.
- **Stopping at the first breach.** An asset can breach 4.1, both limbs of 4.2 and both limbs of 4.6 at once. Remediation needs the full list.

## Verification

- Instantiate `SingaporeMASCyberHygieneEngine(PatchRemediationPolicy(max_days_by_severity={"CRITICAL": 7, "HIGH": 14, "MEDIUM": 60, "LOW": 180}))`. Audit a fully compliant critical order router $\implies$ `is_compliant is True`, `breaches == ()`, `remediation_progress_pct == 100.0`, `entity_notice == "FSM-N22"`. The same engine with `entity_class="BANK"` $\implies$ `entity_notice == "FSM-N06"`.
- Confirm no 30-day rule survives: a `CRITICAL` vulnerability open **20 days** must breach `4.2(a)` against the 7-day firm policy, while a `LOW` vulnerability open **90 days** must pass against the 180-day policy. Both outcomes invert under a flat 30-day SLA.
- Confirm the deadline is inclusive: `days_since_patch_released=7` against a 7-day policy passes; `8` breaches.
- Confirm the patch limbs are distinct: `days_since_patch_released=None` with `compensating_controls_in_place=True` $\implies$ compliant with a `4.2(b)` warning; the same with no controls $\implies$ `4.2(b)` breach; `days_since_patch_released=45` **with** compensating controls $\implies$ still a `4.2(a)` breach.
- Confirm MFA scoping: a non-critical asset with no internet customer-information access and no MFA $\implies$ compliant, with `MULTI_FACTOR_AUTH` in `not_applicable_requirements`; a **non-critical** asset that accesses customer information over the internet without `mfa_on_customer_information_accounts` $\implies$ `4.6(b)` breach; `is_critical_system=None` $\implies$ audited conservatively as in scope.
- Confirm the 4.3 and 4.5 carve-outs: non-conformance with `nonconformity_controls_in_place=True` $\implies$ compliant with a `4.3(c)` warning; absent malware protection with a non-blank justification $\implies$ compliant with a `4.5` warning, but a blank justification $\implies$ `4.5` breach.
- Confirm the gate fails closed: blank identifiers, an unknown severity, a negative or boolean `days_since_patch_released`, a list (rather than tuple) of vulnerabilities, duplicate vulnerability IDs, an unknown `entity_class`, and a missing `patch_policy` must each raise. An asset built from identifiers alone must audit non-compliant.
- Confirm the audit trail is complete: an asset failing everything must carry all six requirements in `failed_requirements` and seven breached paragraphs, with `remediation_progress_pct == 0.0`.
- Run the test suite:
```bash
cd skills/singapore-mas-notice-on-cyber-hygiene-for-trading-systems/scripts
python -m unittest test_singapore_mas_notice_on_cyber_hygiene_for_trading_systems
```

## Related Skills

- `mas-singapore-algo-trading-guidelines`
- `phishing-resistant-authentication-for-custody-access`
- `network-segmentation-for-trading-infrastructure`
- `dependency-vulnerability-scanning-in-ci`
- `api-key-least-privilege-audit-tool`
- `sandbox-credential-leakage-prevention`

---
name: regulatory-sandbox-programs-for-fintech-testing
description: >-
  Use when testing a strategy or product with real clients under a regulator-approved
  sandbox authorisation from the FCA, MAS or SEBI, auditing live telemetry against the
  client caps, cumulative volume and duration boundaries the approval set.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: regulatory-sandbox, fintech-testing, fca-sandbox, mas-sandbox, sebi-sandbox, boundary-conditions, compliance-boundaries
  brokers_frameworks: "FCA Regulatory Sandbox; MAS FinTech Regulatory Sandbox; SEBI Regulatory Sandbox; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when testing new algorithmic trading strategies, innovative financial products, or DLT-based market infrastructure with **real clients** under a regulator-approved sandbox authorisation (e.g. FCA UK, MAS Singapore, SEBI India). Sandbox programs grant temporary, conditional regulatory relief; that relief is scoped by the boundary conditions in your approval — client count, cumulative transaction value, assets held, testing duration, and a documented exit plan. Trading outside those conditions means trading without the relief you were relying on. This engine audits live testing telemetry against the limits you were actually granted and warns before a cap is reached.

**Critical**: no regulator publishes universal numeric caps. The FCA "will set a strict limit to the size of the test" and requires the testing plan to state its own "duration, customer/transaction limit"; MAS agrees boundary conditions per experiment; SEBI approves the user set per testing stage. Accordingly this engine ships **no default limits** — you must register the values transcribed from your own approval letter, and an unregistered program fails closed with `PROGRAM_NOT_FOUND`. Never hard-code a cap you cannot point to in an approval document.

## When NOT to Use

- **SEBI Innovation Sandbox**: out of scope. It is offline testing "in isolation from the live market" on historical, anonymised datasets, and "live data shall not be made available to participants" (SEBI/MRD/CSC/CIR/P/2019/64). There are no live clients, no live AUM and no live traded volume to audit. The live-client Indian program is the **SEBI Regulatory Sandbox**.
- **Firm-wide risk limits or production trading**: sandbox boundary conditions are a licensing constraint, not a risk control. Keep your kill switch and exposure limits separate and independent (`kill-switch-and-drawdown-circuit-breakers`).
- **Deciding whether a breach must be reported**: this engine detects boundary excursions; escalation and regulator notification are a compliance decision, not an automated one.

## Prerequisites

- The approved boundary conditions for your test, transcribed from the regulator's approval letter into `SandboxParameters` (`program_name`, `jurisdiction`, `max_allowed_clients`, `max_transaction_volume_usd`, `max_aum_usd`, `max_duration_months`, optional `approved_extension_months`, `requires_exit_strategy`, `framework_key`).
- Live testing telemetry (`active_clients`, `cumulative_volume_usd`, `current_aum_usd`, `elapsed_months`, `has_exit_plan`). Volume must be **cumulative since test start**, not the current open position.
- `SANDBOX_FRAMEWORKS` provides source-backed regulator metadata (citation, whether live customers are permitted) for documentation and provenance — it deliberately carries no numbers.

## Workflow

1. **Register Approved Boundary Conditions**:
   - Transcribe each cap from the approval letter into `SandboxParameters`; set `framework_key` to record which regulator's framework it came from. Non-positive caps are rejected at construction — a zero cap is a transcription error, not an unlimited allowance.
   - Only populate `approved_extension_months` once the extension is granted **in writing**. Assuming an extension is the most common way a firm ends up testing without relief.
2. **Telemetry Ingestion & Capacity Calculation**:
   - Compute capacity utilisation for clients, cumulative volume, and AUM against the registered caps.
3. **Boundary Breach Checks** — caps are inclusive maxima, so exactly at the cap is compliant and any value strictly above it is a breach:
   - `active_clients` > cap → `CLIENT_LIMIT_BREACH`.
   - `cumulative_volume_usd` > cap → `VOLUME_CAP_BREACH`.
   - `current_aum_usd` > cap → `AUM_CAP_BREACH`.
   - `elapsed_months` > `max_duration_months + approved_extension_months` → `SANDBOX_EXPIRED`.
4. **Exit Plan Audit**:
   - If the approval requires a documented exit / client-transition plan and none is recorded → `MISSING_EXIT_PLAN`. If the approval genuinely does not require one, set `requires_exit_strategy=False` rather than falsifying `has_exit_plan`.
5. **Pre-Breach Warning**: utilisation at or above `warning_threshold_pct` (default 80%), or one month or less of approved testing remaining, emits a `warnings` entry while status stays compliant. A breach has already voided the relief, so the warning is the actionable signal — route it to compliance, not to a dashboard nobody reads.
6. **Audit Report Generation**: output structured `SandboxAuditReport` with `status`, `breaches`, `warnings`, and per-dimension capacity percentages.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing against invented caps**: hard-coding a plausible-looking limit ("500 clients", "12 months") that appears in no approval document. The regulator enforces your boundary conditions, not an industry average — and a wrong cap either raises false breaches or silently permits a real one. Register only transcribed values; leave the program unregistered and fail closed if you do not have them.
- **Treating the SEBI Innovation Sandbox as a live-client program**: it is an offline environment on anonymised historical data. Client, AUM and volume caps do not apply to it, and mapping live telemetry onto it produces meaningless compliance output.
- **Measuring volume as open position**: sandbox transaction thresholds are typically cumulative over the test. Tracking only current exposure lets cumulative volume pass the cap unnoticed.
- **Unmonitored client onboarding**: a growth spike takes `active_clients` past the approved number between compliance reviews. Gate onboarding on the engine's warning, not on a periodic report.
- **Assuming an extension**: continuing to test past the approved end date while an extension request is pending. Until it is granted in writing, `approved_extension_months` is 0 and the test has expired.
- **Confusing a breach alert with a remediation**: exceeding a boundary condition voids the relief for the activity concerned. Detection is the start of the exit/notification path, not the end of it.

## Verification

- Instantiate `RegulatorySandboxProgramsForFintechTestingEngine()` with no programs $\implies$ verify `programs == {}` and that any telemetry returns `PROGRAM_NOT_FOUND` with `is_within_limits=False` (fail-closed, no fabricated defaults).
- Register `SandboxParameters` from an approval (e.g. 500 clients / \$5M volume / \$10M AUM / 6 months). Feed telemetry within limits $\implies$ verify `SANDBOX_COMPLIANT`. Feed exactly 500 clients and \$5,000,000.00 volume $\implies$ verify still `SANDBOX_COMPLIANT` (inclusive maxima). Feed 501 clients and \$5,000,000.01 $\implies$ verify `SANDBOX_BREACHED` with both `CLIENT_LIMIT_BREACH` and `VOLUME_CAP_BREACH`.
- Feed `elapsed_months=8` against a 6-month approval $\implies$ verify `SANDBOX_EXPIRED`; add `approved_extension_months=3` $\implies$ verify compliant with `time_remaining_months=1`.
- Feed 450 clients against a 500 cap $\implies$ verify `SANDBOX_COMPLIANT` with a 90% client-utilisation warning.
- Construct `SandboxParameters` with `max_allowed_clients=0` $\implies$ verify `ValueError` (no divide-by-zero in the capacity calculation).
- Run `python -m unittest discover -s skills/regulatory-sandbox-programs-for-fintech-testing/scripts`.

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `regulatory-capital-requirement-tracking`
- `cross-jurisdiction-regulatory-conflict-resolution`

---
name: finra-algo-trading-registration-requirements
description: >-
  Use when a FINRA member deploys or materially changes an automated trading system, to
  decide whether Rule 1220(b)(4) Securities Trader registration is triggered for the
  person primarily responsible for its design or modification.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: finra-rule-1220b4, series-57, securities-trader, algorithmic-trading-registration, regulatory-notice-16-21, cicd-governance, compliance-audit
  brokers_frameworks: "FINRA Rule 1220(b)(4); FINRA Regulatory Notice 16-21; FINRA Rule 1240 (Continuing Education); FINRA Rule 3110; FINRA Rule 4511; Series 57 / SIE; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in broker-dealer compliance platforms, algo-developer onboarding workflows, and CI/CD release gates for trading code.

**FINRA Rule 1220(b)(4)(A)(iii)** requires each associated person of a FINRA member who is *primarily responsible* for the **design, development or significant modification** of an **algorithmic trading strategy relating to equity, preferred or convertible debt securities** — or who is responsible for the **day-to-day supervision or direction** of such activities — to register as a **Securities Trader**. Persons registering on or after 1 October 2018 must pass the **SIE** and the **Series 57** examination (Rule 1220(b)(4)(B)). The obligation took effect **30 January 2017** under the predecessor NASD Rule 1032(f) (Regulatory Notice 16-21) and was carried into Rule 1220 on 1 October 2018.

The value of this skill is *scope discipline in both directions*. Registration is narrow — four security types, systems that actually generate or route orders, persons *primarily* responsible — and both over- and under-application are compliance failures.

## When NOT to Use

- **Outside a FINRA member.** Rule 1220 binds *associated persons of members*. A non-member proprietary trading firm, a fund manager, or an unaffiliated vendor is outside it. Construct the engine with `is_finra_member=False` and every change classifies out of scope.
- **For non-covered instruments.** The prong reaches equity (including options on equity securities), preferred and convertible debt securities only. A futures, FX, crypto, municipal or straight corporate-debt algorithm is not covered by this prong; a different regime may apply (CFTC/NFA for futures, MSRB for municipals), and firm-level licensing is a separate question — see `algorithmic-trading-firm-licensing-thresholds`. Asserting a Series 57 requirement over a futures algo is regulatory misinformation.
- **As the firm's whole supervisory system.** This gate answers "is this person registered?" It does not discharge Rule 3110 supervision, the Notice 15-09 change-management expectations, or the pre-trade risk controls of SEC Rule 15c3-5 — see `sec-rule-15c3-5-risk-controls-us`.
- **As a source of registration truth.** `DeveloperCredentials` is a *snapshot* of CRD / FINRA Gateway state. This module never queries CRD and cannot tell you the snapshot is stale. A registration that lapsed for two or more years requires requalification (Rule 1210.08) and is not "active".
- **As durable storage.** `audit_trail` is an in-memory reference adapter. Rule 4511(b) sets a six-year default retention where no other period applies, and Rule 4511(c) requires a format and media complying with SEA Rule 17a-4.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations`; dependency-free stdlib).
- A personnel registry sourced from CRD / FINRA Gateway: `personnel_id`, `name`, `role_title`, `is_series_57_active`, `is_sie_active`, plus `is_ce_inactive` (Rule 1240), `is_sie_grandfathered` (pre-1 Oct 2018 registrants), and `is_general_securities_principal` (Series 24).
- A change classification per commit: `security_type`, `system_behavior`, `author_activity`, and whether the author is *primarily* responsible.
- A firm-documented definition of "significant modification". FINRA's guidance is that it is "any change to the code of the algorithm that impacts the logic and functioning of the trading strategy" — a data-feed/vendor change generally is not; a change to a benchmark index generally is. Firms must map that to their own repository.
- The Rule 3110(a)(5) supervisory assignment for each registered developer, decided before this gate runs.

## Workflow

1. **Confirm the firm is a FINRA member.** If not, stop — Rule 1220 does not reach the change at all.
2. **Classify the security type.** Covered: `EQUITY`, `EQUITY_OPTION`, `PREFERRED`, `CONVERTIBLE_DEBT`.
   - **Decision point — an unmapped instrument raises, it does not exit scope.** `assess_scope()` raises `ValueError` on a token it does not recognise. Silently classifying an unmapped instrument as out of scope is how a covered equity algo escapes the gate; a loud CI failure is the safer default.
3. **Classify what the system does.** Only a system that *generates or routes* orders (or order-related messages, including cancellations) is an "algorithmic trading strategy".
   - **Decision point — a pure pass-through router is not covered.** A standard router that sends orders in their entirety to a market center is excluded. Add price/size discretion, parent/child slicing, or displayed-vs-non-displayed decisions and it becomes covered.
   - **Decision point — an idea-generation engine is not covered until it can send orders.** A model producing signals or allocations that cannot emit orders is out of scope; wire it to an order gateway and it is in scope.
4. **Classify what the person did, and whether they were primarily responsible.**
   - Registrable: design, development, significant modification, directing a third party, day-to-day supervision, and monitoring/reviewing the algorithm's performance — the last applies even to an unmodified off-the-shelf algorithm.
   - Not registrable: minor modification, integrating the algorithm into the firm's infrastructure, testing linkages.
   - **Decision point — "primarily responsible" excludes the team, not the lead.** A junior developer working under a lead is not covered; the lead who directs the development is, even if they never write a line.
5. **Evaluate registration for the author and the approving supervisor.** A person qualifies only with an active Series 57, a satisfied SIE requirement, and no CE-inactive status.
   - **Decision point — CE-inactive beats an active Series 57.** Under Rule 1240(a)(3) a person who misses the annual Regulatory Element must "cease all activities as a registered person". Registered-but-CE-inactive is a block, not a pass.
   - **Decision point — a missing SIE record is not automatically a defect.** A Securities Trader registered before 1 October 2018 who maintained that registration is considered to have passed the SIE and has no exam record. Set `is_sie_grandfathered` or the gate will produce false blocks against your most senior traders.
   - A supervisor qualifies as either a Securities Trader or a Securities Trader Principal (Series 57 + Series 24, Rule 1220(a)(7)); the report records which.
6. **Gate the deployment on `report.blocks_deployment`.**
   - **Decision point — out of scope is not approved.** An out-of-scope change returns `OUT_OF_SCOPE_RULE_1220B4`, never `COMPLIANCE_APPROVED`. Rule 3110 supervision and Notice 15-09 change management still apply, and `requires_change_management_review` stays true for any algorithmic strategy change (a Notice 15-09 expectation for covered securities; firm policy for anything outside FINRA's reach).
7. **Retain the report** as a book and record (Rule 4511(b)/(c)).

> Full procedure: see `references/workflows.md`.
> Standards and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Gating on "significant modification" alone.** The rule is disjunctive: **design, development, *or* significant modification**. A brand-new algorithm is not a modification of anything, so a gate keyed on a `is_significant_modification` flag waves through the single highest-risk case — an unregistered engineer building a new equity algo from scratch.
- **Applying Series 57 to every asset class.** Rule 1220(b)(4)(A)(iii) is limited to equity, preferred and convertible debt securities. Blocking a futures or crypto deployment "for FINRA reasons" is a fabricated requirement, and it trains the desk to route around the gate.
- **Treating quantitative software engineers as exempt IT staff.** The rule targets the person primarily responsible for the algorithm's design or development regardless of job title. Notice 16-21's own example: a lead developer who supervises the team building a head trader's strategy must register.
- **Treating every contributor as covered.** The mirror-image error. FINRA states it does not intend the requirement to reach "every associated person who touches or otherwise is involved in the design or development of a trading algorithm", and infrastructure integration and linkage testing are expressly not Securities Trader activities.
- **Reading "off-the-shelf" as "out of scope".** Even where a third party built the algorithm and the firm does not modify it, the associated person responsible for monitoring or reviewing its performance must be a Securities Trader — and any in-house significant modification, or direction of the vendor to make one, must be by a Securities Trader.
- **Trusting `is_series_57_active` without CE status.** A CE-inactive registration is prohibited from functioning in a capacity requiring registration; after two consecutive years of CE inactivity FINRA administratively terminates it.
- **Blocking pre-2018 registrants for a missing SIE.** They are deemed to have passed it. This is the most common false positive when the gate is fed raw CRD exam records.
- **Mistaking a code approver for the Rule 3110(a)(5) supervisor.** The person who clicks approve on a pull request is not necessarily the person assigned to supervise the developer's Securities Trader activities. Keep the assignment explicit; set `require_supervisor_registration=False` only when that assignment is genuinely tracked elsewhere.
- **Recording the decision only for violations.** Out-of-scope and approved decisions are exactly what a FINRA examiner asks for when reconstructing who was responsible for an algorithm on a given date.
- **Leaving the trail in memory.** `audit_trail` does not survive a restart. Persist every report to an append-only sink meeting Rule 4511(c) / SEA Rule 17a-4.

## Verification

- Instantiate `FinraAlgoRegistrationEngine(clock=frozen_clock)` and register a Securities Trader (`DEV_A`), a Securities Trader Principal (`SUP_A`), and an unregistered engineer (`DEV_B`).
- Significant modification to an equity VWAP router by `DEV_A`, approved by `SUP_A`: expect `COMPLIANCE_APPROVED`, `blocks_deployment` false, `supervisor_registration_basis == "SECURITIES_TRADER_PRINCIPAL"`.
- Same change authored by `DEV_B`: expect `REGISTRATION_VIOLATION_BLOCKED` with `AUTHOR_NO_ACTIVE_SERIES_57`.
- `author_activity=ACTIVITY_DESIGN` with `is_significant_modification=False` by `DEV_B`: expect a block — the initial design of a new algorithm is in scope.
- Same change with `security_type=SECURITY_FUTURE`: expect `OUT_OF_SCOPE_RULE_1220B4`, `scope_reason == "OUT_OF_SCOPE_SECURITY_TYPE"`, no violations, and *not* `COMPLIANCE_APPROVED`.
- `system_behavior=SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS`: expect out of scope with `requires_change_management_review` false. `ACTIVITY_MINOR_MODIFICATION`: expect out of scope with `requires_change_management_review` **true**.
- Author with `is_series_57_active=True, is_ce_inactive=True`: expect a block citing `AUTHOR_CE_INACTIVE`. Author with `is_sie_active=False, is_sie_grandfathered=True`: expect approval.
- `security_type="WEATHER_DERIVATIVE"`: expect `ValueError`, not a silent out-of-scope pass.
- Run `python -m unittest discover -s skills/finra-algo-trading-registration-requirements/scripts` (47 tests) and confirm a 100% pass rate.

## Related Skills

- `sec-rule-15c3-5-risk-controls-us`
- `algorithmic-trading-firm-licensing-thresholds`
- `algo-trading-disclosure-to-exchange-membership`
- `risk-control-configuration-change-approval-workflow`
- `strategy-research-to-production-pipeline-governance`
- `record-retention-periods-by-jurisdiction`
- `execution-algorithm-kill-switch-integration`

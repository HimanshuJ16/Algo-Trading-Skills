# Standards — kyc-aml-considerations-for-algo-trading-entities

## Jurisdiction and scope

The binding rules below are **US** (FinCEN CDD Rule, OFAC), supplemented by the
**FATF** international standards, which are not law anywhere until a jurisdiction
implements them, and by the **EU** AML framework where it differs materially.
Nothing here is legal advice, and no threshold in the engine is a substitute for
a compliance officer's determination.

> **Correction to version 1.0.0 of this skill.** The previous standards table
> stated that "any OFAC/**PEP** match MUST result in immediate onboarding
> rejection", and that entities in any FATF-blacklisted jurisdiction MUST be
> rejected. Both were wrong. PEP status triggers enhanced due diligence, not
> refusal (FATF R.12), and of the three call-for-action jurisdictions only two
> carry a call for counter-measures. Both are corrected below and in the engine.

## 1. Beneficial ownership — the FinCEN CDD Rule has two prongs

31 CFR 1010.230 requires **covered financial institutions** to identify and
verify the beneficial owners of each legal entity customer. "Beneficial owner"
is defined under two independent prongs:

| Prong | Definition | Count |
|---|---|---|
| **Ownership**, 1010.230(d)(1) | "Each individual, if any, who, directly or indirectly, through any contract, arrangement, understanding, relationship or otherwise, owns **25 percent or more** of the equity interests of a legal entity customer" | 0 to 4 |
| **Control**, 1010.230(d)(2) | "A single individual with significant responsibility to control, manage, or direct a legal entity customer, including: An executive officer or senior manager (e.g., a Chief Executive Officer, Chief Financial Officer, Chief Operating Officer, Managing Member, General Partner, President, Vice President, or Treasurer); or Any other individual who regularly performs similar functions" | exactly 1 |

So every legal entity customer yields **between one and five** beneficial
owners. The word "if any" appears in the ownership prong and does not appear in
the control prong. **An engine that implements only the ownership prong will
approve an entity having identified nobody**, which is the defect this version
of the skill fixes.

Two further points that matter specifically for trading entities:

- **"Directly or indirectly"** means a person's holdings through several vehicles
  aggregate. Two 15% interests held by one individual is a 30% beneficial owner.
- **1010.230(e)(3)**: a *pooled investment vehicle* operated or advised by a
  financial institution not excluded under (e)(2) is subject to the **control
  prong only**. That is the fund case. The engine does **not** implement this as
  an automatic relaxation — weakening a control based on a US-specific carve-out
  in a multi-jurisdiction skill is the wrong default — but it is why the control
  prong must never be treated as a fallback for when the ownership prong
  returns nobody.

**Verification** is of the beneficial owners' **identity**, in a manner
consistent with the institution's CIP. The institution "may rely on the
information supplied by the legal entity customer regarding the identity of its
beneficial owner or owners, provided that it has no knowledge of facts that
would reasonably call into question the reliability of such information"
(1010.230(b)). On **2026-02-13** FinCEN issued an order removing the regulatory
requirement to re-identify and re-verify beneficial owners at *every* new
account opening, limiting it to initial account opening, points where reliability
is called into question, and risk-based ongoing CDD.

Sources: [31 CFR 1010.230 (Cornell LII)](https://www.law.cornell.edu/cfr/text/31/1010.230),
[FinCEN CDD Rule FAQs](https://www.fincen.gov/resources/statutes-and-regulations/cdd-rule-faqs),
[FFIEC BSA/AML Manual, Appendix 1 — Beneficial Ownership](https://bsaaml.ffiec.gov/manual/Appendices/01),
[Mayer Brown — FinCEN Grants Risk-Based Relief from Repeat Beneficial Ownership Verification Requirements (Feb 2026)](https://www.mayerbrown.com/en/insights/publications/2026/02/more-is-not-always-better-fincen-grants-risk-based-relief-from-repeat-beneficial-ownership-verification-requirements).

## 2. Who the rule binds — and why a trading fund is usually the customer

"Covered financial institution" is defined by cross-reference and covers banks,
registered broker-dealers, mutual funds, futures commission merchants and
introducing brokers in commodities. **An algorithmic trading fund is normally the
legal entity customer of such an institution, not the institution.**

The 2024 rule that would have amended the BSA definition of "financial
institution" to include certain **registered investment advisers and exempt
reporting advisers** — imposing AML/CFT programs and SAR filing on them — has
**not taken effect**. FinCEN issued a final rule dated **2025-12-31** (published
**2026-01-02**) moving the effective date from **2026-01-01 to 2028-01-01**,
following an exemptive relief order of 2025-08-05. Do not build a control on the
assumption that your adviser entity is currently a BSA financial institution;
equally, do not assume the delay is permanent.

Sources: [FinCEN — Final Rule to Postpone Effective Date of Investment Adviser Rule to 2028](https://www.fincen.gov/news/news-releases/fincen-issues-final-rule-postpone-effective-date-investment-adviser-rule-2028),
[Federal Register 91 FR / 2025-24184 (2026-01-02)](https://www.federalregister.gov/documents/2026/01/02/2025-24184/delaying-the-effective-date-of-the-anti-money-launderingcountering-the-financing-of-terrorism),
[Morrison Foerster — FinCEN Hits Pause: No AML Rule for Investment Advisers Until 2028](https://www.mofo.com/resources/insights/260108-fincen-hits-pause-no-aml-rule-for-investment-advisers-until-2028).

## 3. The 25% figure is a jurisdictional choice, not a FATF mandate

| Framework | Test | Status |
|---|---|---|
| FATF INR.10 | "a threshold, for example, any person owning more than a certain percentage of the company (such as 25%)" | An **example**, not a required number |
| FinCEN, 31 CFR 1010.230(d)(1) | **25 percent or more** — inclusive | In force |
| EU AMLD4/AMLD5 | **more than** 25% — exclusive | In force until the AMLR applies |
| EU Regulation 2024/1624 (AMLR) | **25% or more** — inclusive | Applies from **2027-07-10** |

The engine applies `>=` by default, matching FinCEN and the AMLR. If you must
reproduce the AMLD4/5 exclusive test exactly, supply a marginally larger
`ubo_ownership_threshold_pct`. FATF's cascade also has a third step this skill
mirrors through the control prong: where no natural person can be identified by
ownership or by other means of control, the **senior managing official** is
identified.

Sources: [FATF Recommendations (2012, updated)](https://www.fatf-gafi.org/content/dam/fatf-gafi/recommendations/FATF%20Recommendations%202012.pdf.coredownload.inline.pdf),
[FATF Guidance — Beneficial Ownership of Legal Persons (March 2023)](https://www.fatf-gafi.org/content/dam/fatf-gafi/guidance/Guidance-Beneficial-Ownership-Legal-Persons.pdf.coredownload.pdf),
[Hogan Lovells — Changes in Beneficial Ownership rules under Regulation (EU) 2024/1624](https://www.hoganlovells.com/en/publications/changes-in-beneficial-ownership-rules-under-the-new-eu-antimoney-laundering-regulation-eu-20241624).

## 4. The OFAC 50 Percent Rule — aggregation, and a different consequence

Under OFAC's **Revised Guidance of 13 August 2014**, any entity owned "in the
aggregate, directly or indirectly, **50 percent or more** by one or more blocked
persons is itself considered to be a blocked person", whether or not it appears
on the SDN List. The 2014 revision is precisely that OFAC now **aggregates**: the
earlier informal position looked at each blocked person separately. Blocked
Person X at 25% plus Blocked Person Y at 25% blocks the entity.

Two consequences the engine keeps distinct:

| Situation | Consequence |
|---|---|
| A blocked person holds a **minority** interest | A reason to decline the relationship |
| Blocked persons hold **≥ 50% in the aggregate** | The entity **is** blocked property — block and report to OFAC; declining quietly is not sufficient |

Ownership, not control, drives the rule: OFAC's guidance addresses entities
*owned* 50% or more, while cautioning about entities blocked persons may control
without majority ownership.

Sources: [OFAC — Entities Owned by Blocked Persons (50 Percent Rule) FAQs](https://ofac.treasury.gov/faqs/topic/1521),
[OFAC Revised Guidance, 13 August 2014](https://sanctions.org/turbofac/research/OFAC-Revised-Guidance-on-Entities-Owned-by-Persons-Whose-Property-and-Interests-in-Property-are-Blocked-50-percent-rule).

## 5. FATF jurisdiction lists — three tiers, not one

As of the **19 June 2026** plenary statements:

| Tier | Jurisdictions | FATF asks for |
|---|---|---|
| Call for action **with counter-measures** | Iran (IR), DPRK (KP) | Effective counter-measures under R.19 |
| Call for action, **EDD only** | Myanmar (MM) | Enhanced due diligence **proportionate to the risk**; FATF has *not* called for counter-measures, and asks that humanitarian assistance, legitimate NPO activity and remittances not be disrupted |
| Increased monitoring ("grey list"), 22 jurisdictions | AO, BO, BA, BG, CM, CI, CD, HT, IQ, KE, KW, LA, LB, MC, NP, PG, SS, SY, VE, VN, VG, YE | Risk-based EDD; the jurisdiction has committed to an action plan |

Recommendation 19 requires EDD "effective and proportionate to the risks", and
countermeasures likewise "proportionate to the risks". It does not require, and
FATF does not expect, a blanket severance of all activity with a higher-risk
jurisdiction. The June 2026 plenary added Iraq and Bosnia and Herzegovina to the
grey list and removed Algeria and Namibia — which is the point of dating the
snapshot: **these lists change at every plenary, roughly February, June and
October.** `FATF_LISTS_2026_06_19` is a starting default, and the engine raises a
`STALE_JURISDICTION_LISTS` advisory once it is older than `max_list_age_days`.

Sources: [FATF — Black and grey lists](https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html),
[FATF — High-Risk Jurisdictions subject to a Call for Action, 19 June 2026](https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/call-for-action-june-2026.html),
[FATF — Jurisdictions under Increased Monitoring, 19 June 2026](https://www.fatf-gafi.org/en/publications/High-risk-and-other-monitored-jurisdictions/increased-monitoring-june-2026.html),
[ComplyAdvantage — FATF Blacklist and Greylist (updated 29 June 2026)](https://complyadvantage.com/insights/fatf-blacklists-greylists/).

## 6. PEPs — Recommendation 12 requires EDD, not rejection

For **foreign** PEPs (and their family members and close associates), FATF
requires financial institutions to:

1. have risk-management systems to determine whether a customer or beneficial
   owner is a PEP;
2. obtain **senior management approval** before establishing — or continuing —
   the relationship;
3. take reasonable measures to establish **source of wealth and source of
   funds**; and
4. conduct **enhanced ongoing monitoring**.

**Domestic** PEPs and PEPs entrusted with a prominent function by an
international organisation are handled on a **risk basis**: measures (2)–(4)
apply where the relationship is higher risk.

FATF is explicit that these requirements are **preventive, not criminal, in
nature, and should not be interpreted as meaning that all PEPs are involved in
criminal activity**. Automatically rejecting PEPs is de-risking, not compliance.

Sources: [FATF Guidance — Politically Exposed Persons (Recommendations 12 and 22), June 2013](https://www.fatf-gafi.org/content/dam/fatf-gafi/guidance/Guidance-PEP-Rec12-22.pdf.coredownload.pdf),
[FATF Recommendations](https://www.fatf-gafi.org/content/dam/fatf-gafi/recommendations/FATF%20Recommendations%202012.pdf.coredownload.inline.pdf).

## 7. Engineering defaults (no regulatory basis)

No regulator prescribes these. They are policy inputs — calibrate them and record
the calibration.

| Parameter | Default | Meaning |
|---|---|---|
| `ubo_ownership_threshold_pct` | 25.0 | Ownership prong threshold, applied as `>=` |
| `max_unaccounted_ownership_pct` | = the threshold | Undeclared residual tolerated. Derived, not arbitrary: above one threshold's worth, an undisclosed holder could reach the threshold |
| `require_control_person` | True | Enforce 1010.230(d)(2) |
| `max_list_age_days` | 180 | Roughly one missed FATF plenary |
| `OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT` | 50.0 | Fixed by OFAC guidance, not a policy choice |

## 8. Decision precedence

`status` reports the highest-precedence blocking finding; `report.findings`
always carries every finding, so a rejection never conceals a second problem:

`REJECTED_OFAC_50_PERCENT_RULE` → `REJECTED_SANCTIONS_MATCH` →
`REJECTED_FATF_HIGH_RISK` → `REJECTED_UNVERIFIED_UBO` →
`REJECTED_NO_CONTROL_PERSON` → `REJECTED_OWNERSHIP_OPACITY` →
`KYC_AML_EDD_REQUIRED` → `KYC_AML_APPROVED`.

## Category

`regulatory-compliance` — see the top-level `mappings/` directory for how this
category rolls up across the full skill library.

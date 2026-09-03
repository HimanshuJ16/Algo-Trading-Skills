---
name: multi-jurisdiction-tax-residency-implications
description: Tax residency assessment engine for globally-distributed trading
  operations — determines which jurisdictions claim an entity, resolves dual
  corporate residence through the tie-breaker the treaty actually contains, runs
  registered physical-presence tests for decision-makers, and flags permanent
  establishment exposure from co-located infrastructure.
domain: Tax Accounting & Reporting Global
subdomain: International Tax Residency & Permanent Establishment
tags:
- tax-residency
- multi-jurisdiction
- poem
- place-of-effective-management
- permanent-establishment
- substantial-presence-test
- dual-residence
- cross-border-tax
brokers_frameworks:
- OECD Model Tax Convention Art. 4 and Art. 5
- BEPS Multilateral Instrument (MLI) Art. 4
- IRC s.7701(b)(3) Substantial Presence Test
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading operation is spread across jurisdictions and you need to establish **which country taxes the entity as a resident** before any income is priced. A Cayman-incorporated fund whose investment committee meets in Singapore, a UK company whose CIO relocated to Miami, a Singapore manager renting FPGA space in a Frankfurt co-location cage — each of these is a residence or permanent establishment question, and each has to be answered before withholding rates, credits, or filing obligations mean anything.

The engine determines which registered jurisdictions assert residence, resolves dual residence using the tie-breaker the applicable treaty actually contains, applies physical-presence tests to the individuals who run the strategy, and flags fixed places of business that need permanent establishment analysis.

## When NOT to Use

- **As a treaty or statute database.** It ships with no thresholds and no tie-breakers. Residency rules are jurisdiction-specific and inconsistent — the basic individual test is 182 days in India and 183 in the UK, and the US test is a weighted three-year formula. An unregistered jurisdiction returns `REVIEW_REQUIRED`, not a guess.
- **To compute withholding tax or Foreign Tax Credits.** Deliberately out of scope. Residence is the *input* to that arithmetic. Use `double-taxation-treaty-considerations-cross-border-trading`, which models treaty rates per income article, the noncompulsory-payment limit on creditability, and the residence-country credit ceiling.
- **As a complete residency determination for individuals.** Only the arithmetic day-count test is modelled. India's 60-day-plus-365-day rule and its 120-day rule for citizens and persons of Indian origin with Indian-sourced income above INR 1.5 million, and the UK's sufficient ties test — which can make someone resident on well under 46 days — are not. **Failing the registered test is not a clearance.**
- **Where a third jurisdiction claims residence** on a domestic basis other than incorporation or effective management. Only those two bases are modelled, which is what makes a single bilateral tie-breaker sufficient.
- **As a filing position.** Output is decision support for a tax adviser. Where a company is managed is a question of evidence that belongs in board minutes before it belongs in a dataclass.

## Prerequisites

- A `CorporateResidenceRule` per jurisdiction in play, recording whether it taxes on incorporation, on effective management, or both. Registering a jurisdiction that asserts *neither* is meaningful — it distinguishes "no corporate income tax" from "not yet researched".
- The entity's incorporation country and, where established, its `effective_management_country`.
- A `TreatyResidenceTieBreaker` per relevant treaty pair, read off **the treaty in force** — checked against any protocol and both parties' MLI positions — not off the current OECD Model.
- An `IndividualPresenceRule` per jurisdiction whose presence test you intend to apply, with weights as `Fraction`, not `float`.
- Day counts per decision-maker, per country, per tax year.

## Workflow

1. **Collect residence claims.** For the incorporation country and the effective management country, check the registered rule for whether that jurisdiction actually asserts residence on that basis.
   - **Decision point:** a jurisdiction in play with no registered rule produces a required action, never a silent claim and never a silent absence of one. A Cayman fund managed from Singapore is Singapore-resident because Singapore taxes on effective management, not because Cayman "loses" — Cayman asserts nothing.
2. **Resolve dual residence with the treaty's own tie-breaker.**
   - **Decision point, and the one most engines get wrong:** do not assume place of effective management wins. Under the 2017 OECD Model Art. 4(3) and MLI Art. 4, dual-resident entities go to the **competent authorities**, who determine residence by mutual agreement having regard to effective management, incorporation, and other factors. Until that concludes, residence is `DUAL_RESIDENCE_UNRESOLVED`, `treaty_benefits_at_risk` is true, and **no treaty rate may be booked**: absent agreement the entity is entitled to no relief or exemption under that treaty at all.
   - Register `TIEBREAK_POEM` only where the treaty genuinely still reads that way. Many do — MLI Art. 4 is not a minimum standard and a Party may reserve out of it entirely.
   - No registered tie-breaker → `REVIEW_REQUIRED`, not a default.
3. **Run the individual presence tests** for the people who exercise judgement over the strategy. Weighted multi-year tests are evaluated in exact rational arithmetic.
   - **Decision point:** a decision-maker who becomes resident somewhere the entity is not is a signal to re-examine where the entity is managed, and whether a dependent agent permanent establishment has been created. The engine raises that action; it does not answer it.
4. **Flag permanent establishments.** Every fixed place of business outside the resolved residence country is flagged for Art. 5 analysis.
5. **Hand residence downstream.** Feed `resolved_residence_country` into `double-taxation-treaty-considerations-cross-border-trading` as the residence country. If it is `None`, that skill must not be run on assumed rates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming place of effective management breaks the tie.** It did under the pre-BEPS OECD Model and still does under the UN Model and many treaties in force. Under the 2017 OECD Model Art. 4(3) and MLI Art. 4 it is one factor the competent authorities weigh, and *in the absence of agreement the entity gets no treaty relief whatsoever*. Coding "POEM wins" turns a total loss of treaty benefits into a clean answer.
- **Applying a flat 183-day rule to every country.** India's basic test is **182** days. A universal 183 reports an Indian resident as non-resident at exactly the threshold.
- **Confusing the US Substantial Presence Test with a 183-day count.** It is 183 *weighted* days: the current year, plus one third of the first preceding year, plus one sixth of the second, with a separate 31-day floor in the current year. 130 days this year and 180 in each of the two prior years is 220 weighted days — resident — though no single year reaches 183. The IRS's own example runs the other way: 120 days a year for three years is 180 weighted days, and not resident.
- **Reading a failed day count as non-residence.** It is not a clearance. The UK sufficient ties test can make someone resident on fewer than 46 days, and India taxes a 60-day stay combined with 365 days over the preceding four years.
- **Applying a physical-presence test to a company.** Companies do not have physical presence; they have incorporation and management. Day counts belong to the individuals, and matter because of where they drag effective management, not because the entity itself "spent 190 days" anywhere.
- **Assuming unstaffed hardware cannot be a permanent establishment.** The OECD concluded that human intervention is *not* required for a PE to exist. Computer equipment at the enterprise's own disposal — owned or leased and operated by it, as opposed to a hosting arrangement — can be a PE where the functions performed through it exceed the preparatory or auxiliary threshold. An unmanned co-located rack executing the firm's own strategy is exactly the fact pattern to analyse, not to wave away.
- **Treating zero-tax incorporation as the end of the analysis.** The Cayman Islands International Tax Co-operation (Economic Substance) Act brings fund management business into scope, and relief on the basis of tax residence elsewhere has to be evidenced — which points straight back at wherever the entity is actually managed.
- **Losing precision at the statutory boundary.** 31 days plus 304 in each of the two preceding years is exactly 183 weighted days under the US test. Evaluated in binary floats the same sum is 182.99999999999997, and the taxpayer is reported non-resident on the day they became resident. Weights are `Fraction`; float weights are rejected at registration.

## Verification

- Register the US Substantial Presence Test (`day_threshold=183`, `US_SPT_WEIGHTS`, `min_days_current_year=31`) and assess 120 days in each of three years: expect `weighted_days == 180.0` and `meets_registered_test is False`, matching the IRS worked example.
- Assess 130 / 180 / 180: expect `220.0` and `True` — a resident no single-year threshold would catch.
- Assess 20 / 366 / 366: expect `203.0` weighted but `False`, defeated by the 31-day floor.
- Assess 31 / 304 / 304: expect exactly `183.0` and `True`, where float arithmetic yields 182.99999999999997.
- Register India at 182 days and assess 182: expect `True`.
- Assess a Cayman-incorporated entity (`taxes_on_incorporation=False`, `taxes_on_effective_management=False`) managed from Singapore: expect `SINGLE_RESIDENCE` resolved to `SG` on `EFFECTIVE_MANAGEMENT`.
- Assess an SG/UK dual resident under `TIEBREAK_COMPETENT_AUTHORITY` with no determination on file: expect `DUAL_RESIDENCE_UNRESOLVED`, `resolved_residence_country is None`, `treaty_benefits_at_risk is True`. Supply `competent_authority_determination="UK"` and expect `DUAL_RESIDENCE_RESOLVED`.
- Assess the same pair with no registered tie-breaker: expect `REVIEW_REQUIRED`, not a POEM default.
- Assess an SG-resident entity with an unmanned rack in DE: expect one PE flag naming the preparatory-or-auxiliary threshold.
- Run `python -m unittest discover -s skills/multi-jurisdiction-tax-residency-implications/scripts`.

## Related Skills

- `double-taxation-treaty-considerations-cross-border-trading`
- `transfer-pricing-for-multi-entity-trading-operations`
- `record-retention-periods-by-jurisdiction`

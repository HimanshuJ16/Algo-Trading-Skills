# Pre-Flight / Sign-off Checklist — kyc-aml-considerations-for-algo-trading-entities

Entity: ____________________  Assessment date: __________  Reviewer: __________

## Scope

- [ ] Recorded which side of the obligation this file serves: covered financial institution running CDD, or trading entity preparing its own file.
- [ ] Confirmed with counsel whether the entity is itself a BSA financial institution. The RIA/ERA AML rule effective date moved to **2028-01-01**; do not assume either way.

## Documents obtained (documents, not self-certification alone)

- [ ] Register of members / LP agreement / share register obtained.
- [ ] Ownership resolved through every intermediate holding company, trust, and nominee down to **natural persons**.
- [ ] Certificate of incorporation and evidence of the banking jurisdiction obtained.
- [ ] Identity evidence on file for each beneficial owner **and** the control person.

## Ownership prong

- [ ] Each natural person's holdings **aggregated across vehicles** before applying the threshold (15% + 15% = 30%).
- [ ] A real identifier (passport / national ID / LEI) used as the aggregation key — not a name string.
- [ ] Every aggregated holder at or above the threshold identified **and** identity-verified.
- [ ] Declared ownership summed; residual computed.
- [ ] Residual is **below one threshold's worth**, or the dispersion is documented and the tolerance consciously raised.

## Control prong — run this even when the ownership prong found nobody

- [ ] One individual with significant responsibility to control, manage, or direct the entity identified.
- [ ] Their **title** recorded (CEO, CFO, COO, Managing Member, General Partner, President, Vice President, Treasurer, or equivalent function).
- [ ] Their identity verified to CIP standard.
- [ ] If the control person also holds equity, that equity is declared once, as a `UboRecord`.

## Sanctions

- [ ] **Entity name** screened against OFAC SDN/Consolidated, EU, UN and UK HMT — screening actually run, not assumed.
- [ ] Every UBO and the control person screened; provider and run date recorded.
- [ ] **Aggregate** blocked ownership computed across all blocked persons, not per-person.
- [ ] If aggregate blocked ownership is **≥ 50%**: property **blocked and reported to OFAC** — not merely declined.
- [ ] Screening evidence archived with the file.

## Jurisdiction

- [ ] Incorporation **and** banking jurisdictions both screened.
- [ ] Country identifiers resolved to ISO 3166-1 alpha-2 before comparison; no unresolvable value treated as clean.
- [ ] Upstream placeholder codes for "unknown jurisdiction" (`XX`, `ZZ`, and similar) mapped to a rejection — any two-letter token is accepted as a code and would otherwise screen clean against every list.
- [ ] FATF list snapshot `as_of` date recorded on the report, and refreshed since the last plenary (roughly February, June, October).
- [ ] Call-for-action jurisdictions tiered correctly: counter-measures for Iran and the DPRK, **EDD** for Myanmar.
- [ ] Grey-list exposure handled as EDD, not refusal.

## PEP (FATF Recommendation 12)

- [ ] PEP status determined for every beneficial owner and the control person.
- [ ] `pep_category` recorded — FOREIGN / DOMESTIC / INTERNATIONAL_ORGANISATION — rather than left blank.
- [ ] **If a foreign PEP:**
  - [ ] Senior management approval obtained and minuted.
  - [ ] Source of **wealth** and source of **funds** established and documented.
  - [ ] Enhanced ongoing monitoring configured (outside this engine).
- [ ] No relationship refused *solely* because of PEP status — PEP measures are preventive, not an allegation.

## Record

- [ ] `assessment_date` passed explicitly; the run is reproducible.
- [ ] `screening_lists_as_of` recorded on the report.
- [ ] Every finding — including advisories — retained with its citation, not just the final status.
- [ ] Outstanding EDD conditions tracked to closure with an owner and a date.
- [ ] Retention period confirmed for each applicable jurisdiction.
- [ ] Re-review trigger set: next plenary, ownership change, new screening match, or facts calling prior information into question.

## Sign-off

- [ ] Reviewer: ____________________  Date: __________
- [ ] MLRO / Compliance officer: ____________________  Date: __________
- [ ] Counsel consulted where the structure or jurisdiction analysis is contested.

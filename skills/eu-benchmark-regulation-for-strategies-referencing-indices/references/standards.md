# Standards for EU Benchmark Regulation (EU BMR)

## What changed on 1 January 2026

Regulation (EU) 2025/914 (of 7 May 2025, published in the OJ on 19 May 2025, in
force 8 June 2025, **applicable from 1 January 2026**) amended Regulation (EU)
2016/1011. It is not a tidying exercise: it removed the great majority of indices
from the Regulation entirely. Any tool, checklist or policy written against the
pre-2026 text will now generate false positives.

| Before 1 January 2026 | From 1 January 2026 |
|---|---|
| BMR reached essentially every index used as a benchmark in the Union, including non-significant ones. | Article 2(1) reaches only critical benchmarks, significant benchmarks, EU CTBs/PABs, and commodity benchmarks subject to Annex II. |
| Article 29(1): a supervised entity could not use a benchmark unless the administrator was on the ESMA register. | The register gate applies to critical, CTB/PAB and Annex II commodity benchmarks. New references to a **significant** benchmark are barred only while it is the object of an Article 24a(6) public notice. |
| Third-country benchmarks needed equivalence, recognition or endorsement (Article 51(5) transitional ran to 31 December 2025). | Third-country benchmarks outside the four categories may be used freely. ESMA became the single competent authority for third-country administrators. |

## In-scope categories and their thresholds

| Category | Test |
|---|---|
| **Critical** | Listed by the Commission under Article 20. Currently EURIBOR, EONIA, STIBOR, WIBOR and NIBOR. The category applies to EU-located administrators. |
| **Significant** | Not critical, and: >= EUR 50bn EU use in financial instruments, financial contracts or funds averaged over six months; **or** designated by a Member State competent authority (ESMA for non-EU benchmarks) where there are no or very few substitutes and discontinuance would have significant adverse impacts; **or** designated on an EU administrator's own request at >= EUR 20bn. |
| **EU CTB / PAB** | Labelled as an EU Climate Transition Benchmark or EU Paris-aligned Benchmark and meeting the BMR requirements for that label. |
| **Commodity subject to Annex II** | Commodity benchmarks based on contributed input data, other than regulated-data benchmarks, those whose contributors are majority supervised entities, and critical gold/silver/platinum benchmarks. Also exempt: contributed commodity benchmarks whose referencing instruments average <= EUR 200m notional over 12 months. |

**Identification gap.** Critical benchmarks are published in an implementing act;
CTBs and PABs are labelled; significant benchmarks *subject to a warning notice*
appear on the ESMA register. There is **no comprehensive public source** for
significant benchmarks that are not subject to a warning notice, nor for Annex II
commodity benchmarks. Firms must establish this by other means. This engine
therefore takes `category` as an input and does not attempt to derive it.

## The user obligations this engine models

| Provision | Obligation | Modelled as |
|---|---|---|
| **Article 3(1)(17)** | The user obligations bind "supervised entities" — an enumerated list (credit institution, investment firm, insurance/reinsurance undertaking, UCITS, AIFM, IORP, consumer-credit creditor, mortgage-credit non-credit institution, market operator, CCP, trade repository, BMR administrator). | `entity_type`; `NON_SUPERVISED` short-circuits the audit. |
| **Article 3(1)(7)** | "Use of a benchmark" is a closed list: issuance of a financial instrument referencing an index; determination of an amount payable under a financial instrument or financial contract; being a party to a financial contract; providing a Consumer Credit Directive borrowing rate as a spread over an index; measuring an investment fund's performance to track the index, define asset allocation, or compute performance fees. | `use_type`; `NOT_A_BMR_USE` short-circuits the audit. |
| **Article 2(2)** | Exempts central banks, public authorities acting for public policy purposes, CCP reference/settlement prices, single reference prices for MiFID Annex I Section C instruments, press and media, own-borrowing-rate publication, unaware index providers, and Commission-designated non-EU spot FX benchmarks. | `article_2_2_exemption`. |
| **Article 29(1)** | A supervised entity must not **add** a new reference to a significant benchmark that is the object of an Article 24a(6) public notice, or to a critical / CTB / PAB / Annex II commodity benchmark whose administrator is not on the ESMA register. ESMA or the NCA may derogate for 6–24 months to avoid serious market disruption. | `is_new_reference`, `warning_notice_published_on`, `warning_notice_derogation_until`, `REGISTER_GATED_CATEGORIES`. |
| **Article 29(1a)** | A replacement designated under Article 23b or 23c may be used. | `designated_statutory_replacement` plus `relies_on_designated_statutory_replacement`. |
| **Article 29(1b)** | An entity using a benchmark that becomes the object of a public notice in **existing** instruments or contracts must replace it with an appropriate alternative within six months of publication, or issue and publish a statement on its website giving clients a reasoned explanation for not being able to do so. | `replacement_deadline`, `replacement_statement_published`. |
| **Article 28(2)** | Supervised entities other than administrators must produce and maintain robust written plans for material change or cessation; where feasible and appropriate designate one or more alternative benchmarks with reasons; provide the plans to the competent authority on request; and reflect them in the fallback provisions applicable to financial contracts, financial instruments and investment funds. | `has_written_fallback_plan`, `designates_alternative_benchmark`, `fallback_reflected_in_contractual_terms`. |

## Deliberately not modelled

- **Article 28(2), "provide to the competent authority on request"** — a process
  obligation with no observable state at audit time. A boolean asserting it would
  be unfalsifiable.
- **Administrator authorisation withdrawal and benchmark wind-down** (Articles 21,
  23, 35 and the Article 51 transitional machinery). The engine answers "may this
  reference be added or must it be replaced?", not "what happens when an
  administrator loses its authorisation mid-life?". A benchmark entering wind-down
  needs the Article 28(1) administrator procedure and, where one exists, the
  Article 23b/23c designated replacement — handled as a project, not a boolean.
- **Article 27 benchmark statements and Article 29(2) prospectus disclosure.**
  Article 29(2) requires a prospectus referencing an in-scope benchmark to state
  whether the administrator is on the ESMA register, and to disclose any warning
  notice without undue delay. That is a document-production obligation on the
  issuer or offeror rather than a per-strategy audit outcome.
- **Any spread-adjustment arithmetic.** See the statutory replacement table above.

## Register transition

| Date | Effect |
|---|---|
| 31 December 2025 | Article 51(5) transitional for third-country benchmarks ends (it had been extended to this date by Commission Delegated Regulation (EU) 2023/2222). |
| 1 January 2026 | Amended scope applies. Third-country administrators that applied for recognition or endorsement by 31 December 2025 may continue to be used pending ESMA's decision. |
| 30 September 2026 | Administrators on the register at end-2025 retain their authorisation, registration, recognition or endorsement status until this date. |
| 1 October 2026 | Administrators whose benchmarks fall outside scope are removed from the ESMA register. |

Article 29 also requires supervised entities intending to use in-scope benchmarks
to **regularly consult** the ESMA register to verify administrator status. It
names no period. `DEFAULT_REGISTER_CHECK_MAX_AGE_DAYS = 30` is a **firm policy
default with no regulatory basis**; set it to whatever the firm has documented.

## Statutory replacements — what is and is not fixed in law

| Item | Status |
|---|---|
| EONIA -> €STR + **8.5 basis points** | Fixed by Commission Implementing Regulation (EU) 2021/1848. The 8.5 bps is the one-off spread the ECB calculated and announced on 31 May 2019. |
| CHF LIBOR -> SARON-based rates | Fixed by Commission Implementing Regulation (EU) 2021/1847. |
| EURIBOR contractual fallbacks (€STR-based, with a spread adjustment) | **Not** an EU statutory designation. These are working-group recommendations and industry-published spread adjustments. Do not present any specific EURIBOR fallback spread as a regulatory figure; this skill publishes none. |
| €STR itself | Administered by the ECB, exempt under Article 2(2)(a). Not a critical benchmark; never on the Article 20 list. The pre-2.0 version of this skill described "ESTER" as critical, which was wrong on both the name (renamed €STR in March 2019) and the classification. |

## Scoring rules

| Rule | Behaviour |
|---|---|
| Scope order | Entity -> use -> Article 2(2) exemption -> Article 2(1) category. Register and plan tests run only after all four gates pass. |
| Assessment date | Governs which regime applies. `assessment_date < 2026-01-01` restores the pre-amendment scope. |
| Finding aggregation | Every limb is evaluated; the audit never stops at the first failure. Status is `VIOLATION` > `ACTION_REQUIRED` > `COMPLIANT`. |
| Article 29(1b) boundary | Inclusive: overdue requires `assessment_date > deadline`, so exactly on the deadline is not yet a breach. Six months is added by calendar month with end-of-month clamping. |
| Public notice boundary | A notice takes effect on its publication date; a derogation covers dates up to and including its end date. |
| Data errors | An unknown benchmark id, an unrecognised category, entity type, use type or exemption, a duplicate registration, a derogation without a notice, a notice on a non-significant benchmark, or a register check dated after the assessment all raise `BmrConfigurationError`. None is ever reported as a regulatory finding. |
| Advisories | `NO_ALTERNATIVE_DESIGNATED`, `REGISTER_CHECK_STALE`, `REGISTER_CHECK_PREDATES_AMENDMENT` and `CENTRAL_BANK_PLAN_ADVISORY` do not make a report non-compliant. |

## Jurisdiction

Everything above is the **EU** regime. The UK onshored BMR under the European
Union (Withdrawal) Act 2018 and did not take the 2025/914 scope cut; a UK
supervised entity tests Article 29 against the FCA's UK Benchmarks Register.
A dual-regulated group needs both assessments.

## Sources

- Regulation (EU) 2016/1011 (Benchmarks Regulation), consolidated text and article
  commentary — ESMA Interactive Single Rulebook:
  Article 2 (Scope) https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/benchmarks-regulation/article-2-scope ·
  Article 28 (Changes to and cessation of a benchmark) https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/benchmarks-regulation/article-28-changes-and ·
  Article 29 (Use of critical benchmarks, significant benchmarks, commodity
  benchmarks subject to Annex II, EU Climate Transition Benchmarks and EU
  Paris-aligned Benchmarks) https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/benchmarks-regulation/article-29-use-critical
- Regulation (EU) 2025/914 of 7 May 2025 amending Regulation (EU) 2016/1011 as
  regards the scope of the rules for benchmarks, the use in the Union of benchmarks
  provided by an administrator located in a third country, and certain reporting
  requirements — https://eur-lex.europa.eu/eli/reg/2025/914/oj
- ESMA, Public statement on transitional provisions under the BMR review
  (ESMA81-1841807023-996) — https://www.esma.europa.eu/document/public-statement-transitional-provisions-under-bmr-review
- ESMA, Benchmarks policy and registers — https://esma.europa.eu/policy-rules/benchmarks
- ESMA, Q&As on the Benchmarks Regulation (ESMA70-145-114), including the
  central-bank exemption and written-plan Q&As — https://www.esma.europa.eu/sites/default/files/library/esma70-145-114_qas_on_bmr.pdf
- Commission Implementing Regulation (EU) 2016/1368 establishing the list of
  critical benchmarks — https://eur-lex.europa.eu/eli/reg_impl/2016/1368/oj
- Commission Implementing Regulation (EU) 2021/1848 designating €STR plus a fixed
  spread of 8.5 basis points as the replacement for EONIA — https://eur-lex.europa.eu/eli/reg_impl/2021/1848/oj/eng
- Clifford Chance, *EU Benchmarks Regulation: a guide for benchmark users*
  (February 2026) — https://www.cliffordchance.com/content/dam/cliffordchance/briefings/2026/02/eu-benchmarks-regulation_%20a-guide-for-benchmark-users.pdf
- STOXX Ltd, *FAQ: Changes to the BMR regulation on January 1, 2026* (August 2025),
  including the list of STOXX indices remaining in scope — https://www.stoxx.com/document/Resources/Regulation/2026_BMR_Change_FAQ_120825_Final.pdf
- ICE Benchmark Administration Limited, *Status under EU and UK Benchmarks
  Regulations* (21 January 2026) — https://www.ice.com/publicdocs/ICE_Benchmark_Administration_Limited_-_BMR_Status.pdf

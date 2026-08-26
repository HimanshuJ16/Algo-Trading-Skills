# Standards — insider-trading-controls-for-alternative-data-usage

## Jurisdiction and scope

Everything below is **US federal securities law and US/EU data privacy law**, applied
to an investment adviser registered (or required to be registered) with the SEC.
Section 204A is an *adviser* obligation — a proprietary trading firm that is not an
adviser has no Section 204A duty but remains fully subject to Section 10(b) and
Rule 10b-5. EU MAR, UK, Singapore and Hong Kong market-abuse regimes are out of
scope. Nothing here is legal advice.

## What actually makes alt data unlawful to trade on

The common framing — "the dataset contains MNPI, therefore we cannot trade" — is
not the legal test, and applied literally it would bar the entire alternative
data industry, whose product is by construction nonpublic and material.

Liability under Rule 10b-5 requires trading **on the basis of** material nonpublic
information **in breach of a duty**:

| Theory | Duty breached | Authority |
|---|---|---|
| Classical | Insider's fiduciary duty to shareholders | *Chiarella*, *Dirks* |
| Misappropriation | Duty of trust or confidence owed to the **source** of the information | *United States v. O'Hagan*, 521 U.S. 642 (1997) |

Rule 10b5-2 supplies a non-exclusive list of relationships giving rise to a duty of
trust or confidence for misappropriation purposes. The practical consequence for an
alt-data buyer is that **provenance governs**: the question is not "is this signal
valuable?" but "did anyone in the chain hand this over in breach of an obligation?"

A fund manager who did not misappropriate anything can still face exposure where a
**vendor** did and the manager knew or was reckless in not knowing of it — which is
why the vendor-diligence gate is a securities-law control, not merely procurement
hygiene.

The SEC's Division of Examinations states the point directly: alternative data
"does not necessarily contain MNPI." The staff's concern is the **absence of
policies** addressing the risk that it might.

Sources:
[United States v. O'Hagan, 521 U.S. 642 (1997)](https://supreme.justia.com/cases/federal/us/521/642/),
[17 CFR 240.10b5-2](https://www.ecfr.gov/current/title-17/chapter-II/part-240/subpart-A/subject-group-ECFR71e2d22647918b0/section-240.10b5-2),
[Proskauer Hedge Fund Trading Guide, Ch. 2 — Insider Trading](https://www.proskauer.com/pub/proskauer-hedge-fund-trading-guide-2024-chapter-2-insider-trading-focus-on-subtle-and-complex-issues).

## Section 204A — the policy itself is the obligation

Section 204A of the Investment Advisers Act requires an adviser to **establish,
maintain, and enforce** written policies and procedures reasonably designed to
prevent the misuse of MNPI by the firm or its personnel. Rule 204A-1 adds the code
of ethics and personal-trading reporting requirements for access persons.

This is independently chargeable. The SEC has settled Section 204A actions in which
the order contained **no finding that anyone traded on MNPI at all** — for example
an August 2024 settled action against a CLO manager (\$1.8M) turning on the absence
of policies addressing MNPI reaching the firm from underlying loan borrowers, and a
September 2024 action against a distressed-debt adviser (\$1.5M) over the absence
of a monitoring framework for creditors'-committee participation.

The **April 26, 2022** Risk Alert, *Investment Adviser MNPI Compliance Issues*, is
the staff's clearest statement of what it expects specifically for alternative
data. Named deficiencies:

| Deficiency observed | Control in this skill |
|---|---|
| Due diligence not consistently applied or **recorded** across all alt-data sources | `has_vendor_diligence_signoff`, and persisting the report |
| No policies for assessing the **terms, conditions or legal obligations** attached to collection or provision of the data | `is_tos_compliant` |
| No process for deciding **when re-diligence is required** — on elapsed time or on a change in collection practices | re-run cadence, see `references/workflows.md` |
| Red flags about a data source not acted on | escalation path, fail closed |

The Risk Alert defines alternative data as data from non-traditional sources —
social media and internet search data, satellite and drone imagery, analyses of
aggregate credit card transactions, consumer mobile geolocation, and email data
from consumer apps.

Sources:
[SEC Division of Examinations Risk Alert, *Investment Adviser MNPI Compliance Issues*, 2022-04-26](https://www.sec.gov/files/code-ethics-risk-alert.pdf),
[K&L Gates summary](https://www.klgates.com/SECs-Division-of-Examinations-Issues-Risk-Alert-on-Investment-Adviser-MNPI-Compliance-Issues-5-4-2022),
[Cozen O'Connor — SEC asserts 204A enforcement authority despite no MNPI misuse](https://www.cozen.com/news-resources/publications/2024/sec-asserts-enforcement-authority-for-inadequate-204a-policies-and-procedures-despite-no-mnpi-misuse).

## Vendor representations are not a control — *In re App Annie*

The canonical alt-data enforcement action, and the reason `has_vendor_diligence_signoff`
must mean *verified*, not *asserted*.

| | |
|---|---|
| Order | Admin. Proc. File No. 34-92975, **2021-09-14** |
| Respondents | App Annie, Inc.; Bertrand Schmitt (co-founder, former CEO) |
| Charge | Section 10(b) / Rule 10b-5 |
| Conduct | Terms of Service represented that estimates were generated by statistical models from **aggregated and anonymized** data. From late 2014 to mid-2018 App Annie used **actual non-aggregated, non-anonymized** company performance data to adjust its model output. |
| Penalties | \$10,000,000 (App Annie); \$300,000 and a three-year officer-and-director bar (Schmitt) |
| Significance | First SEC enforcement action charging an alternative data provider with securities fraud |

The lesson runs against the intuitive control: App Annie **had** written
representations covering aggregation and anonymisation, and they were false. A
buyer's diligence must reach the underlying practice — right-to-audit exercised,
sample-data inspection, or third-party attestation.

Source: [Gibson Dunn — SEC Announces First Enforcement Action Against Alternative Data Provider](https://www.gibsondunn.com/sec-announces-first-enforcement-action-against-alternative-data-provider-for-securities-fraud-highlighting-regulatory-risks-in-growing-industry/).

## Web scraping — the CFAA is narrow; contract is not

| Authority | Holding | Consequence |
|---|---|---|
| *Van Buren v. United States*, 593 U.S. 374 (2021) | "Exceeds authorized access" is a gates-up-or-down inquiry: liability attaches to obtaining information from areas of a computer that are **off-limits**, not to misusing information one is entitled to access | Violating a site's terms for an improper purpose is not, by itself, a CFAA violation |
| *hiQ Labs v. LinkedIn* (9th Cir.) | Scraping **public** pages without authentication likely does not violate the CFAA | Public-data scraping routes to ToS review, not a hard CFAA reject |
| *hiQ Labs v. LinkedIn* (N.D. Cal. 2022; settled Dec 2022) | LinkedIn won summary judgment on **breach of contract**; settlement carried a \$500,000 judgment and hiQ's liability for trespass to chattels and misappropriation | "Not a federal crime" ≠ "no exposure" |

Terms-of-service compliance therefore matters on two independent axes, which is why
`is_tos_compliant` is scored separately from `has_vendor_diligence_signoff`:

1. **Contract and tort exposure** inherited from the vendor's collection method.
2. **Securities-law exposure** — a confidentiality obligation in the source's terms
   is exactly the kind of duty whose breach converts the data into misappropriated
   MNPI. That is the mechanism in App Annie.

Sources: [Van Buren v. United States (opinion PDF)](https://www.supremecourt.gov/opinions/20pdf/19-783_k53l.pdf),
[Morgan Lewis — LinkedIn v. hiQ: Landmark Data Scraping Suit](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2022/12/linkedin-v-hiq-landmark-data-scraping-suit-provides-guidance-to-data-scrapers-and-web-operators).

## PII — neither GDPR nor CCPA/CPRA specifies a number

| Instrument | Standard | Numeric threshold? |
|---|---|---|
| GDPR Recital 26 | Data is anonymous when the subject is "not or no longer identifiable," assessed against **all means reasonably likely to be used**, including foreseeable technical developments | **None** |
| Cal. Civ. Code § 1798.140(b) — "aggregate consumer information" | Relates to a group or category of consumers, individual identities removed, "not linked or reasonably linkable to any consumer or household, including via a device" | **None** |
| Cal. Civ. Code § 1798.140(m) — "deidentified" | Cannot reasonably be used to infer information about, or be linked to, a particular consumer, subject to reasonable measures, public commitment, and contractual obligations | **None** |

Both regimes state an **outcome standard** (unlinkability), not a group size. A
minimum panel count is a proxy for that standard drawn from k-anonymity practice,
where published guidance on minimum cell size ranges roughly from 3 to 30 and no
single value is authoritative. It is a useful, auditable floor — and it is a
**firm-policy choice**, not a legal requirement. Identifier removal alone does not
satisfy either regime; singling-out, linkability and inference risk must be
assessed.

Sources: [GDPR Recital 26](https://gdpr-info.eu/recitals/no-26/),
[Cal. Civ. Code § 1798.140](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=1798.140).

## Earnings blackout — firm policy, with no regulatory analogue

There is **no SEC rule imposing a blackout window on trading alternative-data
signals around earnings.** The engine's `earnings_blackout_window_hours` is a
self-imposed risk control: MNPI-contamination risk is highest when a dataset could
anticipate an imminent disclosure, and a pause is cheap insurance.

For contrast, the codified waiting periods in adjacent areas are narrower and serve
different purposes:

| Provision | Period | What it actually governs |
|---|---|---|
| Rule 10b5-1(c) cooling-off, directors and officers | Later of 90 days, or 2 business days after the next 10-Q/10-K | Availability of the affirmative defense for an **insider trading plan** |
| Rule 10b5-1(c) cooling-off, other persons | 30 days | Same |
| Rule 10b5-1(c), issuers | None | Same |

Those amendments took effect **2023-02-27**. None of them reaches a firm's use of
research data. Do not cite them as authority for the blackout gate.

Source: [Skadden — SEC Amends Rules for Rule 10b5-1 Trading Plans](https://www.skadden.com/insights/publications/2022/12/sec-amends-rules-for-rule-10b51-trading-plans-and-adds-new-disclosure-requirement).

## Engineering defaults (not regulatory requirements)

| Parameter | Default | Basis |
|---|---|---|
| `min_panel_aggregation_count` | 50 | k-anonymity-style cell-size floor; **no regulatory basis** |
| `earnings_blackout_window_hours` | 48.0 | Two-sided firm-policy pause; **no regulatory basis** |

Both are constructor arguments. Calibrate them to the firm's mandate, data mix and
counsel's advice, and record the calibration alongside the policy. Setting
`earnings_blackout_window_hours=0.0` disables the blackout gate explicitly, which is
preferable to leaving a stale default in place and calling it a control.

## Control semantics

| Field | Means | Fails into |
|---|---|---|
| `has_mnpi_risk` | Provenance implies a breached duty of trust or confidence. Default `True` when provenance is unknown. | `MNPI_PROVENANCE` |
| `has_vendor_diligence_signoff` | A current, documented diligence record exists (from `alternative-data-vendor-due-diligence-checklist`) | `VENDOR_DILIGENCE_SIGNOFF` |
| `is_tos_compliant` | Collection is consistent with the source's terms | `TERMS_OF_SERVICE` |
| `is_pii_scrubbed` | Identifiers removed **and** removal independently verified | `PII_SCRUBBING` |
| `panel_aggregation_count` | Distinct contributors behind each published observation | `PANEL_AGGREGATION` |
| `hours_to_earnings_release` | Signed hours to nearest release (+ before, − after); `None` = none scheduled | `EARNINGS_BLACKOUT` |

## Category

`regulatory-compliance` / `data-sourcing` — see the top-level `mappings/` directory
for how this category rolls up across the full skill library.

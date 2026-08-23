# Standards for Data Vendor Contractual Usage Restriction Tracking

All engineering standards below are traced to a primary source. Where a rule is
specific to one venue or agreement, that is stated — do not universalise it to
every vendor contract without reading yours.

## Engineering standards

| Standard | Requirement | Source |
|---|---|---|
| Fail closed on undeclared use | A use of the data not already provided for in the licence MUST be refused until approved in writing and priced. | Nasdaq GDA s.4(c) |
| External redistribution lock | External distribution MUST be hard-blocked unless the contract permits it; the recipient side carries its own agreement obligations. | Nasdaq GDA s.4(c), s.8(b) |
| Non-display enforcement | Automated, machine-consumed use MUST be verified against a non-display entitlement before the feed is opened. | Nasdaq US Equities and Options Data Policies s.7 |
| Contract term enforcement | A request evaluated after the licensed term has ended MUST be denied, not merely warned about. | Contractual term; general |
| Concurrency headroom | Reserved entitlement units MUST NOT exceed the contracted cap, and MUST be released when the consuming system disconnects. | Contract seat schedules; general |
| Auditable counting procedure | The firm MUST have a quantifiable and auditable procedure for counting fee-liable units. | Nasdaq US Equities and Options Data Policies s.7 |
| Decision record retention | Access decisions MUST be persisted durably for at least the audit look-back period (three years under the Nasdaq GDA). An in-process buffer does not satisfy this. | Nasdaq GDA s.7(e) |

## Definitions that drive the checks

**Non-Display Usage** — "any method of accessing Exchange Information other than
Display Usage … a means of accessing Nasdaq data that involves automated access or
use by a machine, without access or use of a Display by a natural person or
persons." Non-display is fee-liable regardless of whether the OMS, EMS or trading
infrastructure is virtual, cloud-hosted, in a datacenter, enterprise, or on an
individual's desktop.
Source: Nasdaq, *US Equities and Options Data Policies*, v2.6, s.7.

**Non-Display unit of count** — the greater of (a) the number of Subscribers that
can modify the application in real time, or (b) the number of Devices (usually
servers) that receive and benefit from the Information, including servers that run
computations or create derived data. Cores on one physical device count once; GPUs
and memory attached to an already-counted server are not counted separately.
Source: as above, s.7 and the definitions table.

**Derived Data** — "any information generated in whole or in part from Exchange
Information such that the information generated cannot be reverse engineered to
recreate Exchange Information or be used to create other data that is recognizable
as a reasonable substitute for such Exchange Information." Anything that fails this
test is still Exchange Information.
Source: as above, s.3.

**External Distribution** — distribution of the Information outside the
Distributor's entity as defined by the Global Data Agreement.
Source: as above, definitions table.

## Audit exposure (why the gate fails closed)

Under the Nasdaq Global Data Agreement (v4.87), Section 7 — Distributor Audit:

- **s.7(a)** — Nasdaq may have the Distributor's records, reports, payments and the
  systems used to receive or use the Information reviewed by Nasdaq personnel or
  auditors of its choice, "no more than once in any twelve (12) month period unless
  necessary due to suspected non-compliance with the material provisions of this
  Agreement." Nasdaq makes reasonable efforts to give at least four weeks' written
  notice, unless the audit follows suspected material non-compliance.
- **s.7(b)–(d)** — a preliminary audit response normally follows within 90 days; if
  no agreement is reached within 90 days of receipt, Nasdaq's determination (the
  "Final Audit") is deemed conclusive.
- **s.7(e)** — amounts found underreported or underpaid are remitted with interest
  within sixty days; failure to remit permits termination. For a good-faith error,
  liability is limited to unpaid fees plus interest for the **three years**
  preceding the date the non-compliance was first known or determined.
- **s.7(f)** — where the shortfall is 10% or more of reported Reportable Units, the
  Distributor also reimburses Nasdaq's audit, legal and administrative costs.

Section 4(c) — any use of the Information not provided for in the Nasdaq
Requirements, "including, but not limited to, developing or communicating
derivative information based upon the Information, retransmission, redistribution,
reproduction or calculation of indices", requires a revised usage submission,
prior written approval, and payment of the applicable fees.

## Vendor entitlement systems (context, not enforced here)

- **Bloomberg B-PIPE** — enterprise consolidated real-time feed intended to serve
  Bloomberg applications, third-party and internal proprietary applications, and
  non-display ("black box") applications. Entitlements are administered through
  Bloomberg's Entitlements Management and Reporting System (EMRS). *Confidence:
  medium — B-PIPE's scope is documented by Bloomberg; the EMRS name expansion comes
  from secondary sources, as Bloomberg's own EMRS documentation is behind
  authentication.*
- **LSEG DACS** — the Data Access Control System is the permissioning subsystem of
  the LSEG Real-Time Distribution System (formerly TREP), programmatically
  addressable through OpenDACS. Note the branding: "Refinitiv Elektron" is legacy
  naming for what LSEG now markets as LSEG Real-Time.

These systems enforce entitlements at the feed. This skill enforces the contractual
boundary upstream of them; the two are complementary, not substitutes.

## Sources

- Nasdaq, *Global Data Agreement*, v4.87 —
  https://www.nasdaqtrader.com/content/AdministrationSupport/AgreementsData/globaldataagreement_redlined.pdf
- Nasdaq, *US Equities and Options Data Policies*, v2.6 —
  https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/USEquitiesandOptionsDataPolicies.pdf
- Nasdaq, *Data News #2015-9: Clarification for U.S. Non-Display Policy* —
  https://www.nasdaqtrader.com/TraderNews.aspx?id=dn2015-09
- Bloomberg, *Real-Time Market Data Feed (B-PIPE)* —
  https://professional.bloomberg.com/products/data/enterprise-catalog/real-time-data-feed/
- LSEG Developer Portal, *An Introduction to the DACS Entitlement System* —
  https://developers.lseg.com/en/article-catalog/article/introduction-dacs-entitlement-system-opendacs-developers

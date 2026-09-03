# Standards — regulatory-custody-requirements-by-jurisdiction

Nothing here is legal advice. Custody qualification and the validity of a trust
are legal conclusions for counsel; this file records what the instruments say and
where to read them.

## Why rules are keyed by regime, not by country

There is no such thing as "the custody rule in the US". Each row below is a
distinct regime with its own regulator, instrument and requirements. A question
about one, answered with another's rules, produces a confident wrong answer.

| Regime key | Regulator | Governing instrument | Modelled |
|---|---|---|---|
| `US:SECURITIES` | SEC | 17 CFR 275.206(4)-2 (Advisers Act custody rule) | Yes |
| `US:CRYPTO` | SEC | 17 CFR 275.206(4)-2 as applied to crypto assets, plus the 2025-09-30 staff no-action route | Yes |
| `EU:CRYPTO` | National competent authorities (ESMA/EBA at Union level) | Regulation (EU) 2023/1114 (MiCA) | Yes |
| `UK:SECURITIES` | FCA | CASS 6; SUP 3.10 | Yes |
| `UK:CRYPTO` | FCA | FSMA 2000 (Cryptoassets) Regulations 2026; CASS 17 | Yes, from 2027-10-25 |
| `SG:CRYPTO` | MAS | Payment Services Act 2019 and Payment Services Regulations | Yes |
| US broker-dealer customer protection | SEC | 17 CFR 240.15c3-3 | **No** |
| EU custody of financial instruments | NCAs | MiFID II; AIFMD Art. 21 | **No** |
| SG capital markets services custody | MAS | Securities and Futures Act | **No** |

## United States — 17 CFR 275.206(4)-2

Applies to **SEC-registered investment advisers**. State-registered advisers
follow their state's rules.

| Requirement | Provision |
|---|---|
| Client assets maintained with a qualified custodian | (a)(1) |
| Held in a separate account in the client's name, or in accounts containing only clients' assets in the adviser's name as agent or trustee | (a)(1)(i)–(ii) |
| Verified by **actual examination at least once each calendar year** by an independent public accountant, at a time chosen by the accountant without prior notice | (a)(4) |
| Written internal control report from an independent public accountant, where the qualified custodian is the adviser or a related person | (a)(6) |
| Definition of *qualified custodian*: banks and savings associations, registered broker-dealers, registered FCMs, and certain foreign financial institutions | (d)(6) |

**There is no SEC-granted designation called "Qualified Custodian".** An entity
either falls within a (d)(6) category or it does not; a vendor asserting the
status is telling you nothing verifiable.

### Exceptions that matter, and that a naive engine gets wrong

| Exception | Provision | Effect |
|---|---|---|
| Custody arises **solely** from the authority to deduct advisory fees | (b)(3) | Relieves the (a)(4) independent verification |
| Pooled investment vehicle audited at least annually by a PCAOB-registered accountant, with GAAP financial statements distributed to investors **within 120 days** of fiscal year end | (b)(4) | Relieves the account-statement and verification requirements for that pool |

Source: [17 CFR 275.206(4)-2 (Cornell LII)](https://www.law.cornell.edu/cfr/text/17/275.206(4)-2);
[SEC — Staff Responses to Questions About the Custody Rule](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/staff-responses-questions-about-custody-rule).

### Crypto assets: the conditional state-trust route

- The proposed **Safeguarding Rule**, which would have extended the custody rule
  expressly to non-security crypto assets, was **withdrawn on 2025-06-12**.
  Rule 206(4)-2 remains the operative rule.
- On **2025-09-30** the SEC's Division of Investment Management issued a **staff
  no-action letter** stating it would not recommend enforcement against advisers
  and registered funds using a **state trust company** as qualified custodian for
  crypto assets and related cash, subject to conditions: a reasonable basis after
  inquiry that the state regulator authorises crypto custody (reassessed
  annually), audited GAAP financials, a recent SOC 1 or SOC 2 report, a custody
  agreement barring lending, pledging, rehypothecation or transfer without
  written consent and requiring segregation from proprietary assets, disclosure
  of material risks, and a documented best-interest determination.
- The letter **did not** hold that state trust companies satisfy the Advisers Act
  "bank" definition. Staff no-action positions are not rules, are fact-specific,
  and are revocable.

The engine models this as a single gate on the state-trust route; the individual
conditions are scored in `custody-solution-vendor-due-diligence-checklist`.

Sources: [Sidley](https://www.sidley.com/en/insights/newsupdates/2025/10/sec-staff-issues-no-action-relief-permitting-use-of-state-chartered-trust-companies),
[Morgan Lewis](https://www.morganlewis.com/pubs/2025/10/crypto-custody-breakthrough-sec-staff-grants-relief-for-registered-funds-advisers),
[Akin](https://www.akingump.com/en/insights/alerts/sec-allows-state-chartered-trust-companies-to-serve-as-crypto-custodians).

## European Union — Regulation (EU) 2023/1114 (MiCA)

MiCA has applied to crypto-asset service providers since **2024-12-30**. The
Article 143(3) transitional regime ran to an outer limit of **2026-07-01**;
several Member States closed theirs earlier (Germany and Ireland on 2025-12-31;
the Netherlands, Poland, Latvia, Hungary and Slovenia after six months).

### Article 75 — custody and administration on behalf of clients

| Requirement | Paragraph |
|---|---|
| Register of positions opened in the name of each client | 75(2) |
| Custody policy with internal rules and procedures for safekeeping or control | 75(3) |
| Segregation of clients' holdings from the provider's own, with the means of access to clients' crypto-assets clearly identified | 75(7) |
| Liability for loss **capped at the market value of the crypto-asset lost at the time the loss occurred** | 75(8) |

**Article 75 does not mention insurance anywhere.**

### Article 67 — prudential safeguards

Safeguards must at all times be at least the higher of:

- the Annex IV permanent minimum capital for the service class, and
- **one quarter of the preceding year's fixed overheads**, reviewed annually.

Article 67(4) permits those safeguards to take the form of own funds (CET1 items
per Articles 26–30 of Regulation (EU) No 575/2013), **or an insurance policy
covering the Union territories where the services are provided, or a comparable
guarantee**. Article 67(5) sets the policy's characteristics: an initial term of
not less than one year, a cancellation notice period of at least 90 days, cover
from an undertaking authorised to provide insurance, and provision by a
third-party entity.

| Annex IV class | Amount | Services |
|---|---|---|
| Class 1 | EUR 50,000 | Advice, reception and transmission of orders, portfolio management, transfer services |
| **Class 2** | **EUR 125,000** | **Custody and administration**, plus exchange services |
| Class 3 | EUR 150,000 | Operation of a trading platform |

So insurance is a permitted *form* of a capital requirement, not a custody
mandate — and a policy that clears EUR 125,000 still fails Article 67 if a
quarter of fixed overheads is higher.

Sources: [MiCA Art. 75](https://www.mica.wtf/mica/title-v-authorisation-and-operating-conditions-for-crypto-asset-service-providers-art.-59-85/chapter-3/article-75),
[MiCA Art. 67](https://www.mica.wtf/mica/title-v-authorisation-and-operating-conditions-for-crypto-asset-service-providers-art.-59-85/chapter-2/article-67),
[EBA Single Rulebook — Article 75](https://www.eba.europa.eu/regulation-and-policy/single-rulebook/interactive-single-rulebook/17894),
[ESMA — MiCA](https://www.esma.europa.eu/esmas-activities/digital-finance-and-innovation/markets-crypto-assets-regulation-mica).

## United Kingdom — FCA CASS

CASS has **no "qualified custodian" concept**. The gate is FCA authorisation for
the regulated activity of safeguarding and administering investments (FSMA 2000
s.19).

| Requirement | Provision |
|---|---|
| Holding of safe custody assets so they are identifiable and separate from the firm's own assets | CASS 6.2 |
| Use of safe custody assets (including restrictions on the firm's own use) | CASS 6.4 |
| Records, accounts and reconciliations | CASS 6.6 |
| Auditor's **client assets report** on compliance with the custody rules, client money rules and mandate rules, delivered to the FCA **within four months** of the end of the period covered | SUP 3.10 |

The SUP 3.10 report names each individual rule breached, or confirms none were
found. It is not a US-style surprise examination and should not be described as
one.

### Cryptoassets

- CASS 6 already applies to the custody of **relevant specified investment
  cryptoassets** — cryptoassets that are themselves specified investments.
- The broader regime arrives separately: the **Financial Services and Markets Act
  2000 (Cryptoassets) Regulations 2026** were passed by Parliament on
  **2026-02-04**, and the FCA published its policy statements on **2026-06-30**
  (PS26/9 admissions and disclosures and market abuse, PS26/10 stablecoin
  issuance, PS26/11 regulated cryptoasset activities, PS26/12 prudential
  requirements, PS26/13 application of the Handbook). Safeguarding cryptoassets
  is one of the newly regulated activities, and **CASS 17** carries the
  safeguarding requirements — ownership rights, record-keeping, reconciliation
  and private key management.
- **Full commencement is 2027-10-25.** The application window for savings
  provisions runs 2026-09-30 to 2027-02-28. CASS 17 is not being applied to
  relevant specified investment cryptoasset custody at this stage; that stays
  under CASS 6.

Sources: [FCA — overview of our cryptoassets regime policy statements](https://www.fca.org.uk/publications/policy-statements/cryptoasset-regime),
[FCA Handbook CASS 6](https://handbook.fca.org.uk/handbook/cass6),
[FCA Handbook SUP 3.10](https://www.handbook.fca.org.uk/handbook/SUP/3/10.html).

## Singapore — MAS, digital payment token services

Scope here is **DPT services under the Payment Services Act 2019**. Custody by
capital markets services licensees under the Securities and Futures Act is a
separate regime and is not modelled.

MAS consulted in 2022, published Part 1 of its response on **2023-07-03**, and
finalised the segregation and custody requirements in its **2024-04-02** response
to the consultation on amendments to the Payment Services Regulations. Section
21A of the Payment Services Act, empowering MAS to prescribe segregation, custody
and safeguarding requirements, took effect **2024-04-04**.

| Requirement | Nature |
|---|---|
| Customers' assets deposited into a **trust account held on trust** for the customer | Mandatory |
| Segregation of customers' assets from the provider's own assets, with proper books and records | Mandatory |
| Daily reconciliation of customers' assets | Mandatory |
| At least **90%** of customers' DPTs held in wallets not connected to the internet | **Supervisory expectation** ("should"), not a statutory obligation |

Two things MAS **did not** do, and which are commonly asserted anyway:

- **No mandated independent third-party custodian.** A provider may maintain the
  trust account itself, subject to suitability assessment and controls.
- **No mandated insurance over custodied tokens.** MAS's position treats loss-
  handling arrangements — compensation or insurance — as something to be
  **disclosed** to customers, not as a custody requirement. No MAS instrument
  located in this review imposes an insurance mandate.

MAS licensee audit obligations under the Payment Services Act are not modelled
here, because the review did not verify their precise scope; treat that as a gap
rather than as an absence of obligation.

Sources: [MAS — Guidelines on Consumer Protection Measures by DPT Service Providers (PS-G03)](https://www.mas.gov.sg/regulation/guidelines),
[Sidley — Singapore to Impose New Custody Rules on Crypto Service Providers](https://www.sidley.com/en/insights/newsupdates/2023/07/singapore-to-impose-new-custody-rules-on-crypto-service-providers),
[Ocorian — MAS finalises crypto segregation and custody regulations](https://www.ocorian.com/knowledge-hub/insights/mas-finalises-crypto-segregation-and-custody-regulations),
[MAS — Expanded scope of regulated payment services](https://www.mas.gov.sg/news/media-releases/2024/mas-expands-scope-of-regulated-payment-services).

## Confidence and currency

| Claim | Confidence | Note |
|---|---|---|
| Rule 206(4)-2 requirements and (b)(3)/(b)(4) exceptions | High | Rule text |
| MiCA Art. 67 and 75 content, Annex IV Class 2 amount | High | Regulation text |
| CASS 6 / SUP 3.10 requirements | High | FCA Handbook |
| UK cryptoasset commencement 2027-10-25 and CASS 17 | High | FCA policy statement page |
| 2025-09-30 no-action letter conditions | High | Multiple law-firm summaries; the letter itself is the primary source |
| MAS: no insurance mandate, no mandated independent custodian | Medium-high | Supported by MAS's published response and consistent secondary reporting; stated as an absence of an obligation, which is harder to evidence than its presence |
| MAS licensee audit obligations | Not assessed | Deliberately not modelled |

Re-verify before relying on any of it: this is a moving area, and the UK regime
in particular changes on a known date.

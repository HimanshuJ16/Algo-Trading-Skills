# Record Retention Floors by Jurisdiction

**Currency of this file: verified August 2026.** Retention periods are set by
instrument-specific provisions, not by country. Re-verify before any figure here drives a
purge, and confirm which instrument binds *your* entity: the periods below attach to
particular firm statuses (SEC-registered broker-dealer, FINRA member, FCA common platform
firm, MAS CMS licence holder, Australian company, SEBI-registered stock broker), not to
anyone who happens to trade in that country.

## 0. The single most important correction

There is **no single retention period per country**. Under SEC Rule 17a-4 alone, blotters
and ledgers are preserved for six years while business communications are preserved for
three, and client account information runs six years from *account closure* rather than
from record creation. Any table with one number per country is wrong for at least one
record class in that country.

Version 1.0.0 of this skill asserted a flat `US=7, UK=5, SG=5, AU=7, IN=8, EU=5`. Two of
those six figures had no basis in the rule cited (US 7 years, India 8 years under SEBI),
and all six collapsed record classes that the underlying rules separate. The table below
replaces them.

## 1. United States

Applies to an SEC-registered broker-dealer; the FINRA rows apply where the firm is also a
FINRA member.

| Record class | Period | Clock starts | Instrument |
|---|---|---|---|
| Blotters, general ledger, customer account ledgers, ledger of long/short positions (17a-3(a)(1)–(3), (a)(5)) | **6 years**, first 2 in an easily accessible place | Record creation | 17 CFR 240.17a-4(a) |
| Order memoranda (17a-3(a)(6)–(7)) | **3 years** under the SEC rule; **6 years** for FINRA members | Record creation | 17 CFR 240.17a-4(b)(1); FINRA Rule 4511(b) |
| Communications received and sent relating to the firm's business as such | **3 years**, first 2 in an easily accessible place | Record creation | 17 CFR 240.17a-4(b)(4) |
| Account record information | **6 years** | Account closure, or replacement/update of the information | 17 CFR 240.17a-4(e)(5) |
| Reports a securities regulatory authority required the firm to create | **3 years** | Record creation | 17 CFR 240.17a-4(e)(6) |
| FINRA books and records with no period specified elsewhere | **6 years**; account-related records 6 years after the account is closed | Record creation / account closure | FINRA Rule 4511(b) |

**FINRA Rule 4511(b) does not displace a specified period.** It sets a six-year floor only
where no other FINRA rule or applicable Exchange Act rule specifies one. Communications
have a specified period (three years), so three years governs there; order memoranda are
the case where the FINRA residual is the longer of the two for a member firm. The
engine's `US` rows encode the longer applicable floor and name both instruments; a
non-FINRA-member firm should override the `ORDER_AUDIT_TRAIL` row to three years.

**Derivatives are a separate regime.** CFTC Regulation 1.31 requires regulatory records to
be kept **5 years**, readily accessible throughout the period for electronic records and
for the first two years for paper. Records of **oral** pre-trade communications need be
kept only **1 year**; swap records run for the life of the swap plus five years. If your
firm is a CFTC registrant, add CFTC rows rather than relying on the SEC rows.

**Format.** SEC Rule 17a-4(f) was amended on 12 October 2022, effective 3 January 2023, to
add an **audit-trail alternative** alongside WORM: an electronic recordkeeping system that
lets an original be recreated if it is modified or deleted. WORM is one permitted option,
not the only one.

## 2. United Kingdom — FCA

| Scope | Period | Instrument |
|---|---|---|
| Records kept under SYSC 9 in relation to **MiFID business** | **At least 5 years**, extendable to **7 years** where the competent authority requests it | SYSC 9.1.2R |
| Insurance distribution suitability/appropriateness records | At least 5 years | SYSC 9.1.2AR |
| Telephone conversations and electronic communications relating to in-scope transactions | 5 years, up to 7 on FCA request | SYSC 10A |
| Non-MiFID business | No fixed period — records "should be retained for as long as is relevant for the purposes for which they are made" | SYSC 9.1.5G (guidance) |

The extension to seven years is not automatic. Set it explicitly via the engine's
`extension_requested` argument when your competent authority has actually asked.

## 3. European Union — MiFID II

| Scope | Period | Instrument |
|---|---|---|
| Records of services, activities and transactions | **5 years**, and where the competent authority requests it, **up to 7 years** | MiFID II Art. 16(6); Del. Reg. (EU) 2017/565 Art. 72 |
| Recordings of telephone conversations and electronic communications | 5 years, up to 7 on competent-authority request | MiFID II Art. 16(7) |

The period runs from the **date of the record or communication**, not from settlement or
from the end of the client relationship. Several national competent authorities have
exercised the seven-year option; check yours. MAR, AML and national tax law can extend
retention independently of MiFID II.

## 4. Singapore — MAS

Holders of a capital markets services licence must keep books that sufficiently explain
their transactions and financial position (Securities and Futures Act 2001 s.102), and are
required to retain books and records for **at least 5 years** under the SFA and the
Securities and Futures (Licensing and Conduct of Business) Regulations.

> **Confidence caveat.** During the August 2026 review the primary text could not be
> retrieved: Singapore Statutes Online returned HTTP 403 to automated retrieval and the
> MAS document endpoints returned a service-unavailable page. The five-year figure is
> corroborated by secondary sources but has **not** been confirmed against the statute
> here. Confirm against SSO or MAS before this row drives a purge decision.

## 5. Australia — ASIC

| Scope | Period | Instrument |
|---|---|---|
| Company financial records | **7 years** after the transactions covered by the records are completed | Corporations Act 2001 s.286(2) |
| Records demonstrating compliance with the market integrity rules and Part 7.2 | At least 7 years after the record is made | ASIC market integrity rules |

## 6. India — SEBI and the Companies Act

| Scope | Period | Instrument |
|---|---|---|
| Stock broker books of account and records maintained under reg. 17 | **Minimum 5 years** | SEBI (Stock Brokers) Regulations 1992, reg. 18 |
| Exchange/member accounts and documents listed in the rule | 5 years | Securities Contracts (Regulation) Rules 1957, r.15 |
| Books of account of an Indian **company** | **8 financial years** immediately preceding the current financial year (longer if the Central Government so directs during an investigation) | Companies Act 2013 s.128(5) |

This is where version 1.0.0's "SEBI = 8 years" came from: the eight-year figure is real,
but it is a Companies Act obligation on books of account, not a SEBI recordkeeping period,
and it does not extend to every record a broker holds. The engine encodes 8 years for
`TRADE_AND_LEDGER` in India (the longer of the two where both apply to an incorporated
broker) and 5 years for the other classes.

## 7. What the engine deliberately does not model

- **Litigation and regulatory holds.** Modelled only as a boolean that forces
  `LEGAL_HOLD`; scope, custodians, and release are outside this skill.
- **Tax, AML/CFT, GDPR, and employment law.** All impose independent periods, some longer
  and some (GDPR storage limitation) pulling the other way.
- **Contractual retention** owed to clients, venues, or data vendors.
- **Format, immutability, and accessibility obligations** beyond the
  readily-accessible sub-period — see `data-retention-policy-and-storage-tiering` and
  `best-execution-record-keeping-global`.

## 8. Sources

All consulted August 2026.

- SEC Rule 17a-4 retention periods (six-year and three-year categories, account record
  information, first-two-years accessibility) — 17 CFR 240.17a-4,
  <https://www.law.cornell.edu/cfr/text/17/240.17a-4>; SEC, "Books and Records
  Requirements for Brokers and Dealers Under the Securities Exchange Act of 1934",
  <https://www.sec.gov/rules-regulations/2001/10/books-records-requirements-brokers-dealers-under-securities-exchange-act-1934>;
  FINRA, "SEA Rule 17a-4 and Related Interpretations",
  <https://www.finra.org/rules-guidance/guidance/interpretations-financial-operational-rules/sea-rule-17a-4-and-related-interpretations>
- SEC Rule 17a-4(f) audit-trail alternative (adopted 12 October 2022, effective 3 January
  2023) — SEC, "Amendments to Electronic Recordkeeping Requirements for Broker-Dealers",
  <https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers>
- FINRA Rule 4511(b) six-year residual — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511>
- CFTC Regulation 1.31 five-year period, accessibility, one-year oral communications —
  eCFR 17 CFR 1.31, <https://www.ecfr.gov/current/title-17/chapter-I/part-1/subject-group-ECFR26e2c365a191fa7/section-1.31>;
  K&L Gates, "CFTC Amends Recordkeeping Requirements",
  <https://www.klgates.com/CFTC-Amends-Recordkeeping-Requirements-06-02-2017>
- FCA SYSC 9.1.2R five years for MiFID business, SYSC 9.1.5G for non-MiFID business —
  FCA Handbook, <https://handbook.fca.org.uk/handbook/sysc9/sysc9s1>
- MiFID II Art. 16(6)/(7) five years extendable to seven — Del. Reg. (EU) 2017/565 Art. 72;
  summarised at <https://www.smarsh.com/regulations/markets-financial-instruments-directive-MIFIDII>.
  *Secondary source; the EUR-Lex primary text was not retrieved during this review.*
- Singapore SFA 2001 s.102 and five-year retention — MAS, Securities and Futures
  (Licensing and Conduct of Business) Regulations,
  <https://www.mas.gov.sg/regulation/regulations/securities-and-futures-licensing-and-conduct-of-business-regulations>.
  *See the confidence caveat in section 4 — primary text not retrieved.*
- Corporations Act 2001 s.286 seven-year financial records —
  <https://www.austlii.edu.au/cgi-bin/viewdoc/au/legis/cth/consol_act/ca2001172/s286.html>;
  ASIC company record keeping,
  <https://www.asic.gov.au/for-business-and-companies/companies/company-building-blocks/company-record-keeping>
- ASIC market integrity rules seven-year record keeping — ASIC RG 265, "Guidance on ASIC
  market integrity rules for participants",
  <https://download.asic.gov.au/media/vemgspga/rg265-published-2-august-2022-20251218.pdf>
- SEBI (Stock Brokers) Regulations 1992 reg. 18 five-year preservation —
  <https://www.sebi.gov.in/legal/regulations/aug-2023/securities-and-exchange-board-of-india-stock-brokers-regulations-1992-last-amended-on-august-18-2023-_76325.html>;
  Securities Contracts (Regulation) Rules 1957 r.15,
  <https://www.sebi.gov.in/sebi_data/attachdocs/1399433501593.pdf>
- Companies Act 2013 s.128(5) eight financial years —
  <https://indiankanoon.org/doc/30979979/>

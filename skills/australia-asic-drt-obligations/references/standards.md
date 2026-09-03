# Standards for ASIC Derivative Transaction Rules (Reporting) 2024

Regulatory basis: **ASIC Derivative Transaction Rules (Reporting) 2024**, made 19 December 2022
under s 901A of the Corporations Act 2001, Federal Register of Legislation
[F2022L01706](https://www.legislation.gov.au/F2022L01706/latest), as amended by the
ASIC Derivative Transaction Rules (Reporting) Amendment Instrument 2024/1. The 2024 Rules
commenced **21 October 2024** and replaced the 2022 Rules. ASIC's landing page for the regime is
[Derivative transaction reporting](https://www.asic.gov.au/regulatory-resources/markets/otc-derivatives/derivative-transaction-reporting).

Rule text quoted below was verified against ASIC's published comparison documents:
[2024 Rules vs 2022 Rules](https://download.asic.gov.au/media/ixlomfug/asic-derivative-transaction-rules-reporting-comparison-2024-rules-vs-2022-rules.pdf)
and
[Amendment 2024/1 vs 2024 Rules](https://download.asic.gov.au/media/xpaldc0z/comparison-2024-rules-amendment-2024_1-vs-2024-rules.pdf).

## Currency

| Item | Status at last verification (September 2026) |
|---|---|
| ASIC Derivative Transaction Rules (Reporting) 2024 | In force since 21 October 2024; F2022L01706 as amended by Amendment Instrument 2024/1. Rule 2.2.3 is unchanged by that amendment. |
| Item numbering in Table S1.1(1) | The 2024/1 amendment renumbered parts of the table (Counterparty 2 identifier type indicator is **Item 8**, and Item 7a was inserted). Item 1 (UTI), Item 2 (UPI) and Item 92 (Package identifier) are unchanged. Re-verify item numbers against the current compilation before citing them in an audit response. |
| ISO 4914:2021 | Current. Clause 4 (UPI code structure) verified against the ISO preview of the published standard. Annex C (check character) is normative but not publicly available. |
| ISO 23897:2020 | Current. Format follows the CPMI-IOSCO *Harmonisation of the Unique Transaction Identifier* Technical Guidance (BIS/CPMI [d158](https://www.bis.org/cpmi/publ/d158.pdf), February 2017). |

## Rule map

| ASIC provision | Requirement (quoted where material) | Implementation |
|---|---|---|
| **Rule 2.2.3(1)** — Timing (generally, T+2) | A Reporting Entity "must report the information or change by no later than the end of the second Business Day after the day on which the Reportable Transaction or change occurs." | `_add_business_days(trade_date, 2, holidays)`; `is_late_submission = reporting_date > deadline`. Reporting exactly on the deadline is compliant. |
| **Rule 2.2.3(2)** — Repository unavailable | "If the Licensed Repository or Prescribed Repository … is not available to accept the report … by the time required under subrule (1), the Reporting Entity must report the information or changes as soon as practicable after [it] becomes available." No substitute deadline is fixed. | `repository_unavailable_at_deadline=True` sets `DrtComplianceRecord.repository_outage_relief_may_apply`. The engine does **not** invent a replacement deadline or clear the late flag — "as soon as practicable" is a factual judgement for compliance. |
| **Rule 2.2.3(3)** — T+4, with an FX carve-out | "A Reportable Transaction, **other than a foreign exchange contract that is part of a foreign exchange swap derivative transaction**, for which a value for Item 92 of Table S1.1(1) is required to be reported, must be reported by no later than the end of the fourth Business Day after the day on which the Reportable Transaction occurs." | Offset is 4 business days only when `requires_package_identifier and not is_fx_swap_leg`; otherwise 2. |
| **Rule 1.2.3** — "Business Day" | "a day that is not a Saturday, a Sunday, or a public holiday or bank holiday in the **Relevant Jurisdiction**." | Weekends are always skipped; `holidays` is the caller-supplied Relevant-Jurisdiction holiday set. Omitting it skips weekends only and overstates the deadline. |
| **Rule 1.2.3** — "Relevant Jurisdiction" | Australia where the Reportable Transaction "was booked to the profit or loss account of a branch of the Reporting Entity located in this jurisdiction or was entered into by the Reporting Entity in this jurisdiction"; otherwise the jurisdiction in which it was booked or entered into. | Documented as the caller's responsibility. The deadline calendar is therefore not unconditionally Sydney. |
| **Rule 1.2.1** — References to time | "a reference to time is to Australian Eastern Standard Time (AEST) or Australian Eastern Daylight Time (AEDT), as applicable, in Sydney, Australia." | Applies to *times*, not to the Business Day calendar. Rule 2.2.9(4) separately uses "the second business day **in Sydney**" for UTI generating-entity tie-breaks — a different test from Rule 2.2.3. |
| **Rule 2.2.4(2)** — Format | Report in machine-readable form, in accordance with an ISO 20022 message definition covering the Part S1.3 Derivative Transaction Information, using that definition's XML tags. | Out of scope: this module gates the payload before serialisation. |
| **Rule 2.2.9** — UTI | The UTI generating entity is determined by Table 2 in Rule 2.2.9(3); subrule (6) covers the case where the UTI is not received in time and the Reporting Entity must generate one. | Out of scope: the engine validates the UTI's shape, not who minted it. |
| **Table S1.1(1) Item 1** — Unique transaction identifier | "As specified in ISO 23897." For a transaction identifier that is not a UTI as referred to in Rule 2.2.9, "no format is specified". | `_is_valid_uti`: 20–52 uppercase alphanumeric characters. The engine assumes the identifier is a UTI. |
| **Table S1.1(1) Item 2** — Unique product identifier (UPI) | "As specified in ISO 4914." "This data element is **not required in a report about the termination** of an OTC Derivative." | `_is_valid_upi`; skipped when `is_termination_report=True`, but a supplied value is still validated. |
| **Table S1.1(1) Item 7** — Counterparty 2 | "The LEI or another identifier, determined in accordance with subrule S1.3.1(2)". Format: "For an LEI, as specified in ISO 17442. For any other kind of identifier, an alphanumeric code of not more than 72 characters." Allowable values include a Client Code and "ANON for an anonymity identifier". | LEI path uses `_is_valid_lei`; non-LEI path accepts a non-empty ASCII alphanumeric code of ≤ 72 characters. |
| **Table S1.1(1) Item 8** — Counterparty 2 identifier type indicator | "True — if the type of identifier is an LEI; or False — if the type of identifier is not an LEI." | `OtcDerivativeTrade.counterparty_identifier_is_lei`. |
| **Subrule S1.3.1(2)** — Non-LEI identifiers | Where the entity is eligible for an LEI it must be reported once available; where the entity "is a natural person not eligible for the issue of an LEI per the ROC Statement", the Client Code is reported. | The engine models the outcome (LEI vs non-LEI) but not the eligibility determination, which is a policy decision for the reporting entity. |
| **Table S1.1(1) Item 92** — Package identifier | "The identifier (determined by the Reporting Entity) in order to connect two or more Reportable Transactions that are reported separately." Format: "An alphanumeric code of not more than 100 characters." Required where the transactions (a) are entered into together as one economic arrangement, (b) cannot be reported as a single report, or (c) are "the reporting of a foreign exchange swap derivative transaction … reported as two foreign exchange contracts with different Expiration dates". | `package_identifier` is validated when present and required when `requires_package_identifier=True`. Case (c) is the same case Rule 2.2.3(3) excludes from T+4 — see `is_fx_swap_leg`. |
| **Table S1.1(1) Item 103** — Reporting timestamp | "YYYY-MM-DDThh:mm:ssZ date and time format in **UTC** in accordance with ISO 8601." | The engine takes a local `date`, and rejects `datetime` inputs so a UTC timestamp is never silently read as a local day. |

## Identifier formats

| Standard | Format | Source |
|---|---|---|
| **LEI** — ISO 17442 | 20 characters: 18 uppercase alphanumeric characters identifying the entity plus **2 numeric check digits**. Checksum: ISO/IEC 7064 MOD 97-10 — the base-36 numeric representation must satisfy `value % 97 == 1`. | Rule 1.2.3: "LEI means a legal entity identifier code in the format and structure specified in ISO 17442." |
| **UTI** — ISO 23897 | Up to 52 uppercase alphanumeric characters, no separators. CPMI-IOSCO construction `18!c2!n32c`: the generating entity's 20-character LEI (18 alphanumeric + 2 numeric check digits) followed by up to 32 transaction-specific characters — a 20–52 character range. | Rule 1.2.3: "UTI means a unique transaction identifier in the form specified in ISO 23897." CPMI-IOSCO d158. |
| **UPI** — ISO 4914 | 12 characters: "the two-character prefix 'QZ'; nine alphanumeric characters [upper-case A to Z and 0 to 9 only, excluding the vowel characters (A, E, I, O, U) and the character Y] without separators or special characters; one alphanumeric check character [same alphabet], shall be calculated using the method specified in Annex C." | ISO 4914:2021 clause 4 (quoted verbatim). Rule 1.2.3: "UPI means a product identifier code in the format and structure specified in ISO 4914." |

UPIs are issued by the **ANNA Derivatives Service Bureau**, which also operates the UPI
reference data library ([anna-dsb.com/upi-](https://www.anna-dsb.com/upi-/)).

## Deliberate implementation limits

- **The ISO 4914 check character is not verified.** Annex C specifies a MOD 31,30 scheme per
  ISO/IEC 7064:2003 with a custom character-to-value mapping. Annex C is normative but is not
  publicly available, and an incorrectly reconstructed algorithm would reject valid UPIs — a
  worse failure than not checking. Confirm the UPI against the DSB reference data library.
- **The UTI's embedded LEI prefix is not checksum-verified.** A Reporting Entity may
  legitimately report a UTI generated by a foreign counterparty under that jurisdiction's rules
  (Rule 2.2.9(3) Table 2 Items 6–8), or a legacy transaction identifier for which Item 1 states
  "no format is specified".
- **LEI currency is not checked.** Items 5, 6 and 23 require the *current* LEI. A structurally
  valid but lapsed LEI passes this gate; validate against GLEIF.
- **Only four data elements are checked.** Table S1.1(1) carries around 100 elements, plus
  Table S1.1(2) (valuation) and Table S1.1(3) (collateral).

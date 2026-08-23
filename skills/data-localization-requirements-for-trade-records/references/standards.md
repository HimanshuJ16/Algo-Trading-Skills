# Standards for Data Localization Requirements for Trade Records

Each row states the *scope* of the mandate, not a generalized rule. Several
regimes commonly assumed to require localization do not.

| Regime | Instrument | What it actually requires |
|---|---|---|
| China — CII operators | Cybersecurity Law Art. 37 (2016) | Personal information and important data generated during CII operation must be stored in mainland China. Export where "truly necessary" requires a CAC security assessment. |
| China — personal information handlers | PIPL Art. 40; PIPL Art. 38 | Localization for CIIOs and handlers above CAC volume thresholds. Otherwise export requires a CAC security assessment, CAC standard contract, or certification. The CAC *Provisions on Promoting and Regulating Cross-Border Data Flows* (22 Mar 2024) exempt some low-volume, non-sensitive, non-CIIO exports; thresholds are cumulative from 1 January each year. |
| India — payment data | RBI circular DPSS.CO.OD No.2785/06.08.005/2017-2018 (6 Apr 2018) | Payment system data must be stored **only in India**. Foreign processing is permitted, but the data must be brought back to India and deleted abroad — a foreign resting copy is not permitted. Scope is payment system data, not securities trade records generally. |
| India — securities market | SEBI CSCRF, Data Security standard PR.DS.S2 (Aug 2024) | Data-localization provisions **kept in abeyance** by SEBI circular dated 31 Dec 2024 pending further consultation. Not currently an in-force mandate. |
| EU | GDPR Chapter V (Arts. 44–49) | **No localization requirement.** Third-country transfer requires a Chapter V mechanism: Art. 45 adequacy (incl. the EU–US Data Privacy Framework, adopted 10 Jul 2023, upheld by the General Court on 3 Sep 2025), Art. 46 SCCs, or an Art. 49 derogation. |
| EU — market conduct | MiFID II Art. 16(6); RTS 6 | Records of orders and communications retained 5 years (extendable to 7 at the competent authority's request) and made available to the authority. No storage-location mandate; cloud storage is permitted with responsibility retained by the firm. |
| US | SEC Rule 17a-4(a), (b) | Retention: 6 years for 17a-4(a) records (first two years in an easily accessible place); 3 years for 17a-4(b) records. **No residency mandate.** |
| US — electronic systems | SEC Rule 17a-4(f), as amended 2022 (effective 3 Jan 2023) | An electronic recordkeeping system must meet **either** the WORM (non-rewriteable, non-erasable) condition **or** the audit-trail alternative permitting recreation of a modified or deleted record. WORM was retained as an option, not mandated. |
| US — production | SEC Rule 17a-4(j) | Records must be furnished promptly to Commission representatives as legible, true, complete and current copies — the practical constraint on storing US records in export-controlled jurisdictions. |
| Russia | Federal Law No. 242-FZ (in force 1 Sep 2015) | Collection, storage and processing of Russian citizens' personal data must use databases located in Russia; server location must be notified to Roskomnadzor. Not encoded by default in this engine — `RU` resolves to `REVIEW_REQUIRED`. |

## Sources

- SEC, *Electronic Recordkeeping Requirements for Broker-Dealers, Security-Based Swap Dealers, and Major Security-Based Swap Participants*, Release No. 34-96034 (Federal Register, 3 Nov 2022) — https://www.federalregister.gov/documents/2022/11/03/2022-22670/electronic-recordkeeping-requirements-for-broker-dealers-security-based-swap-dealers-and-major
- 17 CFR § 240.17a-4 — https://www.ecfr.gov/current/title-17/chapter-II/part-240/section-240.17a-4
- FINRA, *SEA Rule 17a-4 and Related Interpretations* — https://www.finra.org/rules-guidance/guidance/interpretations-financial-operational-rules/sea-rule-17a-4-and-related-interpretations
- RBI, *Storage of Payment System Data* FAQs — https://www.rbi.org.in/commonman/english/scripts/FAQs.aspx?Id=2995
- SEBI, *Cybersecurity and Cyber Resilience Framework (CSCRF) for SEBI Regulated Entities* and subsequent clarifications — https://www.sebi.gov.in/legal/circulars/aug-2025/technical-clarifications-to-cybersecurity-and-cyber-resilience-framework-cscrf-for-sebi-regulated-entities-res-_96329.html
- European Commission, *Questions & Answers: EU–US Data Privacy Framework* (10 Jul 2023) — https://ec.europa.eu/commission/presscorner/detail/en/qanda_23_3752
- CJEU General Court, judgment of 3 Sep 2025 upholding the DPF adequacy decision (reported) — https://eucrim.eu/news/general-court-confirms-adequacy-of-us-data-protection/
- Greenberg Traurig, *China Relaxes Requirements for Cross-Border Data Transfers* (CAC Provisions of 22 Mar 2024) — https://www.gtlaw.com/en/insights/2024/3/china-relaxes-requirements-for-cross-border-data-transfers
- Morgan Lewis, *Data Localization Laws: Russian Federation* — https://www.morganlewis.com/-/media/files/publication/outside-publication/article/2021/data-localization-laws-russian-federation.pdf

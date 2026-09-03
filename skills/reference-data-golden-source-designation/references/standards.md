# Standards & Sources for Reference Data Golden Source Designation

## What is actually mandated, and on whom

Unsourced "MUST" rules in an "Engineering Standard" table are engineering opinion, not
standards, however reasonable they sound. This section separates what a named authority
actually requires from what this skill recommends.

### BCBS 239 — an aspiration, for banks, about risk data

| Claim | Source | Scope and force |
|---|---|---|
| "A bank should **strive towards** a single authoritative source for risk data per each type of risk." | [BCBS, *Principles for effective risk data aggregation and risk reporting*](https://www.bis.org/publ/bcbs239.pdf), January 2013, Principle 3 (Accuracy and Integrity), para 36(d) | Read the verb. This is an aspiration ("strive towards"), not a hard rule, and it concerns **risk data per type of risk** — not instrument reference data per field. Citing it as "regulation requires a golden source for every reference data field" overstates it in two directions at once. |
| "Risk data should be reconciled with bank's sources, including accounting data where appropriate, to ensure that the risk data is accurate." | BCBS 239, para 36(c) | Footnote 17 defines reconciliation as "the process of comparing items or outcomes **and explaining the differences**" — comparison alone is not reconciliation. This is why the engine records `overridden_vendors` and `skipped_vendors` rather than only the winner. |
| "As a precondition, a bank should have a 'dictionary' of the concepts used, such that data is defined consistently across an organisation." | BCBS 239, para 37 | A priority rule keyed on `tick_size` is meaningless until the organisation agrees what `tick_size` means. Define the field before designating a source for it. |
| Banks should use "single identifiers and/or unified naming conventions for data including legal entities, counterparties, customers and accounts." | BCBS 239, para 33 (Principle 2) | Supports identifier normalisation, which is the sibling skill `reference-data-symbol-mapping-across-vendors`, not this one. |

**Applicability.** BCBS 239 binds banks identified as **G-SIBs** by the FSB (para 14 —
compliance by January 2016, or within three years of a later designation); para 15 only
"strongly suggests" national supervisors extend it to **D-SIBs**, and para 13 leaves
wider application to supervisory discretion. Para 16 limits the Principles to "a bank's
**risk management data**". A proprietary trading firm, a fund, or a non-systemic broker
is **not** subject to BCBS 239, and an instrument master is not automatically risk
management data. Do not cite BCBS 239 as authority for a control in a firm it does not
apply to.

### MiFIR RTS 23 — hard law, for trading venues and systematic internalisers

[Commission Delegated Regulation (EU) 2017/585](https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160714-rts-23_en.pdf)
of 14 July 2016, supplementing Regulation (EU) No 600/2014 (MiFIR). Applies from the
date in MiFIR Article 55 second paragraph (3 January 2018).

| Article | Requirement | Relevance here |
|---|---|---|
| Art. 1 | Trading venues and systematic internalisers "shall provide competent authorities with all details of financial instrument reference data ... referred to in Table 3 of the Annex", in a common XML template following the ISO 20022 methodology. | The reference data set subject to this obligation is enumerated, not open-ended. |
| Art. 3(1) | "Prior to the commencement of trading in a financial instrument ... the trading venue or systematic internaliser concerned shall **obtain the ISO 6166** International Securities Identifying Number ('ISIN') code for the financial instrument." | A **regulator-designated golden source for a specific field**. The authority for an ISIN is the ISO 6166 issuance chain, not whichever vendor your priority rule happens to rank first. |
| Art. 3(2) | LEI codes must comply with **ISO 17442:2012**, "pertain to the issuer concerned", and be "listed in the Global Legal Entity Identifier database maintained by the Central Operating Unit appointed by the ... Legal Entity Identifier Regulatory Oversight Committee". | Same pattern: the designated source for `lei` is GLEIF, and membership of that database is checkable, not a matter of vendor ranking. |
| Art. 5 | "Competent authorities shall conduct quality assessments regarding the content and accuracy of the reference data received ... on at least a **quarterly** basis." | The supervisor re-checks. A designation that was right at onboarding and never revisited will be tested by someone else. |
| Art. 6(1) | Venues and SIs "shall ensure that they provide **complete and accurate** reference data to their competent authorities". | Completeness *and* accuracy — an empty field is a failure mode of the same kind as a wrong one, which is why this engine reports `fields_without_data` rather than quietly omitting the key. |
| Art. 6(2) | They "shall put methods and arrangements in place that enable them to **identify incomplete or inaccurate reference data previously submitted**", and on detection "promptly notify" the competent authority and "transmit ... complete and correct relevant reference data **without undue delay**". | The strongest driver of this engine's design. You cannot identify and correct what you previously submitted unless you retained *which vendor supplied it, under which rule, and what was rejected*. A pipeline that stores only `golden_record` cannot satisfy Art. 6(2). |
| Art. 7(6) | "ESMA shall publish the reference data in an electronic, downloadable and machine readable form." | This is FIRDS. For EU instruments in scope, a regulator-published reference data set exists and can itself be designated as a source. |

**Applicability.** RTS 23 obligations fall on **trading venues and systematic
internalisers**, and (in Arts. 4, 5, 7) on competent authorities and ESMA. An investment
firm that is not an SI has reference data obligations through other instruments — RTS 22
transaction reporting under
[Delegated Regulation (EU) 2017/590](https://eur-lex.europa.eu/eli/reg_del/2017/590/oj)
requires accurate instrument and entity identification in reports — but is **not** the
addressee of RTS 23. Post-Brexit, the UK operates its own onshored version;
`references/workflows.md` and this table describe the EU instrument.

### Identifier registration authorities

These are the concrete, checkable "golden sources" for the fields most often fought over.
Each is an authority of record, not a vendor.

| Field | Standard | Authority of record |
|---|---|---|
| ISIN | ISO 6166 (revised edition published February 2021) | [ANNA](https://anna-web.org/identifiers/) is the ISO 6166 Registration Authority; ISINs are issued by the National Numbering Agency of the relevant jurisdiction. |
| MIC | ISO 10383 (MIC Data Set Structure and Format Release 2.0) | [SWIFT (S.W.I.F.T. SC) is the ISO 10383 Registration Authority](https://www.iso20022.org/market-identifier-codes); the MIC list is free to obtain. |
| LEI | ISO 17442 | GLEIF — named in RTS 23 Art. 3(2) as "the Global Legal Entity Identifier database maintained by the Central Operating Unit appointed by the ... Regulatory Oversight Committee". |
| CFI | ISO 10962 | ANNA, alongside ISIN issuance. |

**A vendor is a distribution channel, not an authority.** Bloomberg, LSEG/Refinitiv and
FactSet all *redistribute* ISINs, MICs and LEIs. Ranking two redistributors against each
other for a field that has a registration authority is solving the wrong problem: the
right designation is the authority, with vendors ranked only as fallbacks for latency or
coverage.

## Effective dates are not covered by this engine

The [ISO 10383 Registration Authority publishes the MIC list on the second Monday of each
month, with modifications taking effect on the fourth Monday](https://www.iso20022.org/market-identifier-codes).
So a correct, current, freshly-published MIC record routinely describes a change that is
**not yet in force**.

This engine gates records by *age* (`max_staleness`) and has no concept of a future
effective date. A record can therefore be fresh, authoritative, and still wrong to apply
today. Where a field changes on a scheduled date — MIC migrations, symbol changes, lot
size revisions — pair this skill with `reference-data-change-notification-pipeline` and
carry the effective date in your own schema. Do not read a passing staleness gate as
"safe to apply now".

## What this skill recommends, on its own authority

These are engineering positions taken by this skill. They are not published requirements
and are not attributed to any of the sources above.

| Position | Reasoning |
|---|---|
| A field with no priority rule should yield **no value**, not an arbitrary one. | An arbitrary pick is indistinguishable downstream from a designated one, and in v1.0.0 of this module it also varied with the caller's argument order — the same vendor data produced different instrument masters in different services. Silence is auditable; a plausible guess is not. |
| Blank and sentinel values must be gated **before** ranking, not after. | Vendors encode absence as `""`, whitespace, `"N/A"` and `"NULL"` at least as often as SQL NULL. An `is not None` test lets a top-ranked vendor's empty string beat a lower-ranked vendor's real value. |
| Two snapshots from the same vendor must be rejected, not merged. | Which snapshot is authoritative is a question about your ingestion, and the engine has no basis to answer it. Last-wins silently destroys the other. |
| Every resolution should retain the rejected alternatives. | Directly serves RTS 23 Art. 6(2) for entities in its scope, and serves ordinary incident forensics for everyone else. |
| Staleness gating requires an explicitly supplied evaluation instant. | An engine that reads the clock cannot be replayed, and a reference data decision you cannot replay is one you cannot defend. |

## Sources

- [BCBS, *Principles for effective risk data aggregation and risk reporting* (BCBS 239), January 2013](https://www.bis.org/publ/bcbs239.pdf) — paras 14–18, 33, 36, 37.
- [Commission Delegated Regulation (EU) 2017/585 (RTS 23), 14 July 2016](https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160714-rts-23_en.pdf) — Articles 1–8.
- [Commission Delegated Regulation (EU) 2017/590 (RTS 22), transaction reporting](https://eur-lex.europa.eu/eli/reg_del/2017/590/oj).
- [ANNA — Identifiers (ISO 6166 ISIN registration authority)](https://anna-web.org/identifiers/).
- [ISO 20022 — Market Identifier Codes (ISO 10383 registration authority and publication schedule)](https://www.iso20022.org/market-identifier-codes).

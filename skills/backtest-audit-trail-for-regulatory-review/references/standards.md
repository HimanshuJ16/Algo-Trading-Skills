# Standards — backtest-audit-trail-for-regulatory-review

## Scope caution

The regulations below govern **transactional and communications records of
regulated entities**. A backtest audit manifest is a research artifact. It is
good governance practice and it is frequently requested in due diligence, but
none of these rules enumerate "backtest manifest" as a required record. Treat
this section as *context for where such records fit*, not as authority that a
specific rule mandates them. Confirm applicability with compliance counsel for
your jurisdiction and registration status.

## SEC Rule 17a-4 — electronic recordkeeping (US)

**Applies to**: members of a national securities exchange, and brokers or
dealers registered under section 15 of the Exchange Act. An unregistered fund
or research group is **not** subject to 17a-4.

**Retention is tiered — there is no single "17a-4 retention period":**

| Paragraph | Period | Records |
|---|---|---|
| 17a-4(a) | Not less than **6 years**, first two in an easily accessible place | Records required by 17a-3(a)(1)–(3), (5), (21), (22) — blotters, ledgers, customer account records |
| 17a-4(b) | Not less than **3 years**, first two easily accessible | Records required by 17a-3(a)(4), (6)–(11), plus communications, financial statements, agreements, trial balances |

Rule 17a-3 enumerates transactional records — blotters, ledgers, order tickets,
customer account records, net capital computations. A backtest manifest is not
among them, so asserting a six-year obligation for one is unsupported. If such
a record is retained under 17a-4 at all, its tier follows from how the firm
classifies it (commonly as a communication or supporting record under (b)).

**Storage format — 17a-4(f)(2)(i)**, as amended 12 Oct 2022 (effective
3 Jan 2023, compliance date 3 May 2023). Two permitted alternatives:

- **(A) Audit-trail alternative** — preserve the record "in a manner that
  maintains a complete time-stamped audit trail that includes: (1) All
  modifications to and deletions of the record or any part thereof; (2) The
  date and time of actions…"
- **(B) WORM** — preserve "exclusively in a non-rewriteable, non-erasable
  format."

The 2022 amendments **retained WORM as an option and added** the audit-trail
alternative; WORM is no longer the only path.

> Implication for this skill: durability and tamper-evidence are properties of
> the **storage system**, not of a hash embedded in the record. The manifest
> supports an audit trail; it does not constitute one.

## SEC Rule 613 — Consolidated Audit Trail (US)

**Rule 613 is an SEC rule**, adopted 18 July 2012 under Reg NMS, requiring the
SROs to create and file the CAT NMS Plan. It is commonly but incorrectly cited
as "FINRA Rule 613". FINRA's industry-member compliance rules implementing the
CAT NMS Plan are the **FINRA Rule 6800 Series**.

**Scope**: order and execution events in NMS securities, reported by exchange
and FINRA members. It governs live order lifecycle reporting — **not** backtest
or research records. It is listed here only to prevent the common mistake of
citing CAT as authority for research recordkeeping.

## MiFID II RTS 25 — business clock synchronisation (EU)

Commission Delegated Regulation (EU) 2017/574.

**Scope (Article 1)**: operators of trading venues and their members or
participants must synchronise "the business clocks they use to record the date
and time of any **reportable event**" with UTC.

**Accuracy for members using a high frequency algorithmic trading technique**
(Article 3, Annex Table 2): maximum divergence from UTC **100 microseconds**,
timestamp granularity **1 microsecond or better**.

> RTS 25 governs the clocks used to timestamp **reportable trading events**.
> It does not impose a 100µs
> accuracy requirement on the timestamp of a backtest manifest, and a research
> artifact is not a reportable event. This module records UTC timestamps at
> microsecond granularity because precise lineage is useful, **not** because
> RTS 25 requires it of research records.

## Cryptographic properties actually provided

| Property | Provided? | Mechanism |
|---|---|---|
| Corruption detection | Yes | `content_digest_sha256` (unkeyed SHA256) |
| Tamper detection by third parties | Yes | `manifest_hmac_sha256` (HMAC-SHA256, RFC 2104) |
| Non-repudiation against the issuer | **No** | Symmetric key — the signer can forge |
| Proof of existence at a point in time | **No** | Requires an RFC 3161 timestamp authority |
| Immutable retention | **No** | Requires WORM or 17a-4(f)(2)(i)(A) storage |

An unkeyed digest embedded in the record it describes provides **none** of the
tamper-evidence properties, because recomputing it after modification is
trivial. That was the defect corrected in version 2.0.0.

## Known limitations

- HMAC keys are symmetric; the issuing firm can regenerate any manifest.
  Independent assurance requires an external trust anchor.
- Metrics are serialised as JSON numbers. Float formatting can differ between
  language runtimes, so byte-level canonical comparison across toolchains is
  not guaranteed. Encode metrics as decimal strings where that matters.
- The engine does not itself invoke git or verify a clean working tree; the
  caller supplies the commit SHA and is responsible for its accuracy.

## Sources

| Claim | Source |
|---|---|
| 17a-4 applicability, (a) 6-year and (b) 3-year tiers, (f)(2)(i) WORM and audit-trail alternative | 17 CFR § 240.17a-4 — https://www.law.cornell.edu/cfr/text/17/240.17a-4 |
| 2022 amendments: adopted 12 Oct 2022, effective 3 Jan 2023, compliance 3 May 2023; WORM retained, audit-trail alternative added | SEC, *Amendments to Electronic Recordkeeping Requirements for Broker-Dealers* — https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers |
| 17a-3 enumerated records are transactional (blotters, ledgers, order tickets) | FINRA, *Books and Records Requirements Checklist for Broker-Dealers* — https://www.finra.org/sites/default/files/2022-02/Books-and-Records-Requirements-Checklist-for-Broker-Dealers.pdf |
| Rule 613 is an SEC rule adopted 18 July 2012 | SEC, *Rule 613 (Consolidated Audit Trail)* — https://www.sec.gov/about/divisions-offices/division-trading-markets/rule-613-consolidated-audit-trail |
| FINRA implements CAT via the Rule 6800 Series | FINRA Rule 6800 Series — https://www.finra.org/rules-guidance/rulebooks/finra-rules/6800 |
| RTS 25 scope (reportable events, venues and members) and 100µs / 1µs HFT tier | Commission Delegated Regulation (EU) 2017/574, Art. 1 and 3, Annex Table 2 — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0574 |
| HMAC construction | RFC 2104, *HMAC: Keyed-Hashing for Message Authentication* — https://www.rfc-editor.org/rfc/rfc2104 |
| JSON does not permit NaN/Infinity | RFC 8259 §6 — https://www.rfc-editor.org/rfc/rfc8259 |

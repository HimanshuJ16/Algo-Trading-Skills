# Best Execution & Record-Keeping Standards

**Currency of this file: verified August 2026.** Best-execution and recordkeeping rules
have changed materially since MiFID II went live — several obligations widely cited in
older material have since been deleted, suspended, or withdrawn. Re-verify before relying
on any row below, and confirm which rules bind *your* firm: most of the reporting
obligations here fall on execution venues, market centres, and broker-dealers, not on a
buy-side trading firm.

## 1. What best execution actually requires

Best execution is a **process obligation across multiple factors**, not a numeric
threshold. No regulator in any regime below prescribes a maximum slippage figure. The
engine's `slippage_tolerance` is a firm-chosen risk parameter and nothing more.

| Regime | Standard | Factors |
|---|---|---|
| MiFID II Article 27(1) | Take "all **sufficient** steps" to obtain the best possible result for clients (raised from "all reasonable steps" under MiFID I) | Price, costs, speed, likelihood of execution and settlement, size, nature, or any other relevant consideration |
| FINRA Rule 5310 | Use "reasonable diligence" to ascertain the best market so the resultant price is as favourable as possible under prevailing market conditions | Character of the market, size and type of transaction, number of markets checked, accessibility of quotations, terms of the order |

**FINRA's regular and rigorous review.** A member routing customer orders on an
automated, non-discretionary basis, or internalising order flow, must conduct regular and
rigorous reviews of execution quality where it does not review order by order. Those
reviews must be performed **at minimum quarterly**, on a **security-by-security,
type-of-order** basis (limit, market, market-on-open). Per-order screening does not
discharge this.

## 2. Status of the reporting obligations — read before citing any of them

| Obligation | Binds | Current status |
|---|---|---|
| **RTS 28** (Delegated Reg (EU) 2017/576) — annual top-five-venue and execution-quality report | Investment firms | **Deleted.** Directive (EU) 2024/790 removes Article 27(6) of MiFID II. Published in the OJ 8 March 2024, in force 29 March 2024, transposition deadline 29 September 2025. ESMA told NCAs not to prioritise supervisory action from 13 February 2024. |
| **RTS 27** (Delegated Reg (EU) 2017/575) — quarterly execution-quality reports | Execution venues, not investment firms | **Suspended, then deprioritised.** Directive (EU) 2021/338 (Capital Markets Recovery Package) suspended it to 28 February 2023; ESMA then stated NCAs should not prioritise supervisory action from 1 March 2023 pending the MiFID review amendment. Do not treat as a live periodic obligation without checking your NCA's current position. |
| **SEC Regulation Best Execution** | Would have bound brokers, dealers, government securities and municipal securities dealers | **Withdrawn, never adopted.** Proposed 14 December 2022; the SEC formally withdrew it on 12 June 2025 (effective 17 June 2025) stating it does not intend to issue final rules. FINRA Rule 5310 remains the operative US best-execution rule for FINRA members. |
| **SEC Rule 605** — order execution quality reports | Market centres; since the 2024 amendments also broker-dealers introducing or carrying **at least 100,000 customer accounts**, and single-dealer platforms (separate report) | **In force, recently expanded.** Amendments adopted 6 March 2024. Compliance date extended from 14 December 2025 to **1 August 2026** (Release 34-104147). Limited exemptions have been granted by SEC order — check current exemptive relief. |
| **SEC Rule 606** — order routing disclosure | Broker-dealers routing customer orders | **In force.** Quarterly public reports on non-directed customer orders in NMS stocks and listed options; under Rule 606(b)(3), a report to a customer *on request* covering the customer's not-held orders for the prior six months. A buy-side firm is the requester here, not the filer. |

## 3. Timestamp granularity — MiFID II RTS 25

Commission Delegated Regulation (EU) 2017/574. Business clocks must be synchronised to
UTC as issued and maintained by the timing centres in the BIPM Annual Report on Time
Activities. **There is no single universal granularity, and RTS 25 nowhere requires
nanoseconds.**

Operators of trading venues — driven by gateway-to-gateway latency:

| Gateway-to-gateway latency | Max divergence from UTC | Timestamp granularity |
|---|---|---|
| > 1 millisecond | 1 millisecond | 1 millisecond or better |
| ≤ 1 millisecond | 100 microseconds | 1 microsecond or better |

Members/participants of trading venues — driven by activity type:

| Type of trading activity | Max divergence from UTC | Timestamp granularity |
|---|---|---|
| High-frequency algorithmic trading | 100 microseconds | 1 microsecond or better |
| Voice trading systems | 1 second | 1 second or better |
| Request-for-quote where the response requires human intervention or no algorithmic trading | 1 second | 1 second or better |
| Negotiated transactions | 1 second | 1 second or better |
| Any other trading activity | 1 millisecond | 1 millisecond or better |

Map your activity to a row, then set `TimestampPrecision` accordingly. The engine's check
inspects the **precision of the recorded string**, which is necessary but not sufficient:
clock accuracy and traceability to UTC are infrastructure properties under RTS 25 Article
4 that no downstream inspection of a timestamp can confirm. See
`clock-synchronization-ptp-for-trading-hosts`.

## 4. Retention periods

| Regime | Period |
|---|---|
| MiFID II Article 16(6) | 5 years; up to **7 years** where the competent authority requests it |
| FINRA Rule 4511(b) | At least **6 years** for FINRA books and records with no period specified elsewhere; where records pertain to an account, 6 years after the account is closed |
| SEC Rule 17a-4 | Varies by record type (commonly 3 or 6 years, with an initial period in an easily accessible place) — check the specific paragraph for the record in question |

Jurisdiction-by-jurisdiction detail: `record-retention-periods-by-jurisdiction`.

## 5. Immutability and audit trails

**WORM is no longer the only permitted approach.** The SEC adopted amendments to Rule
17a-4 on 12 October 2022, effective 3 January 2023, which retain WORM as an option and
add an **audit-trail alternative**: an electronic recordkeeping system that preserves
records so an original can be recreated if it is modified or deleted. The amendments also
replaced "electronic storage media" with "electronic recordkeeping system".

What hashing does and does not give you:

- A SHA-256 over a record detects accidental corruption and casual edits **only if the
  hash is held somewhere the editor cannot reach**. A hash stored next to the record it
  covers is recomputable by anyone who edits the record.
- A **hash chain** — each entry committing to the previous entry's hash — makes any edit,
  deletion, or reordering break every subsequent link. This is what the engine implements
  and what `verify_audit_log()` checks.
- A chain still proves nothing against a party who can rewrite the entire log, because
  they can recompute it end to end. **Anchor the head hash externally** — publish it,
  escrow it, or write it to a retention-locked store on a schedule. Only then does
  verification carry evidential weight.

## 6. Benchmarks used for execution-quality screening

Implementation shortfall / arrival price, interval VWAP, TWAP, and participation rate
(POV) are the common comparators. Capture the benchmark **before or during** execution.
A benchmark reconstructed after the fact from the same fills it is meant to assess
measures nothing. Benchmark construction and TCA methodology:
`transaction-cost-analysis-tca-integration`.

## 7. Sources

All consulted August 2026.

- MiFID II Article 27 best-execution standard and the deletion of Article 27(6)/RTS 28 —
  ESMA, "ESMA clarifies certain best execution reporting requirements under MiFID II"
  (13 February 2024), <https://www.esma.europa.eu/press-news/esma-news/esma-clarifies-certain-best-execution-reporting-requirements-under-mifid-ii>;
  DLA Piper, <https://www.dlapiper.com/en/insights/publications/2024/02/esma-publishes-statement-on-reporting-requirements-under-rts-28-of-mifid-ii>
- RTS 27 suspension under Directive (EU) 2021/338 and subsequent ESMA deprioritisation —
  ESMA public statement on RTS 27 reporting, <https://www.esma.europa.eu/sites/default/files/library/esma35-43-3444_public_statement_rts_27_reporting.pdf>
- SEC Regulation Best Execution withdrawal — SEC, "Regulation Best Execution" rulemaking
  status page, <https://www.sec.gov/rules-regulations/2025/06/regulation-best-execution>;
  Federal Register, "Withdrawal of Proposed Regulatory Actions" (17 June 2025),
  <https://www.federalregister.gov/documents/2025/06/17/2025-11110/withdrawal-of-proposed-regulatory-actions>;
  original proposal, <https://www.sec.gov/files/rules/proposed/2022/34-96496.pdf>
- Rule 605 amendments, scope, and compliance-date extension to 1 August 2026 — Federal
  Register, "Extension of Compliance Date for Disclosure of Order Execution Information"
  (2 October 2025), <https://www.federalregister.gov/documents/2025/10/02/2025-19316/extension-of-compliance-date-for-disclosure-of-order-execution-information>;
  SEC Release 34-104147, <https://www.sec.gov/files/rules/final/2025/34-104147.pdf>;
  Sidley, <https://www.sidley.com/en/insights/newsupdates/2024/04/sec-adopts-amendments-to-modernize-disclosure-of-order-execution-information>
- Rule 606 obligations and 606(b)(3) on-request reports — SEC staff FAQs on Rule 606,
  <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/faq-rule-606-regulation>;
  SEC press release 2018-253, <https://www.sec.gov/newsroom/press-releases/2018-253>
- FINRA Rule 5310 and the regular-and-rigorous review cadence — FINRA Rule 5310,
  <https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310>; FINRA best-execution
  guidance, <https://www.finra.org/rules-guidance/guidance/reports/2021-finras-examination-and-risk-monitoring-program/best-execution>
- FINRA Rule 4511 six-year retention — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511>
- SEC Rule 17a-4 electronic recordkeeping amendments and the audit-trail alternative —
  SEC, "Amendments to Electronic Recordkeeping Requirements for Broker-Dealers",
  <https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers>;
  Sidley, <https://www.sidley.com/en/insights/newsupdates/2022/10/sec-modernizes-broker-dealer-recordkeeping-requirements>
- MiFID II Article 16(6) five/seven-year retention —
  <https://www.grip.globalrelay.com/rules/eu-mifid-ii-art-16/>
- RTS 25 clock synchronisation tables (Commission Delegated Regulation (EU) 2017/574) —
  reproduced at <https://www.emissions-euets.com/time-stamping-and-business-clocks-synchronisation>.
  *The EUR-Lex primary text did not render through automated retrieval during this
  review; the table values above were cross-checked against a second independent source
  (<https://www.pico.net/assets/resources/documents/clock-synchronization-mifid-ii.pdf>)
  but should be confirmed against the Official Journal text before being relied on for a
  compliance decision.*

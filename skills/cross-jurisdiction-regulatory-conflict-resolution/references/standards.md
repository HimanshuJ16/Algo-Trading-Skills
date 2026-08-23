# Standards for Cross-Jurisdiction Regulatory Conflict Resolution

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Rule Resolution Strategy | Cross-border orders MUST be evaluated under **Strictest Rule Primacy** across every applicable regime. This is a firm-policy default for *prohibition-style* rules only; a mandate-vs-prohibition conflict MUST be escalated, not resolved by MAX/AND/OR. |
| Zero Non-Compliant PFOF | PFOF routing MUST be blocked if ANY applicable jurisdiction prohibits payment for order flow (AND across jurisdictions). |
| Legal Entity Identifier (LEI) | An LEI MUST be validated structurally before use: 20 upper-case alphanumerics, positions 19-20 numeric ISO/IEC 7064 MOD 97-10 check digits, `value % 97 == 1` after A-Z → 10-35 conversion (ISO 17442-1:2020). Length-only checks are prohibited. |
| LEI Is Not a Universal Client ID | Natural-person clients MUST be identified by a national client identifier (MiFIR Art. 26, RTS 22 Art. 6 and Annex II; CONCAT fallback), never by requiring an LEI. |
| Structural Validation Is Not Sufficient | Offline validation MUST NOT be treated as confirmation that an LEI is issued, correctly attributed, or in an active registration status; a GLEIF lookup remains required before transaction reporting. |
| Short-Selling Severity Ordering | The MAX resolution MUST use `NONE(0) < REPORTING(1) < PRICE_TEST(2) < BAN(3)`, ordered by restriction on the ability to execute. A disclosure-only regime MUST NOT outrank a price test. |
| Fail-Closed Configuration | An unregistered jurisdiction MUST resolve to the strictest value on EVERY dimension (PFOF blocked, LEI mandatory, short selling banned) and be surfaced to operators. An empty jurisdiction set MUST raise, never resolve permissively. |
| Non-Blocking Obligations | Constraints that do not block the order (price test, net short position reporting, LEI tagging) MUST be returned explicitly for downstream enforcement rather than dropped on an APPROVED decision. |
| Audit Decision Integrity | Every evaluated order MUST produce an audit-trail entry, and the accessor MUST return copies so a recorded REJECTED decision cannot be rewritten after the fact. |
| Deterministic Audit Records | Rationale strings MUST be built from a sorted jurisdiction list; identical orders MUST produce byte-identical audit text. |

## Regulatory Anchors (verify currency before relying on them)

| Jurisdiction / Regime | Provision | Effect on this engine |
|---|---|---|
| EU (MiFIR) | Art. 39a, inserted by Regulation (EU) 2024/791 (in force 28 Mar 2024) | Prohibits receiving payment/benefit for routing retail and opt-in professional client orders to a particular venue. Member states could exempt pre-existing domestic activity until **30 June 2026**; only Germany notified ESMA of using the carve-out, so the ban applies EU-wide from 1 July 2026. Configure EU as `is_pfof_allowed=False`. |
| EU (MiFIR) | Art. 26 transaction reporting; RTS 22 = Commission Delegated Reg. (EU) 2017/590, Arts. 5-6 and Annex II | "No LEI, no trade" for legal-entity clients; natural persons are identified by the ISO 3166-1 alpha-2 nationality prefix plus the Annex II national identifier, with CONCAT as fallback. ESMA validation additionally requires the code to be present in GLEIF with an entity status active on the trading date. |
| Global (ISO) | ISO 17442-1:2020; ISO/IEC 7064 MOD 97-10 | LEI = 20 alphanumeric characters; 1-4 LOU prefix, 5-18 entity part, 19-20 check digits. Validation: map A-Z to 10-35, read the 20 characters as one integer, require remainder 1 mod 97. |
| UK (FCA) | COBS 2.3 inducements; FSA FG12/13 "Payment for Order Flow" (2012), carried forward by the FCA | PFOF treated as an inducement incompatible with the inducement and best-execution rules for retail and professional client business — configure UK as `is_pfof_allowed=False`. |
| US (SEC) | Reg NMS Rules 605/606 (Rule 605 amended by Release 34-99679, adopted 6 Mar 2024) | PFOF is permitted subject to order-routing/execution-quality disclosure rather than prohibited — the core EU/UK vs US conflict this engine resolves. |
| US (SEC) | Reg SHO Rule 201 (adopted 24 Feb 2010) | Alternative uptick rule: once a security falls ≥10% from the prior day's closing price, short sales are permitted only above the national best bid for the remainder of that day and the next. Maps to `PRICE_TEST` — a constraint on price, not a ban. Rule 203(b) locate/close-out duties are NOT modelled here. |
| EU (SSR) | Regulation (EU) No 236/2012, Arts. 5-6; threshold set by Commission Delegated Reg. (EU) 2022/27 (applicable 31 Jan 2022) | Net short positions ≥ **0.1%** of issued share capital must be notified to the competent authority (public disclosure from 0.5%). A disclosure duty, not a trading restriction — maps to `REPORTING`. Emergency NCA bans under Arts. 20/23 map to `BAN`. |
| Korea (FSC) | Short-selling prohibition Nov 2023 – 30 Mar 2025 | Illustrative `BAN` configuration only. The prohibition was **lifted on 31 March 2025** with full resumption across KRX-listed stocks; do not ship this as a live rule profile without re-checking current FSC status. |

Sources consulted (Aug 2026): EUR-Lex Regulation (EU) 2024/791 and Regulation (EU) No 236/2012;
Commission Delegated Regulation (EU) 2022/27; Commission Delegated Regulation (EU) 2017/590
(RTS 22) and ESMA RTS 22 Annex II; ESMA MiFIR transaction-reporting validation-rule guidance;
ISO 17442-1:2020 / ISO/IEC 7064; SEC releases on Reg SHO Rule 201 and Reg NMS Rule 605
(34-99679); FCA FG12/13 and COBS 2.3; Korea FSC press release on the 31 Mar 2025 resumption.
Dates and status verified August 2026 — re-verify before relying on them.

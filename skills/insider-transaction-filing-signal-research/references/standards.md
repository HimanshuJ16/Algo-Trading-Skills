# Standards for SEC Form 4 Insider Filing Factor Research

Two kinds of requirement appear below and they are not interchangeable.

**Regulatory facts** are obligations on the *filer* under US Securities Exchange Act Section 16
and the SEC rules thereunder. They constrain what the data can possibly contain; they say nothing
about how you must build a signal. No regulator mandates an insider-sentiment methodology.

**Engineering standards** are this skill's own requirements. "MUST" there means "MUST, to produce
a defensible research signal with this module."

Jurisdiction throughout: **United States**, issuers with a class of equity securities registered
under Exchange Act Section 12. Nothing here transfers to UK MAR Article 19 PDMR notifications, EU
MAR managers' transactions, or SEBI (Prohibition of Insider Trading) Regulations disclosures.

## Regulatory facts constraining the data

| Fact | Authority | Effect on the signal |
|---|---|---|
| Form 4 is due "before the end of the second business day following the day on which the subject transaction has been executed." | 17 CFR 240.16a-3(g)(1); SEC Form 4 General Instruction 1(a) | The trade date is not the information date. A minimum two-business-day gap is structural. |
| Where the reporting person does not select the date of execution under a Rule 10b5-1(c) arrangement, the date the executing broker, dealer or plan administrator notifies the reporting person is deemed the date of execution — but not later than the third business day after the trade date. | 17 CFR 240.16a-3(g)(2)–(4) | A fully compliant filing can be public roughly **five business days** after the trade. A fixed +2-day offset understates the lag. |
| The Rule 10b5-1(c) checkbox — "Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c)" — plus the plan adoption date in "Explanation of Responses". | SEC Form 4, General Instruction 10; adopted by SEC Release 33-11138 | The only structured plan indicator that exists. |
| Section 16 reporting persons must comply with the amended Forms 4 and 5 for reports **filed on or after 1 April 2023**. Before that: "Today, the disclosure of a purchase or sale under a Rule 10b5-1 trading arrangement in Forms 4 and 5 is voluntary, resulting in a lack of consistent and comprehensive information about such trades." | SEC Release 33-11138 (adopted 14 Dec 2022; effective 27 Feb 2023) | An unchecked box before 2023-04-01 is *not disclosed*, not *not a plan trade*. Model it as UNKNOWN. |
| Code **P** = "Open market **or private** purchase of non-derivative or derivative security"; code **S** = "Open market **or private** sale". Equity-swap-linked trades take a combined code, "S/K" or "P/K". Codes A, D, F, I, M, C, E, H, O, X, G, L, W, Z, J, K, U cover grants, exercises, withholding, gifts, expiries and other exempt events. | SEC Form 4, General Instruction 8 | Execution venue cannot be inferred from the code. A bare `== "S"` test drops combined codes. |
| Reporting-owner relationship is four independent booleans — `isDirector`, `isOfficer`, `isTenPercentOwner`, `isOther` — with `officerTitle` (string, max 30 characters) mandatory only when `isOfficer` is true. | EDGAR Ownership XML Technical Specification v3, § reportingOwnerRelationship | There is no structured role enumeration and no single "role" field. The flags are not mutually exclusive. |
| Bona fide gifts (code G) moved from Form 5 to Form 4, reportable within two business days. | SEC Release 33-11138 | Post-Feb-2023 Form 4 feeds contain gift lines that earlier feeds did not. Count them; never score them. |
| EDGAR accepts filings each business day 06:00–22:00 ET. "Some filing submissions that begin after 5:30 p.m. ET — or 10:00 p.m. for Ownership forms 3, 4, 5 — will be disseminated the next business day, showing up in the following business day's index." | SEC, "Accessing EDGAR Data" — Business hours and dissemination | The dissemination instant is timezone-sensitive and can slip a business day. Naive timestamps must be rejected. |

## Engineering standards

| Metric | Engineering Standard | Basis |
|---|---|---|
| Point-in-Time Alignment | Signals MUST be evaluated against a timezone-aware `as_of` instant and gated on the EDGAR **`filing_datetime`**, never on `transaction_date`. Naive datetimes MUST be rejected rather than coerced. | 17 CFR 240.16a-3(g); a naive instant silently adopts the host clock and the resulting look-ahead is invisible in the equity curve. |
| Filing-Lag Realism | A fixed offset from the transaction date MUST NOT stand in for the filing timestamp. | Rule 16a-3(g)(2)–(4) extends the deemed execution date for plan trades; late filings exist beyond it. CMP report a median trade-to-report delay of 3 days over 1986–2007. |
| 10b5-1 Plan Status | Plan status MUST be modelled with three states (PLAN / NON_PLAN / UNKNOWN). Filings dated before 2023-04-01 MUST be supplied as UNKNOWN unless the plan was affirmatively disclosed, and the UNKNOWN count MUST be surfaced in the report. | SEC Release 33-11138: pre-amendment disclosure was voluntary. |
| 10b5-1 Filtering | Excluding plan trades MUST be a configurable research choice, MUST NOT be described as removing noise, and MUST be reported as a count. | Release 33-11138 documents -2.5% six-month industry-adjusted returns after first sales under short-cooling-off plans and single-trade plans (49% of the sample) "consistently loss-avoiding regardless of cooling-off period"; Jagolinzer (2009). Plan trades carry documented signal. |
| Routine/Opportunistic Classification | The CMP classifier MUST use only transactions dated strictly before the classification year, and MUST emit UNCLASSIFIED for insiders lacking a trade in each preceding year rather than defaulting them to opportunistic. | CMP (2012) § II: routine = "placed a trade in the same calendar month for at least three consecutive years"; "We require an insider to make at least one trade in each of the three preceding years in order to define her as either an opportunistic or a routine trader." |
| Transaction Code Handling | Only primary codes P and S MAY be scored. Combined codes MUST be resolved to their leading component. Every non-P/S code MUST be counted, and unrecognised codes MUST raise rather than be dropped. | SEC Form 4, General Instruction 8. |
| Open-Market Determination | Open-market status MUST come from a separate field, never inferred from code P or S. | Codes P and S cover open market *or private* transactions. |
| Role Weighting | Capacity MUST be resolved from the four EDGAR relationship booleans plus the free-text `officerTitle`, taking the highest applicable weight. A filer with no weighted flag MUST be counted and MUST NOT receive an undocumented middle weight. | EDGAR Ownership XML Technical Specification v3; the flags are independent and overlap. |
| Weight Sign Discipline | Role weights MUST be finite and non-negative. | A negative weight inverts trade direction and breaks the score's [-1, +1] bound. |
| Input Validation | `shares` and `price` MUST be finite and strictly positive. Direction comes from the transaction code, never from a negative quantity. | A NaN price propagates through both the numerator and the denominator, making every threshold comparison false and returning a spurious NEUTRAL. |
| Sample Sufficiency | The engine MUST emit `INSUFFICIENT_DATA` below the configured minimum scored notional or minimum distinct-insider count. | The score is a scale-free ratio: one de-minimis purchase saturates it at +1.00 and reads identically to a broad, large insider bid. |
| Threshold Calibration | The ±0.30 classification cut-offs and the role weight schedule (CEO/CFO 1.0, other officer 0.8, director 0.6, 10% owner 0.3) are illustrative defaults, NOT validated constants, and MUST be re-estimated out-of-sample per universe before live use. | No published study establishes these values. |
| Report Reconciliation | The exclusion counters plus the scored counts MUST equal the number of filings supplied. | A silent drop is indistinguishable from a genuinely empty signal. |
| Amendment Hygiene | Form 4/A amendment chains MUST be resolved upstream; a repeated filing identifier MUST be surfaced. | An amendment restates transaction lines under a new accession; carrying both double-counts the same economic trade. |

## Sources

- SEC **Form 4**, "Statement of Changes in Beneficial Ownership" (OMB 3235-0287). General
  Instruction 1 (filing deadline), General Instruction 8 (complete transaction code table),
  General Instruction 10 (Rule 10b5-1(c) transaction indication).
  <https://www.sec.gov/files/form4.pdf>
- **17 CFR 240.16a-3(g)** — "Reporting transactions and holdings": Form 4 due before the end of
  the second business day following execution; (g)(2)–(4) deemed date of execution for
  broker/plan-executed transactions.
- **SEC Release No. 33-11138 / 34-96492**, "Insider Trading Arrangements and Related Disclosures"
  (adopted 14 December 2022; effective 27 February 2023; Section 16 form compliance from
  1 April 2023). Cooling-off periods, mandatory Form 4/5 checkbox, gift reporting on Form 4, and
  the Section V economic analysis of returns around Rule 10b5-1 plan sales.
  <https://www.sec.gov/files/rules/final/2022/33-11138.pdf>
- **EDGAR Ownership XML Technical Specification (v3)** — `reportingOwnerRelationship`
  (`isDirector`, `isOfficer`, `isTenPercentOwner`, `isOther`), `officerTitle` (string, 30),
  `periodOfReport`, per-transaction `transactionDate`.
  <https://www.sec.gov/info/edgar/ownershipxmltechspec-v3_d.pdf>
- Cohen, L., Malloy, C. & Pomorski, L. (2012). "Decoding Inside Information." *Journal of Finance*
  67(3), 1009–1043. Routine/opportunistic definition and sample construction: Section II. Reported
  results: an equal-weighted long-opportunistic-buys / short-opportunistic-sells portfolio earns a
  five-factor alpha of 180 bps per month ($t=6.07$); the value-weighted spread is 82 bps per
  month. NBER working paper: <https://www.nber.org/papers/w16454>
- Jagolinzer, A. D. (2009). "SEC Rule 10b5-1 and Insiders' Strategic Trade." *Management Science*
  55(2), 224–239. Participating insiders' "sales systematically follow positive and precede
  negative firm performance."
- Lakonishok, J. & Lee, I. (2001). "Are Insider Trades Informative?" *Review of Financial Studies*
  14(1), 79–111. NYSE/AMEX/Nasdaq, 1975–1995: "informativeness of insiders' activities is coming
  from purchases, while insider selling appears to have no predictive ability." Useful context for
  why a symmetric buy/sell score may be the wrong parameterisation for some universes.
- Larcker, D. F., Levy, B., Quinn, P. J., Tayan, B. & Taylor, D. J. (2021). "Gaming the System:
  Three 'Red Flags' of Potential 10b5-1 Abuse." Stanford Rock Center for Corporate Governance,
  *Closer Look* series. Cited throughout Release 33-11138's economic analysis; the source of the
  -2.5% / -4% figures quoted above. Release 33-11138 notes as a caveat that "the tests of
  statistical significance of the differences are not shown in the study.

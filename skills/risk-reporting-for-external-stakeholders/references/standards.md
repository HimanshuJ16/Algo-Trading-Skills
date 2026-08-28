# Standards for Risk Reporting for External Stakeholders

Every claim this skill makes, with the primary source it rests on. Where a
figure is a market convention or a repository choice rather than a published
requirement, that is stated rather than implied.

Verified August 2026. Form PF and AIFMD reporting are both mid-reform (see
"Currency of these sources" at the end) — re-check before relying on a cadence.

## The boundary that matters most: this is not a filing

Version 1 of this skill claimed to generate "SEC Form PF / FCA Annex IV" reports
and instructed that position redaction was **mandatory for regulators**. That is
backwards. The statutory regimes require position- and counterparty-level detail.

| Requirement | Source | What it actually says |
|---|---|---|
| Position-level detail, per fund, monthly | Form PF, section 2b, Question 35 | "For each open position of the reporting fund that represents 5% or more of the reporting fund's net asset value, provide the information requested below" — % of net asset value and sub-asset class, for each of the three months of the reporting period. |
| Total open positions | Form PF, section 2b, Question 34 | "Total number of open positions (approximate), determined on the basis of each position and not the issuer or counterparty". |
| Counterparty-level detail | Form PF, section 2b, Questions 22 and 23 | Q22: "Identify the five counterparties to which the reporting fund has the greatest mark-to-market [net counterparty credit exposure]". Q23: the five counterparties with the greatest exposure **to** the fund. Q36–Q38 then ask for collateral posted in each direction. |
| Principal exposures per AIF | Directive 2011/61/EU Art. 24; Reg. (EU) No 231/2013 Art. 110(1) and Annex IV | AIFMs "shall complete... the pro-forma reporting template set out in Annex IV" for each AIF managed or marketed in the Union, covering markets and instruments traded and the AIF's principal exposures. |
| Filings are confidential, which is why redaction is not the control | Form PF, General Instructions, "Federal Information Law and Requirements for a Collection of Information"; Advisers Act s.204(b) [15 U.S.C. § 80b-4(b)]; 17 CFR 275.204(b)-1 | "The SEC does not intend to make public information reported on Form PF that is identifiable to any particular adviser or private fund, although the SEC may use Form PF information in an enforcement action." |

The information barrier this skill enforces belongs on the **discretionary**
channel — LP letters, prime broker feeds, auditor appendices, supervisory
requests answered in aggregate — not on a statutory filing.

## Who files what, and how often

| Regime | Who | Cadence | Source |
|---|---|---|---|
| Form PF | SEC-registered investment advisers with at least USD 150 million in private fund assets under management | Annually for smaller private fund advisers | 17 CFR 275.204(b)-1; Form PF General Instructions, Instruction 1 |
| Form PF | **Large hedge fund adviser**: at least USD 1.5 billion in hedge fund assets under management | Quarterly, within 60 days of fiscal quarter end. Section 2b is completed for each **qualifying hedge fund** (a hedge fund with net assets of at least USD 500 million) | Form PF General Instructions; Glossary |
| AIFMD Annex IV | AIFMs above the Art. 3(2) registration thresholds, up to EUR 1 billion total AUM | Half-yearly | Reg. (EU) No 231/2013 Art. 110(3) |
| AIFMD Annex IV | AIFMs above EUR 1 billion total AUM | Quarterly | Reg. (EU) No 231/2013 Art. 110(3) |
| UK (post-Brexit) | Full-scope UK AIFM | Quarterly, half-yearly or annual, "with reference to the AUM thresholds and other criteria set out in SUP 16.18"; small authorised UK AIFM reports annually (SUP 16.18.6R) | FCA Handbook SUP 16.18; FCA, *Reporting Annex IV transparency information under AIFMD* |
| UK | Returns are AIF001 (AIFM level) and AIF002 (AIF level) | Submitted through the FCA's regulatory reporting system — Gabriel in the guidance cited, since migrated to RegData. Confirm the current channel with the FCA before a submission window. | FCA, *Reporting Annex IV transparency information under AIFMD* |

Art. 110(3) also contains per-AIF frequency uplifts beyond the AIFM-level
thresholds above. Read the article for your fund's exact cadence rather than
inferring it from the AIFM tier.

**LP reporting has no equivalent federal mandate in the US.** The SEC's Private
Fund Advisers rule — including the quarterly statement rule, Rule 211(h)(1)-2 —
was vacated in its entirety by the US Court of Appeals for the Fifth Circuit in
June 2024, which held that the SEC exceeded its authority under Advisers Act
sections 211(h) and 206(4). LP risk-letter content and cadence are governed by
the limited partnership agreement and side letters, plus the Advisers Act
antifraud provisions. Do not describe a quarterly LP statement as an SEC
requirement.

## Source-to-behaviour map

| Engine behaviour | Source | What it actually says |
|---|---|---|
| `var_pct_of_nav` is a **percentage of NAV** | Form PF Q40(b)(vii)–(ix) | "VaR at the end of the 1st month of the reporting period (as a % of NAV)". |
| `var_confidence_pct` and `var_horizon_days` are **required, not defaulted** | Form PF Q40(b)(i)–(ii) | "(i) Confidence interval used (e.g., 100%-alpha%) (as a percentage) ... (ii) Time horizon used (in number of days)". Q40(b) further requires a separate response "for each such combination" where multiple are used. |
| VaR is a positive loss magnitude | Form PF Glossary, "VaR" | "For a given portfolio, the loss over a target horizon that will not be exceeded at some specified confidence level." |
| `LiquidityConvention.BUCKETED` is the default, and buckets must sum to ~100% | Form PF Q32 | "Specify the percentage by value of the reporting fund's positions that may be liquidated within each of the periods specified below. **Each investment should be assigned to only one period**... (The total should add up to approximately 100%.)" |
| The Form PF Q32 bucket schedule (`FORM_PF_Q32_LIQUIDITY_BUCKETS`) | Form PF Q32 | "1 day or less; 2 days – 7 days; 8 days – 30 days; 31 days – 90 days; 91 days – 180 days; 181 days – 365 days; Longer than 365 days". |
| Bucket assignment is by shortest reasonable liquidation period, no fire-sale discount | Form PF Q32 | "...based on the shortest period during which you believe that such position could reasonably be liquidated at or near its carrying value... assuming no fire-sale discounting." |
| Contingent legs share the least-liquid leg's bucket | Form PF Q32 | "In the event that individual positions are important contingent parts of the same trade, group all those positions under the liquidity period of the least liquid part". |
| HMAC is what turns the digest into authentication | NIST FIPS 198-1, *The Keyed-Hash Message Authentication Code (HMAC)* | HMAC uses a cryptographic hash with a shared secret key to provide message authentication; an unkeyed hash provides neither authentication nor a defence against an attacker who alters content and recomputes the hash. |
| Non-repudiation needs an asymmetric signature, which this module does not provide | NIST FIPS 186-5, *Digital Signature Standard (DSS)* | Digital signatures under DSS bind a message to a private key held only by the signer, which is what lets a third party attribute it. HMAC is symmetric and cannot. |
| SHA-256 is the digest | NIST FIPS 180-4, *Secure Hash Standard* | Defines SHA-256. |
| Constant-time tag comparison | Python `hmac.compare_digest` | Reduces the timing side channel available to an attacker probing tag comparison. |
| `gross_leverage`/`net_leverage` are **not** AIFMD leverage | Reg. (EU) No 231/2013 Arts. 7–8 | AIFMD leverage is calculated by the gross method (Art. 7) and the commitment method (Art. 8), which convert derivative positions into equivalent positions in the underlying. A simple exposure ÷ NAV ratio is neither. |

## Repository conventions, not published requirements

These are engineering choices. They are defensible, but do not cite them as
rules to a regulator or an LP:

- **`LIQUIDITY_SUM_TOLERANCE_PCT = 1.0`** — Form PF says the buckets "should add
  up to approximately 100%" without quantifying "approximately". One percentage
  point accommodates rounding in the caller's aggregation without accepting a
  profile wrong by a whole bucket.
- **`SECTOR_SUM_TOLERANCE_PCT = 0.5`** — slack on the check that absolute sector
  exposures cannot exceed gross exposure as a percentage of NAV. The invariant
  itself is arithmetic, not regulatory.
- **Top-5 for LPs, top-3 for prime brokers, full for regulators and auditors** —
  carried over from version 1 and reasonable, but they are house policy. Your
  LPA, side letters and prime brokerage agreement govern. The engine's
  contribution is that the policy is explicit, per-recipient, and fails closed
  for anyone not named.
- **Ranking by absolute exposure** — follows from what "concentration" means, not
  from a citation.
- **`report_id` ending in the digest prefix** — chosen so a restatement gets a
  new identifier while a byte-identical regeneration is idempotent.
- **`DEFAULT_IDENTIFIER_FIELDS`** — a starting list of position-dict keys, not a
  standard. Extend it to match your position records.

## Currency of these sources

- **Form PF is mid-reform.** The amendments adopted 8 February 2024 have had
  their compliance date extended three times — to 12 June 2025, then 1 October
  2025, then **1 October 2026** (SEC/CFTC, September 2025), to allow the SEC to
  complete a substantive review of the form. Filings due before that date use the
  current version of the form. Separately, the SEC and CFTC proposed further
  amendments in 2026 that would raise the all-filers threshold from USD 150
  million to USD 1 billion and the large-hedge-fund-adviser threshold from USD
  1.5 billion to USD 10 billion; those are **proposed, not adopted**, and the
  thresholds in the table above are the ones currently in force.
- **AIFMD II (Directive (EU) 2024/927)** had a member-state transposition
  deadline of 16 April 2026 and amends Art. 24 supervisory reporting, moving from
  principal markets and top exposures toward comprehensive coverage of each AIF's
  markets, instruments and exposures. ESMA is mandated to deliver RTS/ITS
  replacing the Annex IV template, with a delivery deadline to the Commission of
  16 April 2027. Until that template is in force, Annex IV as described above
  remains the operative one — but this is the field most likely to be stale.

## Sources

- Form PF (paper version), SEC/CFTC — General Instructions, section 2b (Q22, Q23,
  Q32, Q34, Q35, Q36–Q38, Q40), and Glossary.
  <https://www.cftc.gov/sites/default/files/idc/groups/public/@newsroom/documents/file/formpf.pdf>
- 17 CFR § 275.204(b)-1 — Reporting by investment advisers to private funds.
  <https://www.law.cornell.edu/cfr/text/17/275.204b-1>
- SEC, *Form PF Frequently Asked Questions*, Division of Investment Management.
  <https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/form-pf-faq>
- SEC press release 2025-119, *SEC and CFTC Extend Form PF Compliance Date to
  Oct. 1, 2026* (September 2025).
  <https://www.sec.gov/newsroom/press-releases/2025-119-sec-cftc-extend-form-pf-compliance-date-oct-1-2026>
- Directive 2011/61/EU (AIFMD), Article 24 — Reporting obligations to competent
  authorities. <https://eur-lex.europa.eu/eli/dir/2011/61/oj>
- Commission Delegated Regulation (EU) No 231/2013, Articles 7–8 (leverage
  calculation) and Article 110 with Annex IV (reporting to competent
  authorities). <https://eur-lex.europa.eu/eli/reg_del/2013/231/oj>
- Directive (EU) 2024/927 (AIFMD II). <https://eur-lex.europa.eu/eli/dir/2024/927/oj>
- FCA Handbook, SUP 16.18 — AIFMD reporting.
  <https://www.handbook.fca.org.uk/handbook/SUP/16/18.html>
- FCA, *Reporting Annex IV transparency information under the Alternative
  Investment Fund Managers Directive*.
  <https://www.fca.org.uk/publication/documents/reporting-annex-iv-transparency-aifmd.pdf>
- *National Association of Private Fund Managers v. SEC*, US Court of Appeals for
  the Fifth Circuit (June 2024) — vacating the Private Fund Advisers rule in its
  entirety.
- NIST FIPS 180-4, *Secure Hash Standard (SHS)*.
  <https://csrc.nist.gov/pubs/fips/180-4/upd1/final>
- NIST FIPS 198-1, *The Keyed-Hash Message Authentication Code (HMAC)*.
  <https://csrc.nist.gov/pubs/fips/198-1/final>
- NIST FIPS 186-5, *Digital Signature Standard (DSS)*.
  <https://csrc.nist.gov/pubs/fips/186-5/final>

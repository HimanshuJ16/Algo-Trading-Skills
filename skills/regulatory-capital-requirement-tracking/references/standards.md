# Standards for Regulatory Capital Requirement Tracking

Every threshold and modelling decision in this skill, with the primary source it
rests on. Where a figure is a repository convention rather than a published
requirement, that is stated rather than implied.

## Source-to-behaviour map

| Engine behaviour | Source | What it actually says |
|---|---|---|
| Requirement components aggregate by **greater-of** | 17 CFR 240.15c3-1(a) | "Every broker or dealer must at all times have and maintain net capital no less than **the greater of** the highest minimum requirement applicable to its ratio requirement under paragraph (a)(1) of this section, or to any of its activities under paragraph (a)(2) of this section, and must otherwise not be 'insolvent'…" |
| Requirement components aggregate by **greater-of** | FCA MIFIDPRU 4.3.2R | "The own funds requirement of a non-SNI MIFIDPRU investment firm is **the highest of**: (1) its permanent minimum capital requirement under MIFIDPRU 4.4; (2) its fixed overheads requirement under MIFIDPRU 4.5; or (3) its K-factor requirement under MIFIDPRU 4.6." (MIFIDPRU 4.3.3R: for an SNI firm, "the higher of" PMR or FOR.) |
| `net_capital = net worth + qualifying sub debt − non-allowable assets − haircuts` | 17 CFR 240.15c3-1(c)(2) | "The term net capital shall be deemed to mean the net worth of a broker or dealer, adjusted by:" — the adjustments below. |
| `qualifying_subordinated_debt` added back | 15c3-1(c)(2)(ii) | Excludes "liabilities of the broker or dealer which are subordinated to the claims of creditors pursuant to a satisfactory subordination agreement" (see Appendix D for what makes an agreement satisfactory). |
| `non_allowable_assets` deducted | 15c3-1(c)(2)(iv) | Deducting "fixed assets and assets which cannot be readily converted into cash". |
| `securities_haircuts` deducted **from capital**, not added to the requirement | 15c3-1(c)(2)(vi) | "Deducting the percentages specified in paragraphs (c)(2)(vi)(A) through (M) of this section (or the deductions prescribed for securities positions set forth in Appendix A) of the market value of all securities, money market instruments or options". |
| `CAPITAL_DEFICIT` carries a same-day notice | 17 CFR 240.17a-11(a)(1) | "Every broker or dealer whose net capital declines below the minimum amount required pursuant to § 240.15c3-1, or is insolvent as that term is defined in § 240.15c3-1(c)(16), must give notice of such deficiency **that same day**…" |
| `WARNING_BUFFER_BREACHED` at **120%**, carrying 24-hour notice | 17 CFR 240.17a-11(b)(3) | Notice within 24 hours if a computation "shows that its total net capital is **less than 120 percent** of the broker's or dealer's required minimum net capital". |
| Floor comparison is `>=` (at-the-floor is not a breach) | 15c3-1(a) | "net capital **no less than** the greater of…" |
| Warning comparison is `<` (exactly 120% does not warn) | 17a-11(b)(3) | "**less than** 120 percent of … required minimum net capital". |
| No default requirement — `spec` is mandatory | 15c3-1(a)(2) | The minimum depends on permissions: (a)(2)(i) "$250,000" for a firm carrying customer accounts; (a)(2)(ii) "$100,000" for a firm exempt from Rule 15c3-3; (a)(2)(iii) "$100,000" for dealers; (a)(2)(iv) "$50,000" for introducing brokers. |
| No default requirement — `spec` is mandatory | FCA MIFIDPRU 4.4.1R | PMR is GBP 750,000 for firms with permission for dealing on own account (also underwriting/placing on a firm commitment basis, and certain OTFs); GBP 150,000 for MTF operators, certain OTF operators, and firms holding client money or assets; GBP 75,000 for reception and transmission of orders, execution on behalf of clients, portfolio management, and investment advice. |
| Ratio requirement is the caller's to compute | 15c3-1(a)(1)(i) | "No broker or dealer, other than one that elects the provisions of paragraph (a)(1)(ii) of this section, shall permit its aggregate indebtedness to all other persons to exceed 1500 percent of its net capital (or 800 percent of its net capital for 12 months after commencing business as a broker or dealer)." |
| Ratio requirement is the caller's to compute (alternative method) | 15c3-1(a)(1)(ii) | A firm electing the alternative standard maintains "net capital … not less than the greater of $250,000 or 2 percent of aggregate debit items". |
| Fixed overheads requirement is the caller's to compute | FCA MIFIDPRU 4.5.1R | "The fixed overheads requirement of a MIFIDPRU investment firm is an amount equal to one quarter of the firm's relevant expenditure during the preceding year." |

## Why Basel III is not modelled

Basel III sets **ratios to risk-weighted assets**, not an absolute currency
floor, and sets three of them simultaneously against three different definitions
of eligible capital. Basel III (BCBS, *A global regulatory framework for more
resilient banks and banking systems*, rev. June 2011) ¶50:

> "Common Equity Tier 1 must be at least 4.5% of risk-weighted assets at all
> times. Tier 1 Capital must be at least 6.0% of risk-weighted assets at all
> times. Total Capital (Tier 1 Capital plus Tier 2 Capital) must be at least
> 8.0% of risk-weighted assets at all times."

And ¶129:

> "A capital conservation buffer of 2.5%, comprised of Common Equity Tier 1, is
> established above the regulatory minimum capital requirement."

A single `net_capital` scalar compared to a single floor cannot express three
simultaneous tests over three capital definitions. Version 1 of this skill
listed Basel III among its supported frameworks; it had no RWA input, no ratio
test, and no tier separation, so that claim is withdrawn.

If you want a single-tier test, translate it yourself: pass `ratio × RWA` as a
requirement component and that tier's own funds as the capital figure. The
answer speaks only to the tier you translated. Note also that the conservation
buffer *stacks* on the minimum (8.0% + 2.5%), which is the case
`AGGREGATION_SUM` exists for — Basel is the counterexample to greater-of, not
an instance of it.

## Repository conventions, stated as such

| Convention | Status |
|---|---|
| `early_warning_pct` default of **1.20** | A **US rule** (17a-11(b)(3)) for broker-dealers. Applied to a MIFIDPRU firm or any other regime it is a prudent house buffer with no regulatory force. Set it to what your own regime requires. |
| Tie-break in greater-of resolves to the alphabetically-first component name | Determinism convention. No regulatory content — when two components are equal the amount is the same either way; only the reported `binding_component` label is affected. |
| Rejecting a zero or negative requirement component | Engineering convention. No regime this skill models has a zero floor, and under greater-of a zero component would vanish silently, hiding a failed upstream computation. |
| Rejecting `non_allowable_assets > total_assets` | Engineering convention. Non-allowable assets are a subset of assets by construction; a larger figure indicates a unit or scope error. |
| Amounts held as `float` | Engineering convention. Round to your reporting precision **before** constructing; the engine does not round, and its display formatting is round-half-even. |

## Cadence

15c3-1(a) requires the minimum be maintained "at all times", which is stricter
than any reporting cadence. Daily end-of-day computation is the practical floor,
not the standard the rule sets. A firm inside its warning band should compute
more often than daily.

## Sources

- 17 CFR § 240.15c3-1 — Net capital requirements for brokers or dealers.
  <https://www.law.cornell.edu/cfr/text/17/240.15c3-1>
- 17 CFR § 240.17a-11 — Notification provisions for brokers and dealers.
  <https://www.law.cornell.edu/cfr/text/17/240.17a-11>
- FCA Handbook, MIFIDPRU 4.3 (own funds requirement), 4.4 (permanent minimum
  capital requirement), 4.5 (fixed overheads requirement).
  <https://www.handbook.fca.org.uk/handbook/MIFIDPRU/4/>
- Basel Committee on Banking Supervision, *Basel III: A global regulatory
  framework for more resilient banks and banking systems* (rev. June 2011),
  ¶50 and ¶129. <https://www.bis.org/publ/bcbs189.pdf>
- FINRA, *Net Capital Requirements for Brokers or Dealers* (SEA Rule 15c3-1
  interpretations) and SEA Rule 17a-11 interpretations.
  <https://www.finra.org/rules-guidance/guidance/interpretations-financial-operational-rules>

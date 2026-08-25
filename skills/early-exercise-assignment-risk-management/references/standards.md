# Standards for Early Exercise Assignment Risk Management

## Engineering standards enforced by this skill

| Metric | Engineering Standard |
|---|---|
| Exercise-style gate | System MUST distinguish American-style (early exercisable) from European-style (exercisable only at expiration) contracts, and MUST reject an unrecognised style rather than defaulting it. |
| Settlement independence | Exercise style MUST NOT be inferred from settlement method, or vice versa. OEX (S&P 100) is American-style *and* cash-settled; XEO on the same index is European-style. |
| Ex-dividend call test | Short American calls MUST be closed or rolled before the last cum-dividend session's exercise cutoff when the early-exercise test is satisfied. Exact test: $D > p_{ex} + K(1 - e^{-r\tau})$. Fallback screen $D > \text{Extrinsic}_{\text{cum-div}}$ is conservative (over-flags) and MUST be labelled as a screen, not a verdict. |
| Dividend relevance | A dividend whose ex-date falls after the option's expiration MUST be excluded from the call test — it cannot be captured by exercising that contract. |
| Parity test | ITM short options whose extrinsic value falls to or below $\max(\$0.05,\ 0.0005 \times K)$ MUST be flagged, for calls and puts alike, independently of any dividend. |
| Quote convention | Extrinsic value SHOULD be computed from the **bid**. The holder's alternative to exercising is selling, which realises the bid; the mid overstates the buffer. |
| Risk output | The engine MUST NOT publish a probability of assignment. It publishes an ordinal severity score. |

## Regulatory and market-structure touchpoints

| Fact used | Source | Applicability |
|---|---|---|
| Assignment notices are allocated by the member on a FIFO basis, by random selection, or by another FINRA-approved equally-random method; members must obtain FINRA approval for the method and disclose it to customers in writing. | FINRA Rule 2360(b)(23)(C); FINRA Regulatory Notice 11-35 (July 2011) — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/2360>, <https://www.finra.org/rules-guidance/notices/11-35> | US FINRA members, exchange-listed options. This is why an individual short's assignment cannot be derived from the exercise economics alone. |
| "Option holders who hold expiring options have until 5:30 p.m. Eastern Time (ET) on the day of expiration to make a final exercise decision"; members may set an earlier deadline but may not accept instructions after 5:30 p.m. ET. | FINRA Rule 2360(b)(23)(A); FINRA Information Notice, 3 Feb 2021, "Exercise Cut-Off Time for Expiring Options" — <https://www.finra.org/rules-guidance/notices/information-notice-020321> | **Expiring** options only. For early exercise on an ordinary session the operative deadline is the clearing member's own cutoff, which this skill does not model. |
| Expiring standardized equity options in the money by $0.01 or more are exercised automatically under OCC's Exercise-by-Exception procedure unless contrary instructions are given. | OCC Rule 805 (Exercise-by-Exception); FINRA Regulatory Notice 10-36 (Contrary Exercise Advice) — <https://www.finra.org/rules-guidance/notices/10-36> | Expiration-day mechanics. Referenced here as context; the expiry-day exposure itself belongs to `options-pin-risk-management-at-expiry`. |
| Since the T+1 transition on 28 May 2024, the ex-dividend date is the record date (previously one business day earlier under T+2). | SEC T+1 final rule, compliance date 28 May 2024 — SEC Investor Bulletin, <https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin>; DTCC T+1 Dividend Processing FAQ, <https://www.dtcc.com/-/media/Files/PDFs/T2/T1-Dividend-Processing-FAQ.pdf> | US markets. Fixes the exercise decision on the **session before the ex-date**: exercise settles T+1, landing the exerciser on the record-date books. |

## Quantitative reference

| Result | Source |
|---|---|
| Early exercise of an American call immediately before an ex-dividend date is optimal iff $D > K\left(1 - B(t,T)\right) + p$, equivalently $D > \text{TV}_{ex}$, where $p$ is the same-strike same-expiry put and $\text{TV}_{ex}$ is the call's time value at the ex-dividend underlying price. | Merton, "Theory of Rational Option Pricing", *Bell Journal of Economics and Management Science*, 1973. Standard textbook statement, e.g. Kwok, *Mathematical Models of Financial Derivatives*, ch. 5 — <https://www.math.hkust.edu.hk/~maykwok/courses/ma571/06_07/Kwok_Chap_5.pdf> |
| American put early exercise is driven by interest on the strike: the early exercise premium is positive whenever the risk-free rate is positive. Put extrinsic $= c + PV(D) - K(1 - e^{-r\tau})$, so the exercise boundary is where extrinsic reaches zero. | Put-call parity; Merton (1973). Empirical early-exercise-premium evidence for American vs European index options: Li, "An empirical study on the early exercise premium of American options: Evidence from OEX and XEO options", *Journal of Futures Markets*, 2024 — <https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22508> |

## Limitations — not modelled here

- Borrow cost, hard-to-borrow status, and recall risk, which can drive call exercise absent any dividend.
- The ex-date price drop and its effect on the surviving short position.
- The wildcard option in cash-settled American index options (Fleming & Whaley, 1994), which adds exercise value beyond the tests above.
- Non-US jurisdictions: allocation methods, cutoff times, and ex-date conventions outside the US are set by the local clearing house and are not covered by the sources above.

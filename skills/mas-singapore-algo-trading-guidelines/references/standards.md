# Standards for Singapore Algorithmic Trading Pre-Trade Controls

All statements below were verified on **2026-08-25**. Thresholds are SGX-published
values or firm-set controls, as marked. Nothing here is a MAS-published trading
threshold, because MAS publishes none.

## What Singapore does NOT require

| Claim frequently made | Status |
|---|---|
| A MAS-issued algorithm registration number / "MAS Algorithm Identifier" | **Does not exist.** MAS operates no algorithm registration regime and issues no algorithm identifiers. There is no Singapore equivalent of SEBI's exchange-assigned Algo-ID. |
| MAS Guidelines "SFA 04-G08" as the algorithmic trading framework | **Misattributed.** SFA 04-G08 is the *Guidelines on Liquidity Risk Management Practices (Fund Management Companies)*. It has nothing to do with algorithmic trading. |
| A MAS-mandated $\pm 10\%$ order price collar | **Misattributed.** The 10% figure is the SGX-ST **circuit breaker** band, an exchange mechanism on potential *trade* prices, not a MAS rule and not an order-entry collar. |

## What actually binds an SGX algorithmic order

| Control | Source | Requirement | Threshold set by |
|---|---|---|---|
| Entity licensing | Securities and Futures Act 2001 (Singapore) | Dealing in capital markets products requires a Capital Markets Services licence or an exemption. Market misconduct provisions bind algorithmic order flow as they bind manual flow. | Statute (no numeric threshold) |
| Approved Trader registration | SGX Futures Trading Rules 2.13.2, 2.13.4 | Approved Traders and Registered Representatives must be registered with the Exchange before executing trades; the Exchange maintains the register. Algorithmic flow still answers to a registered natural person. | Exchange (no numeric threshold) |
| Pre-execution risk limits | SGX Futures Trading Rules 3.9.1(3); Practice Note 3.9.1(3) (Pre-Execution Checks) | Clearing Members must set pre-execution limits on their trading participants. Orders are checked at the Clearing Member's hosted system or by SGX's exchange-hosted Pre-Trade Risk Controls module. | **Firm and Clearing Member.** SGX publishes no figure. |
| Forced Order Range | SGX-ST Practice Note 8.6; Regulatory Notice 11.4.2(g) (Application of the Force Key) | An order priced outside the Forced Order Range must be confirmed with the Force Key before it may be submitted. It is a fat-finger control, not a prohibition. | Exchange. Published as $\pm 30$ bids for stocks with bid sizes below 0.20, and for ETFs and debentures at all bid sizes; **varies by product class and bid size**. |
| Circuit breaker (securities) | SGX-ST Rule 8.14; Regulatory Notice 8.14.1; Practice Note 8.10A | A price band around a reference price. A Cooling-Off Period is triggered when an incoming order seeks to match, wholly or partly, with an existing order at a price outside the band; the incoming order is not matched outside the band, and the quantity left unfilled at the commencement of the Cooling-Off Period is rejected. | Exchange. **10%** either side; **5-minute** Cooling-Off Period. |
| Circuit breaker reference price | SGX-ST Regulatory Notice 8.14.1 | The last traded price **at least five minutes earlier** — a lagged price, not the current mid and not the current last done. | Exchange |
| Circuit breaker eligibility | SGX-ST Regulatory Notice 8.14.1 | Assessed **daily**. The instrument's reference price at the start of the Market Day must be at least **0.50** in its underlying currency (**JPY 500** for yen-denominated instruments). Applies during continuous trading; not during the opening and closing routines. | Exchange |
| Price limits (derivatives) | SGX Futures Trading Rules 4.1.15 (Price Limits and Cooling Off); individual contract specifications | The Exchange may prescribe Price Limits for particular Contracts. **Per contract** — the securities 10% figure does not carry across. | Exchange, per contract |
| Automated trading controls | SGX RegCo Algorithmic Trading Regulatory Guide; SGX RegCo consultation of 21 September 2023, whose amendments formalised key aspects of the Guide into the Futures Trading Rules and SGX-ST Rules | Pre-deployment testing, risk controls and the ability to withdraw unexecuted orders. | Substance prescribed; **values firm-set** |
| Order message rate ceiling | Firm control | Not an SGX-published number. Calibrate to the algorithm and the gateway's throttle. | **Firm** |

## Engineering consequences

| Requirement | Engineering standard |
|---|---|
| Threshold comparison | Compare **unrounded**; round only for reporting. Rounding a 10.0049% deviation to 10.00% before testing `> 10.0` approves a genuine breach. |
| Band inclusivity | Trading must be within **or at** the thresholds, so a breach requires the price to be strictly outside the band. |
| Non-finite input | Reject NaN and infinity before any comparison. `float('nan') > limit` is `False`, so a NaN price passes every ceiling. |
| Quantity | Reject non-positive quantities. A negative quantity produces a negative order value that passes every value ceiling. |
| Currency | Never compare an order value against a ceiling denominated in another currency. SGX lists counters in several currencies. |
| Unevaluated checks | Report `None`, never `0.0`. A report that carries a figure for a check that never ran is an audit trail that lies. |
| Missing inputs | Resolve conservatively — unknown marketability and unknown circuit breaker eligibility both resolve toward the check applying, so an absent field can never make a breaching order look safe. |

## Sources

- Securities and Futures Act 2001 (Singapore) — https://sso.agc.gov.sg/Act/SFA2001
- MAS Guidelines index (confirms SFA 04-G08 is Liquidity Risk Management Practices for Fund Management Companies) — https://www.mas.gov.sg/regulation/guidelines/guidelines-sfa-04-g08-liquidity-risk-management-practices-for-fund-managers
- MAS Financial Institutions Directory (authoritative source for CMS licence status) — https://eservices.mas.gov.sg/fid
- SGX-ST Rule 8.14, Circuit Breakers and Cooling-Off Periods — https://rulebook.sgx.com/rulebook/814-circuit-breakers-and-cooling-periods
- SGX-ST Regulatory Notice 8.14.1, Circuit Breaker — https://rulebook.sgx.com/rulebook/regulatory-notice-8141-circuit-breaker
- SGX-ST Practice Note 8.10A, Circuit Breaker — https://rulebook.sgx.com/rulebook/practice-note-810a-circuit-breaker
- SGX-ST Practice Note 8.6, Application of the Forced Order Range — https://rulebook.sgx.com/rulebook/practice-note-86-application-forced-order-range
- SGX-ST Regulatory Notice 11.4.2(g), Application of the Force Key — https://rulebook.sgx.com/rulebook/regulatory-notice-1142g-application-force-key
- SGX Futures Trading Rules, Practice Note 3.9.1(3), Pre-Execution Checks — https://rulebook.sgx.com/rulebook/practice-note-3913-pre-execution-checks
- SGX Futures Trading Rule 4.1.15, Price Limits and Cooling Off — https://rulebook.sgx.com/rulebook/4115-price-limits-and-cooling
- SGX Futures Trading Rule 2.13.2, Registration of Approved Traders — https://rulebook.sgx.com/rulebook/2132-registration-approved-traders
- SGX Futures Trading Rule 2.13.4, Register of Approved Traders and Registered Representatives — https://rulebook.sgx.com/rulebook/2134-register-approved-traders-and-registered-representatives
- SGX RegCo guidance for members (Algorithmic Trading Regulatory Guide) — https://regco.sgx.com/guidance-members
- SGX RegCo consultation, 21 September 2023, *Proposed changes to the Futures Trading Rules and other rulebooks* — https://www.rajahtannasia.com/viewpoints/sgx-regco-proposes-changes-to-futures-trading-rules-including-customer-margining-requirements/

**Verification note.** The `rulebook.sgx.com` and `regco.sgx.com` pages listed
above are the primary sources and were located by title and rule number, but
they refuse automated retrieval. The rule *text* relied on here was read from
search-indexed extracts of those same pages, cross-checked against SGX's own
announcement of the circuit breaker regime and contemporaneous member circulars.
Confirm rule numbering and current thresholds directly in the rulebook before
relying on any figure in production.

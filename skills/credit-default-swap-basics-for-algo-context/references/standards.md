# Standards for Credit Default Swap Basics

Primary sources (consulted 2026-08-22; SNAC coupons, ISDA recovery
conventions and the accrued-premium rebate re-verified 2026-08-23):

- **ISDA Big Bang Protocol** (ISDA, April 2009): https://www.isda.org/traditional-protocol/big-bang-protocol/
- **ISDA CDS Standard Model** (official code + conventions): https://www.cdsmodel.com/
- **IMF**: "The Pricing of Credit Default Swaps During Distress": https://www.elibrary.imf.org/view/journals/001/2006/254/article-A001-en.xml
- **S&P Dow Jones Indices — iTraxx**: https://www.spglobal.com/spdji/en/landing/topic/itraxx/
- **ISDA — Standard CDS Examples** (worked cash-settlement examples): https://www.cdsmodel.com/assets/cds-model/docs/Standard%20CDS%20Examples.pdf

| Metric | Engineering Standard | Source |
|---|---|---|
| Recovery rate baseline | Senior unsecured CDS defaults to $R = 40\%$ (pricing convention of the ISDA CDS Standard Model; not a regulatory mandate). The paired subordinated convention is $R = 20\%$. Set the constructor explicitly for subordinated exposures — the default is the senior figure. | cdsmodel.com; ISDA upfront-conversion convention |
| Standard coupons | North American corporate CDS (SNAC) trade at fixed 100 bps (IG) / 500 bps (HY) coupons with upfront settlement of the spread difference. Other regions/asset classes use different grids (e.g. 25/100/500/1000) — verify per market before assuming 100/500. | ISDA Big Bang Protocol (2009) |
| Upfront methodology | This engine computes an INDICATIVE upfront via a continuous-annuity RPV01. Settlement-matching conversion MUST use the ISDA CDS Standard Model (quarterly premiums on IMM dates — Mar/Jun/Sep 20 — Act/360 accrual, ISDA curve conventions). | cdsmodel.com; ISDA standardisation |
| Hazard rate method | Flat credit-triangle $\lambda = s_{par}/(1-R)$: a textbook approximation (Hull; O'Kane) valid away from distress; no curve stripping. | Standard credit-derivatives literature |
| Accrued premium | ISDA cash settlement = clean upfront − accrued premium since the last IMM date, which the protection seller rebates to the buyer (the buyer then pays a full quarterly coupon on the next IMM date). This engine returns the CLEAN figure only; the accrued term is not modelled. | ISDA Standard CDS Examples (cdsmodel.com) |
| Negative discount rates | $(r + \lambda) < 0$ is a well-defined annuity, $(e^{|r+\lambda|T} - 1)/|r+\lambda| > T$, not the $T$ limit. Negative policy rates (EUR/CHF/JPY, 2014–2022) against tight IG hazards make this reachable in practice. | Calculus of the annuity integral; policy-rate history |
| Day count | Actual/360 applies to the ISDA Standard Model's quarterly premium legs; this engine's continuous approximation does not implement day counting — do not mix the two when reconciling. | cdsmodel.com |
| Distressed classification | ~1000 bps is the informal distress boundary (fair spreads converge toward ~1000 bps as default becomes imminent); 500 bps is the standard HY coupon and cannot be the boundary. Tier cuts are desk conventions, not codified rules. | IMF working paper; market convention |
| Cross-asset signals | Z-score spike detection requires genuine dispersion ($\sigma > 0$) and at least two observations; flat histories and short samples are data errors, not NEUTRAL-by-mathematics signals. | Engineering requirement |

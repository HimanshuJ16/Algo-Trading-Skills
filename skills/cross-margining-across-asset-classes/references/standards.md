# Standards for Cross-Margining Across Asset Classes

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Estimator Status | Output MUST be labelled as an estimate. Clearing houses do not compute cross-margin by correlation-aggregating standalone margins, so the figure MUST be reconciled against the CCP or clearing-broker requirement before collateral is released. |
| Offset Eligibility | Offsets MUST only be applied to asset pairs explicitly registered against an active arrangement (CME-OCC, CME-FICC/GSD). Unregistered pairs MUST receive no offset credit (`default_correlation = 1.0`) and MUST be reported in `unregistered_pairs`. |
| Correlation Consistency | Pairwise offsets that imply a negative portfolio variance MUST raise, not be clamped to zero. A non positive semi-definite offset set would otherwise report a fabricated near-total margin saving. |
| Input Validation | Non-finite, negative, or duplicated asset-class margin components MUST be rejected before aggregation. |
| Minimum Margin Floor | A floor of `minimum_floor_pct` × standalone sum (default 20%) is applied as an **internal model-risk guard**. This is a configurable prudential parameter with no clearing house or regulatory source — see the caveat below. It MUST NOT be represented to a risk committee or regulator as a clearing requirement. |
| Audit Trail | Every applied offset, its program attribution, the floor in force, and any unregistered pair MUST be recorded in the audit report. |
| Recalculation Cadence | Cross-margin savings SHOULD be recalculated whenever the underlying standalone margins change (order fills, mark-to-market moves, CCP parameter updates). Note that offset credits themselves change only when the clearing arrangement changes, not tick-by-tick. |

## Caveat on the 20% floor

No verifiable source was found for a percentage-of-standalone-margin floor imposed
by CME, OCC, FICC, the CFTC, or the SEC. Real minimum-margin mechanisms are
structured per-contract or per-position rather than as a fraction of the
un-offset sum — for example FINRA Rule 4210(g)'s portfolio-margin risk-based
minimum of \$0.375 per unit of deliverable per short option contract (\$37.50 for
a standard 100-multiplier contract), and SPAN's short option minimum. Treat the
20% default as this module's own conservatism dial, calibrate it to your model
risk appetite, and set it to `0.0` if you want the unfloored aggregation.

## Methodology provenance

| Component | Source | Notes |
|---|---|---|
| Aggregation formula $\sqrt{\sum IM_r^2 + \sum_{r \neq s} \psi_{rs} IM_r IM_s}$ | ISDA SIMM Methodology (v2.4 §  aggregation across risk classes; same shape in later versions) — https://www.isda.org/a/CeggE/ISDA-SIMM-v2.4-PUBLIC.pdf | Used here across asset classes / clearing houses instead of SIMM risk classes. Note SIMM's final step across *product* classes is a plain sum with no offset. |
| CME SPAN 2 | CME SPAN 2 Margin Framework — https://www.cmegroup.com/clearing/files/cme-span-2-margin-framework.pdf | Historical VaR (≥10y lookback) plus a stress component over thousands of scenarios; cross-model offsets are implied from product covariance, not from aggregating standalone margins. |
| OCC STANS | OCC Margin Methodology — https://www.theocc.com/risk-management/margin-methodology | Full-portfolio Monte Carlo, base requirement from a 99% Expected Shortfall measure at a two-day horizon. |
| CME-OCC cross-margin program | OCC Cross-Margin Programs — https://www.theocc.com/risk-management/cross-margin-programs | Long-standing arrangement between OCC and CME for offsetting positions. |
| CME-FICC/GSD cross-margin arrangement | CME FAQ: CME-FICC Cross-Margining Arrangement expansion — https://www.cmegroup.com/trading/interest-rates/cleared-otc/faq-cme-ficc-cross-margining-arrangement-expansion.html | Enhanced clearing-member arrangement live January 2024 for US Treasury securities vs. CME interest rate futures. Expansion to end-user clients was filed September 2025 and has since received SEC and CFTC approval; participation requires the same dually-registered FCM/BD at both clearing houses and a signed cross-margin participant agreement. Confirm current scope with CME/DTCC before relying on end-user eligibility. |
| FINRA portfolio-margin risk-based minimum | FINRA Rule 4210(g) — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210 | Cited only as a contrast to the percentage floor used here; it is a per-contract minimum, not a percentage of standalone margin. |

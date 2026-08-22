# Standards for Single-Name Concentration Risk

## What is actually mandated

Nothing in this table is a universal "5% of NAV" rule. The regimes below constrain
concentration in narrower, conditional ways, and each applies only to firms and funds
within its own scope. Confirm applicability before treating any figure as binding.

| Source | Jurisdiction / Scope | What it actually requires | Status |
|---|---|---|---|
| Investment Company Act of 1940 §5(b)(1) (15 U.S.C. §80a-5(b)(1)) | US registered investment companies electing "diversified" status | At least 75% of total assets must consist of cash/cash items, Government securities, other investment companies' securities, and other securities — with the "other securities" bucket limited, per issuer, to 5% of total assets and to 10% of the issuer's outstanding voting securities. | Mandatory for funds electing diversified status. Note the 5% applies **within** the 75% bucket; the remaining 25% is unconstrained by this test. Not a portfolio-wide 5% cap, and not applicable to hedge funds or proprietary trading. |
| UCITS Directive 2009/65/EC Art. 52(1)–(2) | EU/EEA UCITS funds | Max 5% of assets in transferable securities or money market instruments issued by the same body. Member States may raise this to 10%, in which case the total of all holdings exceeding 5% must not exceed 40% of assets (the "5/10/40 rule"). | Mandatory for UCITS. Does not apply to non-UCITS vehicles. |
| SEC Rule 15c3-5 (17 CFR 240.15c3-5) | US broker-dealers with market access, and those providing customers access via their MPID | Financial risk controls "reasonably designed to systematically limit the financial exposure" that "prevent the entry of orders" exceeding pre-set credit or capital thresholds, by rejecting such orders. Controls must be under the broker-dealer's direct and exclusive control (§(d)(1)). | Mandatory for in-scope broker-dealers. Prescribes automated pre-trade rejection but sets **no** specific concentration percentage — thresholds are firm-determined. |
| MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589 Art. 15 | EU/EEA investment firms engaged in algorithmic trading | Pre-trade controls on order entry including maximum order values and maximum order volumes (Art. 15(1)(b)–(c)); firms "shall immediately include all orders sent to a trading venue into the calculation of the pre-trade limits" (Art. 15(2)); orders risking the firm's own risk thresholds must be automatically blocked or cancelled, with controls applied "where appropriate, on exposures to individual clients, financial instruments, traders, trading desks or the investment firm as a whole" (Art. 15(5)). | Mandatory for in-scope firms. Again sets no numeric concentration percentage. |
| SEC Rule 10b-18 (17 CFR 240.10b-18(b)(4)) | US issuers repurchasing their own shares | Safe-harbour volume condition: daily Rule 10b-18 purchases must not exceed 25% of ADTV, where ADTV is measured over the four calendar weeks preceding the week of the purchase. One block purchase per week may be made instead. | A non-exclusive **safe harbour** for issuer buybacks, not a general trading limit. Cited only as the nearest regulatory precedent for an ADV-percentage cap and for its ADV lookback definition. |

## Engineering defaults used by this skill

These are risk-policy defaults, not regulatory thresholds. Configure them to the firm's
own mandate and document the basis.

| Setting | Default | Basis |
|---|---|---|
| Single-name absolute NAV cap | 5% of NAV | Convention echoing the ICA'40 and UCITS 5% issuer figures. Applied here to absolute exposure across the whole portfolio, which is **stricter** than either rule. |
| Order size vs ADV | 10% of ADV | Market-practice heuristic for limiting market impact. No regulator sets this figure; the only comparable rule (10b-18) uses 25% in a narrower context. Calibrate against measured impact rather than adopting it as given. |
| ADV lookback window | 20 trading days | A configuration choice, not a definition. Regimes differ — 10b-18 uses four calendar weeks, and venue or vendor ADV series may use other windows. Ensure the window matches the one the policy was calibrated on. |
| Pre-trade enforcement | Synchronous, before the order message is sent | Aligns with SEC Rule 15c3-5 and RTS 6 Art. 15 for in-scope firms; sound engineering practice regardless. |

## Sources

- 15 U.S.C. §80a-5(b)(1) — https://www.law.cornell.edu/uscode/text/15/80a-5
- Directive 2009/65/EC (UCITS IV) Art. 52 — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32009L0065
- 17 CFR §240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5
- Commission Delegated Regulation (EU) 2017/589 (RTS 6) Art. 15 — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589
- 17 CFR §240.10b-18 — https://www.law.cornell.edu/cfr/text/17/240.10b-18

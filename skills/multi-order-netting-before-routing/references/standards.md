# Standards for Multi-Order Netting Before Routing

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Batch validation | An order in another symbol, a repeated `order_id`, an unrecognised `side`, or a non-positive/non-integer quantity MUST raise. A side filter MUST NOT be used as validation: it silently discards anything it does not match. |
| Internal match price | $P_{\text{mid}} = (P_{\text{bid}} + P_{\text{ask}}) / 2$, computed in exact decimal arithmetic and **not** rounded to the quoting increment. Rounding transfers half a cent per share to one side on every cross. |
| Reference-price integrity | The engine MUST refuse to cross against a quote older than a configured limit, and against a crossed book ($P_{\text{bid}} > P_{\text{ask}}$). On refusal it MUST emit no residual order — the residual size came from the same rejected quote. A locked book ($P_{\text{bid}} = P_{\text{ask}}$) is valid and yields a zero spread saving. |
| Quote age provenance | A quote with no timestamp MUST be reported as unaged (`QUOTE_AGE_UNVERIFIED`), never assumed fresh. |
| Limit-price eligibility | A buy crosses only where $P_{\text{limit}} \ge P_{\text{mid}}$, a sell only where $P_{\text{limit}} \le P_{\text{mid}}$. An ineligible order MUST be excluded from the cross **and** from the net residual, and returned to the caller. |
| Residual pricing | The residual order MUST carry the most conservative limit among its contributing orders (lowest for a buy, highest for a sell). An unpriced (`MARKET`) residual MUST be flagged. |
| Matched quantity | $Q_{\text{matched}} = \min(Q_{\text{buy}}^{\text{eligible}}, Q_{\text{sell}}^{\text{eligible}})$ — over eligible, not submitted, quantity. |
| Net residual | $Q_{\text{residual}} = Q_{\text{dominant}}^{\text{eligible}} - Q_{\text{matched}}$. Only one side can be non-zero. |
| Allocation | The matched quantity MUST be allocated by an explicit, disclosed policy that sums exactly to $Q_{\text{matched}}$ in whole shares. Pro-rata MUST use floor plus largest remainder with a deterministic tie-break; arrival order MUST NOT be the implicit default. |
| Ownership classification | The cross MUST be classified over the participants that actually received an internal fill. Unknown beneficial ownership MUST fail safe to "reportable", never to "transfer". |
| Cost model | Fee saving $= 2 \cdot Q_{\text{matched}} \cdot \text{fee}$ and spread saving $= Q_{\text{matched}} \cdot \text{spread}$ are estimates conditional on **both** sides having removed liquidity at the touch. Costs that survive internalisation MUST be subtracted on a reportable cross, or their absence disclosed. |
| Money arithmetic | All price, fee and saving arithmetic MUST use an exact decimal type. Half-cent mids and per-share fees accumulated in binary floats do not reconcile against a clearing statement. |

## Regulatory touchpoints

All sources consulted 2026-08-26. Jurisdiction is stated per row; **none of these rules is universal**, and the engine hard-codes no threshold from any of them.

### United States — the cross itself

| Rule | Jurisdiction / applicability | What it says | Source |
|---|---|---|---|
| **FINRA Rule 5210** | US, FINRA members | No member shall publish or circulate any communication "which purports to report any transaction as a purchase or sale of any security unless such member believes that such transaction was a bona fide purchase or sale." Supplementary Material .01 extends this to transaction reports the member has no reasonable basis to believe are bona fide. | [FINRA Rule 5210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210) |
| **FINRA Rule 5210, Supplementary Material .02** | US, FINRA members | Self-trades — "transactions in a security resulting from the unintentional interaction of orders originating from the same firm that involve no change in the beneficial ownership of the security" — are generally bona fide, but members must have policies "reasonably designed to review their trading activity for, and prevent, a pattern or practice of self-trades resulting from orders originating from a single algorithm or trading desk, or related algorithms or trading desks." **Pre-routing netting is one such control**; it is not a substitute for venue-level SMP, which covers the resting book this engine cannot see. | [FINRA Rule 5210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210); [Regulatory Notice 14-28](https://www.finra.org/rules-guidance/notices/14-28) |
| **FINRA Rule 6380A** | US, FINRA members, OTC trades in NMS stocks | Reportable transactions must be reported to a Trade Reporting Facility "as soon as practicable, but no later than 10 seconds after execution"; anything later is designated late. TRF operating hours open at 04:00 ET from 30 March 2026. | [FINRA Rule 6380A](https://www.finra.org/rules-guidance/rulebooks/finra-rules/6380a); [Regulatory Notice 25-15](https://www.finra.org/rules-guidance/notices/25-15) |
| **FINRA Rule 5310 + Supplementary Material .09** | US, FINRA members, customer orders | 5310(a)(1): "use reasonable diligence to ascertain the best market for the subject security … so that the resultant price to the customer is as favorable as possible under prevailing market conditions." .09(a): a member that internalises customer order flow "must have procedures in place to ensure the member periodically conducts regular and rigorous reviews of the quality of the executions of its customers' orders **if it does not conduct an order-by-order review**." .09(b) requires comparing internalised execution quality against competing markets. | [FINRA Rule 5310](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310) |
| **Reg NMS Rule 611 (17 CFR 242.611)** | US, NMS stocks, all trading centers | Every trading center must maintain policies reasonably designed to prevent trade-throughs of protected quotations. "Trading center" reaches a broker-dealer that executes orders internally, as principal or by crossing orders as agent — so an internal crossing engine is in scope. A mid derived from a current quote sits inside the NBBO and cannot trade through; a mid derived from a *stale* quote can. | [SEC, Rule 611 of Regulation NMS](https://www.sec.gov/spotlight/emsac/memo-rule-611-regulation-nms.pdf); [Division of Market Regulation Rule 611 FAQ](https://www.sec.gov/divisions/marketreg/rule611faq.pdf) |
| **Reg NMS Rule 612 (17 CFR 242.612)** | US, NMS stocks | "No national securities exchange, national securities association, alternative trading system, vendor, or broker or dealer shall display, rank, or accept from any person a bid or offer, an order, or an indication of interest" in an increment finer than the applicable minimum. The prohibition is on **display, ranking and acceptance of orders and quotations** — executions are not covered, which is why a half-cent midpoint fill is permissible. | [17 CFR § 242.612](https://www.law.cornell.edu/cfr/text/17/242.612) |
| **Advisers Act § 206(3) / Rule 206(3)-2** | US, SEC-registered investment advisers | Rule 206(3)-2 permits blanket prospective client consent for agency cross transactions subject to written consent, per-transaction confirmations, annual disclosure and a revocation notice — but **not** where the adviser "recommended the transaction to both any advisory client and another person on the other side of the transaction." An internal netting engine acting on the adviser's own opposing signals is exactly that case, so the blanket-consent route is unavailable. | [17 CFR § 275.206(3)-2](https://www.law.cornell.edu/cfr/text/17/275.206(3)-2); [SEC OCIE Risk Alert, Principal and Agency Cross Trading](https://www.sec.gov/files/OCIE%20Risk%20Alert%20-%20Principal%20and%20Agency%20Cross%20Trading.pdf) |

### United States — what a reportable cross still costs

The saving from not touching an exchange is the **access/taker fee** and the spread. It is not the whole fee stack. Where the cross moves stock between beneficial owners and is therefore reported, these survive:

| Cost | Basis | Current figure | Source |
|---|---|---|---|
| Section 31 regulatory transaction fee | Applies to covered **sales** of equity securities required to be promptly reported to the ORF, ADF or a FINRA TRF. FINRA charges it to the clearing firm on the sell side; where no member is on the sell side, to the member on the buy side. | **$20.60 per $1,000,000** of covered sale proceeds, effective 4 April 2026. | [FINRA Information Notice 03/17/26](https://www.finra.org/rules-guidance/notices/information-notice-20260317); [SEC, Section 31 Transaction Fees](https://www.sec.gov/rules-regulations/fee-rate-advisories/section-31-transaction-fees-basic-information-firms) |
| FINRA Trading Activity Fee (TAF) | Per-share charge on member **sales** of covered equity securities, subject to a per-trade cap. | Rate and cap are reset by FINRA; read the current schedule rather than hard-coding. | [FINRA, Regulatory Transaction Fees](https://www.finra.org/taxonomy/term/1378) |
| TRF media/reporting and clearing charges | Per-report and per-side venue and clearing charges on the internalised print. | Firm- and facility-specific. | Facility fee schedule |

**Worked conversion into the engine's `retained_internalization_cost_per_share_usd`.** At a $150.05 mid, the Section 31 fee on the sell side is $150.05 \times 20.60 \times 10^{-6} \approx \$0.00309$ per matched share. Against an avoided access fee of $0.003 on each of two sides ($0.006 per matched share), the Section 31 fee alone consumes **roughly half** the fee saving before TAF and TRF charges are counted. Add the TAF and facility charges from your own current schedules; the engine takes one combined per-matched-share figure and reports `INTERNALIZATION_COST_UNMODELLED` when it is omitted on a reportable cross.

*The spread saving is unaffected by any of this — it is a price improvement, not a fee.*

### European Union

Mid-point matching against another venue's price is the **reference price waiver**, MiFIR Article 4(1)(a), and it is capped: Article 5 requires venues to suspend the waiver in an instrument whose EU-wide trading under it exceeds **7 %** of total Union volume, on the basis of ESMA-published data, for three months. The monitored period began 29 September 2025. This repo covers the mechanism in `esma-double-volume-cap-mechanism`; that skill's `references/standards.md` carries the verified Article 5 text, cadence and suspension mechanics, and is the source of record here rather than a duplicate table.

The distinction that matters for this skill: the waiver regime governs a **trading venue's** dark midpoint matching. A firm netting its own opposing internal orders before routing is not thereby operating a venue — but a system that matches third-party buying and selling interest on a multilateral basis may be, and that is a licensing question (MTF/SI perimeter) to answer before scaling an internal crossing network. See `algorithmic-trading-firm-licensing-thresholds`.

## Confidence and limitations

- **The engine classifies; it does not advise.** `cross_type` and `requires_execution_report` are derived purely from the `beneficial_owner_id` values supplied. Whether a given entity structure constitutes one beneficial owner is a legal determination, and the engine's fail-safe (unknown → reportable) exists because guessing the other way is the dangerous error.
- **Section 31 rate.** $20.60 per million is the rate effective 4 April 2026 per the FINRA information notice. The SEC resets this rate; it has been changed mid-year and has previously been set to zero. Read the current advisory rather than trusting this figure.
- **TAF rate and cap** were not retrieved from a primary schedule during this review and are deliberately not quoted. Take them from FINRA's current fee schedule.
- **FINRA Rule 5310.09.** A widely repeated secondary claim is that internalising firms *must* conduct order-by-order review. The rule text does not say that: .09(a) requires regular and rigorous reviews **if** the member does not conduct an order-by-order review. Both routes are open; neither is optional.
- **Rule 612 tick tiers.** The $0.005 minimum increment added by the 2024 Reg NMS amendments is not operative — exemptive relief has been extended to the first business day of November 2027. `exchange-tick-size-regime-tracking` carries the verified status; do not derive a quoting increment from this file.
- **Non-US venues.** Nothing above establishes how a given non-US market treats internal crossing, and several jurisdictions regulate it far more tightly than the mid-price arithmetic suggests. Confirm per venue before enabling netting on it.

## Cross-references

- Venue-level self-match prevention, which covers the resting book this engine cannot see: `exchange-self-match-prevention-configuration`
- Post-hoc surveillance for the pattern netting is meant to prevent: `wash-trade-and-spoofing-self-detection`
- Routing the residual once it has been sized: `smart-order-routing-across-venues`, `smart-order-router-failover-on-venue-outage`
- Sizing the residual to the venue's lot and minimum rules: `minimum-fill-size-and-lot-rounding-logic`
- Best-execution evidence for internalised flow: `best-execution-record-keeping-global`
- EU reference price waiver and the Article 5 cap: `esma-double-volume-cap-mechanism`
- US minimum pricing increments and their compliance status: `exchange-tick-size-regime-tracking`

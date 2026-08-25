# Standards for Execution Slippage Attribution

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Decomposition Identity | $\text{IS}_{\text{total}} \equiv \text{IS}_{\text{timing}} + \text{IS}_{\text{sizing}}$ MUST hold **exactly in full precision**, and MUST be verified rather than assumed. After each of the three figures is independently rounded to 2 dp for reporting, the two sides may differ by up to one $0.01$ bps ulp (theoretical bound $3 \times 0.005 = 0.015$ bps); the reported total MUST be the directly computed total, never the sum of the rounded components. |
| Normalisation Denominator | All three components MUST be divided by $P_{\text{decision}}$. Normalising the sizing leg on $P_{\text{arrival}}$ breaks additivity. |
| Notional Weighting | Contribution to canonical IS MUST be expressed on the intended notional $Q_{\text{order}} \times P_{\text{decision}}$, i.e. the per-share cost scaled by $Q_{\text{filled}} / Q_{\text{order}}$. |
| Side Multiplier Standard | Buy orders $(+1)$ and Sell orders $(-1)$ MUST apply the correct sign multiplier, and any other `side` value MUST be rejected — never coerced to a default. |
| Input Validation | Prices MUST be finite and $> 0$; quantities MUST satisfy $0 < Q_{\text{filled}} \le Q_{\text{order}}$; timestamps MUST be timezone-aware ISO-8601 and non-decreasing. Invalid input MUST raise, never yield a "no action" verdict. |
| Driver Ranking | Components MUST be ranked by **cost-signed** magnitude. A favourable (negative-cost) component MUST NOT be reported as a slippage driver. |
| Contribution Shares | Shares MUST be normalised on gross cost $\lvert\text{IS}_{\text{timing}}\rvert + \lvert\text{IS}_{\text{sizing}}\rvert$ so they stay within $[-100\%, +100\%]$ when the legs offset. |
| Basis Point Precision | All slippage components MUST be reported in basis points (bps) with 2 decimal places. |
| Materiality Threshold | The bps threshold separating "driver" from "noise" is a **desk reporting convention**, not a standard or a regulatory figure. It MUST be configurable and MUST be recorded on the report so a verdict is reproducible. |

## Scope boundary

Perold's Implementation Shortfall has four components:

$$\text{IS} = \text{delay cost} + \text{trading cost} + \text{opportunity cost} + \text{explicit fees}$$

This skill measures the first two, on shares that actually filled. **Opportunity cost**
$(Q_{\text{order}} - Q_{\text{filled}}) \times (P_{\text{end}} - P_{\text{decision}})$ requires an
end-of-horizon price this engine is never given, and **explicit fees** (commissions, exchange
fees, taxes, stamp duty) are not ingested. A report with `is_partial_fill` set MUST NOT be
presented as an order's total cost. See `implementation-shortfall-minimization` for the full
four-component shortfall.

## Sources

| Claim | Source | Verified |
|---|---|---|
| IS is the return difference between the paper portfolio and the implemented portfolio; opportunity cost on unexecuted shares is part of it | Perold, A. F. (1988), "The Implementation Shortfall: Paper vs. Reality", *Journal of Portfolio Management* **14**(3), Spring 1988, pp. 4–9, doi:10.3905/jpm.1988.409150 — https://jpm.pm-research.com/content/14/3/4 | Citation and pagination confirmed against the publisher's record and the Harvard Business School faculty listing. |
| Expanded decomposition IS = delay + trading + opportunity + fees; delay and trading costs are weighted by **executed** shares; opportunity cost by **unexecuted** shares; the bps denominator is **total (intended) shares × decision price** | Standard CFA Level III trade-cost formulation — https://analystprep.com/study-notes/cfa-level-iii/measurement-and-determination-of-cost-of-trade/ | Component-by-component formulas and the "IS \$ / (Total Shares × Decision Price) × 10,000" denominator confirmed. |
| Delay cost is the value lost between the investment decision and order release; execution cost is the difference between execution price and the price at order release | Kissell Research Group TCA definitions, as documented by MathWorks — https://www.mathworks.com/help/datafeed/post-trade-analysis-metrics-definitions.html | Conceptual definitions confirmed; the page publishes no formulas ("contact the Kissell Research Group"), so no numeric claim is sourced from it. |

### Deliberately not cited

**Almgren & Chriss (2000), "Optimal Execution of Portfolio Transactions", *Journal of Risk*
3(2), pp. 5–39** was previously listed as this skill's framework. It is an *ex-ante* optimal
trading-trajectory model — an efficient frontier trading expected impact cost against
execution variance — not a post-trade attribution framework, and nothing in this engine
implements it. It remains the correct reference for
`execution-algo-parameter-optimization-via-backtest` and
`implementation-shortfall-minimization`.

**Regulatory reporting regimes are deliberately absent.** This skill asserts no
best-execution obligation. Note for anyone tempted to add one: the MiFID II RTS 28
best-execution reporting obligation was removed by the deletion of Article 27(6) MiFID II
via Directive (EU) 2024/790, and ESMA deprioritised supervision of RTS 27 reports from
28 February 2023 — so a citation to "RTS 27/28 requires this" would be outdated. See
`best-execution-record-keeping-global` and `mifid-ii-algo-trading-compliance-eu`.

# Standards for Commodity Carry Cost Modeling

| Metric | Engineering Standard |
|---|---|
| Directionality of the Bound | For a consumption commodity the cost-of-carry relation is an inequality, $F_0 \le (S_0 + U)e^{(r+c)T}$, not an equality. Only the cash-and-carry (futures-rich) side may be reported as an arbitrage. |
| Reverse Carry | A futures price below fair value MUST NOT be reported as an arbitrage. Reverse cash-and-carry requires selling the physical short, which is generally impossible for a consumption commodity. Surface it as a candidate conditional on borrow/inventory availability. |
| Convenience Yield Bounds | Implied $y < 0$ means the upper bound is breached. It MUST raise an inspection alert. The dominant real-world causes are non-synchronous spot/futures quotes, a spot grade or location that is not contract-deliverable, and understated storage or financing costs — check those before treating it as profit. |
| Continuous Compounding | All cost-of-carry and convenience-yield formulas MUST use continuous compounding ($e^{x}$), and $r$ MUST be converted to a continuously compounded rate before use. |
| Maturity Precision | $T$ MUST be an exact year fraction, and MUST share a day-count basis with the quoted rate $r$. ACT/365 is a common default, but it is a convention to state explicitly, not a universal rule — an ACT/360 money-market quote paired with an ACT/365 $T$ biases the result by ~1.4% of the rate. |
| Storage Cost Units | Fixed per-unit storage charges MUST be modelled additively as a present value $U$, not folded into a percentage-of-spot rate. Exchange storage tariffs are set per unit per day, independent of price level. |
| Numerical Validation | Inputs MUST be validated for finiteness as well as sign. NaN passes `<= 0` checks and propagates silently through `math.log` into a NaN price with a valid-looking regime label. |
| Short-Maturity Guard | Implied-yield extraction below roughly one day to expiry MUST be flagged as unreliable; the $1/T$ factor amplifies quote noise into implausible annualised yields. |

## Sources

- Hull, J. C., *Options, Futures, and Other Derivatives*, Ch. 5 "Determination of Forward and Futures Prices" — §5.11 futures prices of consumption assets ($F_0 \le S_0 e^{(r+u)T}$, $F_0 \le (S_0 + U)e^{rT}$) and §5.12 the convenience yield defined by $F_0 e^{yT} = (S_0 + U)e^{rT}$, hence $y \ge 0$.
- ThisMatter, *Futures Prices: Known Income, Cost of Carry, Convenience Yield* — states both storage forms, $F_0 = (S_0 + U)e^{rT}$ and $F_0 = S_0 e^{(r+u)T}$, and that arbitrage "cannot be done for a consumption asset" because it may have a convenience yield that cannot be calculated directly, only inferred. <https://thismatter.com/money/futures/futures-prices-cost-of-carry.htm>
- AnalystPrep, *Commodity Futures and Forwards* (FRM Part 1) — convenience yield as "the additional value that comes with holding the asset rather than having a long forward or futures contract"; a readily available asset has zero convenience yield. <https://analystprep.com/study-notes/frm/part-1/financial-markets-and-products/commodity-futures-and-forwards/>
- CME Group, *Variable Storage Rate* — the mechanism that resets the maximum allowable storage charge on wheat shipping certificates in increments of hundredths of a cent per bushel per day, keyed to nearby spreads as a percentage of financial full carry. <https://www.cmegroup.com/trading/agricultural/grain-and-oilseed/variable-storage-rate.html>
- CBOT Rulebook, Chapter 7 *Delivery Facilities and Procedures* and the product chapters (e.g. Ch. 10 Corn Futures) — storage/premium charges on shipping certificates are capped per bushel per day. <https://www.cmegroup.com/rulebook/CBOT/I/7.pdf>
- CME Group, *Implications of WTI Oil Futures In Backwardation Amid the Supply Crunch* — sustained WTI backwardation with materially positive roll yield, i.e. large positive convenience yields are a persistent market state rather than an arbitrage. <https://www.cmegroup.com/insights/economic-research/2026/implications-of-wti-oil-futures-in-backwardation-amid-the-supply-crunch.html>

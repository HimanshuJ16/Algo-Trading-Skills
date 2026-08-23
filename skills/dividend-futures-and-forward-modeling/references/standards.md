# Standards for Dividend Futures and Forward Modeling

## Contract reference

| Item | Eurex EURO STOXX 50 Index Dividend Futures | CME S&P 500 Annual Dividend Index Futures |
|---|---|---|
| Product ID | **FEXD** | **SDA** |
| Underlying | EURO STOXX 50 DVP (dividend points) | S&P 500 Annual Dividend Points Index (SPXDIVAN) |
| Contract value | EUR 100 per index point | USD 250 per index point |
| Minimum tick | 0.1 points = EUR 10 | 0.05 points = USD 12.50 |
| Settlement | Cash, payable the first exchange day after final settlement | Cash |
| Final settlement basis | "the cumulative total of the relevant **gross** dividends of the constituents of the underlying index" | Accumulation of ordinary **gross** dividends of constituents going ex-dividend within the cumulation period |
| Accrual period | Contract period (quarterly, semi-annual, and annual expiries listed) | 12 months; index resets to zero after the leading December contract expires |
| Special dividends | Determined by the index provider's rules | Excluded from the dividend index |

Product identifiers were verified against the exchanges' own product pages (see Sources). Note that the code **FDBX**, used in earlier versions of this skill, does not correspond to any Eurex dividend futures product; the EURO STOXX 50 index dividend future is FEXD. Related Eurex dividend product IDs include FEDV (EURO STOXX Select Dividend 30), FSBD (STOXX Europe 600 Banks Dividend), and FDIV (DivDAX).

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Discrete dividend model | Single-stock and near-dated index forwards MUST use the discrete dividend present value $\text{PV}(D)$, not a continuous yield $q$. |
| Gross vs net separation | The dividend-futures fair value MUST use **gross ordinary** dividends. Withholding tax MUST be applied only to the cash-and-carry $\text{PV}(D)$/$\text{FV}(D)$, where the physical holder receives net cash. The two measures MUST NOT share one tax-adjusted total. |
| Ex-date vs payment date | Eligibility MUST be determined by the **ex-dividend date**; the cash flow MUST be discounted from the **payment date**. Collapsing the two is a modelling error, not a simplification. |
| Accrual window bounds | The dividend window MUST be bounded at **both** ends: $t^{ex} > \text{accrual start}$ and $t^{ex} \le T$. Already-ex dividends MUST be excluded — including one overstates $\text{PV}(D)$ and produces a false cash-and-carry signal. |
| Special dividends | Special/extraordinary dividends MUST be excluded from the dividend-index accrual while remaining in the forward's $\text{PV}(D)$. |
| Arbitrage tolerance band | The mis-pricing spread $\Delta_{\text{arb}}$ MUST strictly exceed round-trip transaction costs before triggering an arbitrage trade. |
| Asymmetric leg costs | The reverse cash-and-carry (long forward / short spot) MUST carry its own, higher cost threshold: it requires stock borrow, is exposed to recall risk and hard-to-borrow names, and pays **gross** manufactured dividends. A symmetric threshold overstates reverse-leg opportunities. |
| Input finiteness | Non-finite inputs (NaN/Inf) MUST be rejected, not propagated. Every NaN comparison evaluates False, so a NaN silently yields a confident `NO_ARBITRAGE` verdict. |
| Units | All engine outputs are **per unit** (per share or per index point) in the underlying's currency. The venue contract multiplier MUST be applied downstream before sizing. |

## Model limitations

- The dividend-futures fair value implemented here is the **deterministic expected accrual**. It carries no convexity adjustment for stochastic rates and no dividend-volatility term, so it must not be used to price options on dividend futures (e.g. Eurex OEXD).
- Forward pricing assumes a single continuously compounded rate $r$ over $[0, T]$ and perfectly known dividend amounts and dates. Real dividend risk is concentrated in *forecast* error, which this engine does not model.
- Borrow cost for the reverse leg is represented only as a flat threshold, not a term structure.

## Sources

- Eurex, EURO STOXX 50 Index Dividend Futures (FEXD) product page — <https://www.eurex.com/ex-en/markets/did/eqt-idx-div-fut/EURO-STOXX-50-Index-Dividend-Futures-946316>
- Eurex, Equity Index Dividend Futures overview — <https://www.eurex.com/ex-en/markets/did/eqt-idx-div-fut>
- CME Group, S&P 500 Annual Dividend Index Futures contract specifications — <https://www.cmegroup.com/markets/equities/sp/sp-500-annual-dividend-index.contractSpecs.html>
- CME Group, Equity Index Dividend Futures and Options — <https://www.cmegroup.com/markets/equities/files/equity-index-dividend-futures-and-options.pdf>
- CME Group, FAQ: Dividend Index Futures — <https://www.cmegroup.com/articles/faqs/frequently-asked-questions-on-nasdaq-100-and-russell-2000-annual-dividend-index-futures.html>

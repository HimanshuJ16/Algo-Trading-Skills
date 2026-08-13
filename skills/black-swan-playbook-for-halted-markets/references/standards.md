# Institutional Quant Standards: Trading Halts & Black Swans

Institutional quant desks treat Limit Up/Limit Down (LULD) and market-wide circuit breakers (MWCB) not just as regulatory events, but as critical symptoms of liquidity exhaustion and microstructure collapse.

> **Jurisdiction.** The halt mechanics below describe **US NMS equities**. LULD is a National Market System plan; MWCB thresholds are set in exchange rules (NYSE / NYSE American / NYSE Arca Rule 7.12, Nasdaq Rule 4121). Other venues use materially different regimes — NSE/BSE index-based circuit filters and per-scrip price bands, Eurex T7 volatility interruptions, JPX special quotes, ASX trading pauses. Do **not** port the thresholds or the reopening assumptions to a non-US venue without re-deriving them from that venue's rulebook.

## 1. US halt mechanics that drive the playbook

| Mechanism | Trigger | Effect | Scope |
|---|---|---|---|
| LULD limit state | NBB/NBO reaches but does not cross a price band | 15-second limit state; if unresolved, a 5-minute trading pause | Single security |
| LULD reopening | End of the 5-minute pause | Reopening auction at the **primary listing exchange**; trading may not resume until that auction completes and new bands are disseminated | Single security |
| MWCB Level 1 / Level 2 | S&P 500 declines 7% / 13% vs prior close, before 3:25 p.m. ET | 15-minute market-wide halt (no halt if triggered at or after 3:25 p.m. ET) | **All** NMS securities |
| MWCB Level 3 | S&P 500 declines 20% | Trading ends for the remainder of the day, at any time | **All** NMS securities |

Two consequences matter more than the thresholds themselves:

- **A market-wide halt removes the hedge.** An MWCB Level 1/2 halt is a coordinated halt of the cash equity market *and* all US-based equity index futures and options; CME states that products tied to the S&P 500, Nasdaq-100, Dow and Russell 2000 resume 10 minutes after the regulatory halt commenced. Neither an ETF proxy nor an index-futures proxy is tradable while the halt is in force, so proxy hedging is unavailable during an MWCB — a playbook that routes one during a Level 1 halt is routing an order that cannot execute.
- **Level 3 has no reopen.** The "auction resumption" phase of this playbook does not apply to a Level 3 event; the position carries to the next session.

## 2. Microstructure Awareness
- **Avoid Stale Fills:** The immediate cancellation of open working orders upon a HALT signal is paramount. Resting orders persist in the book through a US halt and are eligible interest for the reopening auction, so leaving limit orders active exposes the algo to aggressive adverse selection during the highly volatile re-opening auction. Order entry, modification and cancellation remain permitted during the pause.
- **Limit States:** Algorithms must detect when an asset approaches a Limit State and proactively pause trading to avoid being "pinned" in a halted stock with trapped capital.

## 3. Dynamic Proxy Hedging & Basis Risk
- **Proxy Selection:** During Black Swans, correlations often converge to 1.0. Liquid proxies (index futures, sector ETFs) become the only viable way to manage risk when single-name liquidity vanishes — provided the proxy is itself trading (see §1).
- **Notional, not share count:** A regression beta relates percentage returns, so an equal-notional hedge is required. Hedging `$1` of the asset means shorting `$beta` of the proxy; in units,
  `ProxyUnits = PositionUnits × Beta × (AssetPrice / ProxyPrice)`, and for a futures proxy
  `Contracts = Beta × PositionValue / (IndexLevel × Multiplier)`.
  Sizing on `PositionUnits × Beta` alone is correct only in the degenerate case where both legs trade at the same price.
- **Basis Risk Filtering:** A proxy hedge is only valid if the spread between the proxy and the underlying remains predictable. If basis risk (spread divergence) spikes beyond a critical threshold, the hedge becomes speculative and must be aborted. The comparison must reject non-finite readings explicitly — `NaN > limit` is `False`, so a stale feed silently defeats the gate.

## 4. Fat-Tail Optimization vs VaR
- **Adaptive Risk Limits:** Standard Value-at-Risk (VaR) models, which assume normal (Gaussian) distributions, fail drastically during Black Swans. Institutions use Distributionally Robust Optimization (DRO). Risk limits must dynamically expand to survive the volatility regime without forcing unnecessary fire-sales at the market bottom.
- **Convexity:** Prefer long-volatility proxies (VIX futures, OTM options) to provide structural convexity when standard delta hedging becomes prohibitively expensive due to cost of carry.

## 5. Auction Participation
- **Price Discovery Volatility:** Re-opening auctions feature immense price discovery volatility. Institutional standard dictates calculating an independent Fair Value estimate (using proxy movements during the halt) and placing strict Limit Orders (LOC/MOC equivalents) rather than market orders to control slippage.
- **Simultaneous Unwind:** The proxy hedge must be unwound synchronously with the auction execution to prevent net-new directional exposure — but unwinding on order *submission* leaves the position naked if the auction limit order does not fill. Trigger the unwind from the auction fill notification where the execution stack supports it, and never leave the hedge working once the halted name is trading again.

## Sources

- SEC, *Stock Market Circuit Breakers* (Investor.gov) — MWCB levels, 15-minute halt, 3:25 p.m. rule, Level 3 close: https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers
- NYSE, *Market-Wide Circuit Breakers FAQ* and NYSE / NYSE American / NYSE Arca Rule 7.12: https://www.nyse.com/publicdocs/nyse/NYSE_MWCB_FAQ.pdf
- Nasdaq, *Market Wide Circuit Breaker* (Nasdaq Rule 4121): https://www.nasdaqtrader.com/trader.aspx?id=CircuitBreaker
- LULD Plan (National Market System Plan to Address Extraordinary Market Volatility): https://www.luldplan.com/
- Nasdaq, *Limit Up-Limit Down FAQ* — limit state, 5-minute pause, primary-listing-exchange reopening auction, order handling during the pause: https://www.nasdaqtrader.com/content/MarketRegulation/LULD_FAQ.pdf
- CME Group, *US-Based Equity Index Futures Price Limits: FAQ* — coordinated halt of US equity index futures and options under NYSE Rule 7.12, 10-minute futures resumption, 20% end-of-day: https://www.cmegroup.com/trading/equity-index/us-based-equity-index-futures-price-limits-faq.html

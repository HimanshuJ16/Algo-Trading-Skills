# Institutional Quant Standards: Trading Halts & Black Swans

Institutional quant desks treat Limit Up/Limit Down (LULD) and market-wide circuit breakers (MWCB) not just as regulatory events, but as critical symptoms of liquidity exhaustion and microstructure collapse.

## 1. Microstructure Awareness
- **Avoid Stale Fills:** The immediate cancellation of open working orders upon a HALT signal is paramount. Leaving limit orders active leaves the algo exposed to aggressive adverse selection during the highly volatile re-opening auction.
- **Limit States:** Algorithms must detect when an asset approaches a Limit State and proactively pause trading to avoid being "pinned" in a halted stock with trapped capital.

## 2. Dynamic Proxy Hedging & Basis Risk
- **Proxy Selection:** During Black Swans, correlations often converge to 1.0. Liquid proxies (index futures, sector ETFs) become the only viable way to manage risk when single-name liquidity vanishes.
- **Basis Risk Filtering:** A proxy hedge is only valid if the spread between the proxy and the underlying remains predictable. If basis risk (spread divergence) spikes beyond a critical threshold, the hedge becomes speculative and must be aborted.

## 3. Fat-Tail Optimization vs VaR
- **Adaptive Risk Limits:** Standard Value-at-Risk (VaR) models, which assume normal (Gaussian) distributions, fail drastically during Black Swans. Institutions use Distributionally Robust Optimization (DRO). Risk limits must dynamically expand to survive the volatility regime without forcing unnecessary fire-sales at the market bottom.
- **Convexity:** Prefer long-volatility proxies (VIX futures, OTM options) to provide structural convexity when standard delta hedging becomes prohibitively expensive due to cost of carry.

## 4. Auction Participation
- **Price Discovery Volatility:** Re-opening auctions feature immense price discovery volatility. Institutional standard dictates calculating an independent Fair Value estimate (using proxy movements during the halt) and placing strict Limit Orders (LOC/MOC equivalents) rather than market orders to control slippage.
- **Simultaneous Unwind:** The proxy hedge must be unwound synchronously with the auction execution to prevent net-new directional exposure.

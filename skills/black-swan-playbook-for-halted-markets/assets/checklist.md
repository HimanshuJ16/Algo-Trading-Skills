# Black Swan Pre-Flight Checklist

Use this checklist to ensure your trading infrastructure is prepared for extreme market conditions, LULD halts, and market-wide circuit breakers.

### System Readiness
- [ ] Exchange status feed ingestion is real-time, tested, and reliable.
- [ ] Order cancellation logic verified to execute < 50ms upon HALT signal.
- [ ] Risk limits are configured dynamically (fat-tail aware), not statically fixed.
- [ ] API rate limit monitors are active to prevent order spam during halted states.

### Halt Classification
- [ ] Feed distinguishes single-name LULD pauses from market-wide circuit breakers.
- [ ] Hedging is suppressed on a market-wide halt (proxy ETFs and US index futures are halted too) and the suppression is recorded for post-mortem.
- [ ] Repeated halt status messages for the same symbol are idempotent — no second hedge is fired.
- [ ] Level 3 (20%) handling is defined: no reopening auction, position carries to the next session.

### Proxy Hedging Infrastructure
- [ ] Comprehensive mapping of primary symbols to highly liquid proxy instruments (ETFs, futures).
- [ ] Beta values for proxies are updated continuously based on recent historical windows, and are documented as *return* betas.
- [ ] Live prices for **both** legs are available; hedge size is notional-scaled (`Position × Beta × AssetPrice / ProxyPrice`), never share-count only.
- [ ] Basis risk calculation is active and has defined cut-off thresholds for every proxy pair.
- [ ] Every risk gate rejects `NaN`/`Inf`/missing readings explicitly and fails closed.
- [ ] Symbols with no proxy configuration are excluded from hedging rather than hedged at an assumed beta.
- [ ] Proxy hedge execution logic is tested in paper-trading against simulated halts.

### Auction Participation
- [ ] Fair Value estimation models are running and can price halted assets based on proxy drift.
- [ ] Order types for re-opening auctions (LOC, limit-on-open) are supported by the broker API.
- [ ] Auction participation is skipped, not defaulted to market, when no usable fair value exists.
- [ ] Proxy hedge unwind fires on every resumption path (`RESUME_AUCTION`, `PRE_OPEN`, direct `NORMAL`), not only when an auction order is generated.
- [ ] Unwind trigger is tied to the auction fill where supported, so an unfilled auction order does not leave the position unhedged.

### Post-Mortem & Logging
- [ ] Action logging is comprehensive (recording reasons, state changes, prices, and quantities).
- [ ] Alerting channels (Slack, PagerDuty, Email) are configured for HALT and RESUME events.

# Black Swan Pre-Flight Checklist

Use this checklist to ensure your trading infrastructure is prepared for extreme market conditions, LULD halts, and market-wide circuit breakers.

### System Readiness
- [ ] Exchange status feed ingestion is real-time, tested, and reliable.
- [ ] Order cancellation logic verified to execute < 50ms upon HALT signal.
- [ ] Risk limits are configured dynamically (fat-tail aware), not statically fixed.
- [ ] API rate limit monitors are active to prevent order spam during halted states.

### Proxy Hedging Infrastructure
- [ ] Comprehensive mapping of primary symbols to highly liquid proxy instruments (ETFs, futures).
- [ ] Beta values for proxies are updated continuously based on recent historical windows.
- [ ] Basis risk calculation is active and has defined cut-off thresholds for every proxy pair.
- [ ] Proxy hedge execution logic is tested in paper-trading against simulated halts.

### Auction Participation
- [ ] Fair Value estimation models are running and can price halted assets based on proxy drift.
- [ ] Order types for re-opening auctions (LOC, limit-on-open) are supported by the broker API.
- [ ] Proxy hedge unwind logic is synchronized to execute alongside the auction resumption.

### Post-Mortem & Logging
- [ ] Action logging is comprehensive (recording reasons, state changes, prices, and quantities).
- [ ] Alerting channels (Slack, PagerDuty, Email) are configured for HALT and RESUME events.

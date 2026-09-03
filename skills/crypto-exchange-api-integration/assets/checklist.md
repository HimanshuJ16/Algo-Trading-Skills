# Pre-Flight / Sign-off Checklist — crypto-exchange-api-integration

Use this before considering the skill's implementation complete.

- [ ] **Right Rate-Limit Model:** Confirm each exchange uses its own model — Binance weight per fixed clock window, Kraken decaying counter per API key, Coinbase requests per second — and not one model copied across all three.
- [ ] **Current Limits, Not Hard-Coded Ones:** Confirm Binance limits are read from `GET /api/v3/exchangeInfo` `rateLimits[]` at startup, or that the hard-coded constants were re-checked against current docs (Spot is 6,000 weight/min since 2023-08-25, not 1,200).
- [ ] **Metering Scope:** Confirm per-IP limits (Binance, Coinbase) are shared across every process on the host, and per-API-key limits (Kraken) across every process using that key.
- [ ] **Bidirectional Header Sync:** Confirm `update_from_header()` moves the local counter **down** as well as up, and that the counter returns to zero at the clock-window boundary. A ratchet-only sync deadlocks the limiter and halts trading.
- [ ] **No Invented Budgets:** Confirm an unregistered namespace raises `UnknownNamespaceError` rather than receiving a default limiter.
- [ ] **Namespace Separation:** Confirm Spot, Futures, and Margin calls use separate limiters, balances, and endpoints.
- [ ] **Post-Only Spelled Correctly:** Confirm spot post-only renders `type=LIMIT_MAKER` with no `timeInForce`, futures post-only renders `type=LIMIT` + `timeInForce=GTX`, and that no payload contains `execInst` (a BitMEX parameter Binance ignores).
- [ ] **Self-Trade Prevention (STP):** Confirm `stp_mode` is chosen explicitly per strategy and validated against the symbol's `allowedSelfTradePreventionModes`.
- [ ] **24/7 Rolling P&L Reset:** Confirm risk controls use `Rolling24hPnLTracker` (or an explicit fixed UTC boundary) rather than an assumed session close.
- [ ] **Maintenance Backoff:** Confirm reconnect logic handles extended 502/503/504 maintenance windows with exponential jittered backoff, and honours `Retry-After` on 429 so it never escalates to an 418 IP ban.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/crypto-exchange-api-integration/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________

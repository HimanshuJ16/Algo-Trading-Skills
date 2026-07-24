# Algo-Trading-Skills — 500-Skill Global Roadmap

This is a planning backlog, not a claim that 500 skills are fully built. Entries marked **BUILT** have the full `SKILL.md` + `references/` + `scripts/` + `assets/` structure and pass `tools/validate_skills.py`. Entries marked **planned** are a title and one-line scope only, intended as a prioritized queue for future passes -- see `CONTRIBUTING.md` if you want to build one out yourself.


## broker-integration  _(target: 35)_

- **[BUILT]** `zerodha-kite-postback-webhook-verification` — Validating Kite Connect postback signatures so order-update webhooks can't be spoofed.
- **[BUILT]** `upstox-oauth-refresh-token-rotation` — Handling Upstox's refresh-token rotation without breaking a long-running bot session.
- **[BUILT]** `alpaca-paper-live-key-separation` — Preventing a bot from accidentally trading live capital using a paper-environment code path, and vice versa.
- **[BUILT]** `ibkr-tws-gateway-headless-launch` — Running IBKR's TWS/Gateway headless in a container for a bot with no persistent desktop session.
- **[BUILT]** `schwab-api-oauth-pkce-flow` — Implementing Schwab's (post-TD-Ameritrade-migration) OAuth2 PKCE flow for unattended token refresh.
- **[BUILT]** `tradestation-websocket-order-updates` — Consuming TradeStation's order-update stream without missing fills during reconnects.
- **[BUILT]** `broker-agnostic-adapter-interface` — Designing a broker adapter interface so strategy code doesn't need per-broker branching.
- **[BUILT]** `sandbox-vs-production-endpoint-drift` — Detecting behavioral differences between a broker's sandbox and production environments before they surprise you live.
- **[BUILT]** `webhook-based-order-fill-notifications` — Consuming broker fill-webhooks reliably, including replay/dedup for at-least-once delivery guarantees.
- **[BUILT]** `broker-account-margin-call-handling` — Detecting and responding programmatically to a broker-issued margin call before forced liquidation.
- _(capacity for 25 more skills in this category — not yet titled)_

## real-time-architecture  _(target: 30)_

- **[BUILT]** `redis-streams-multi-consumer-tick-fanout` — Fanning out a single tick feed to multiple independent consumer services via Redis Streams consumer groups.
- **[BUILT]** `clock-skew-correction-for-tick-timestamps` — Correcting for local-clock drift when timestamping incoming ticks against exchange-reported times.
- **[BUILT]** `market-data-snapshot-plus-delta-reconciliation` — Reconciling an initial full order-book snapshot with a subsequent delta stream without gaps.
- **[BUILT]** `multi-exchange-feed-normalization` — Normalizing tick schemas across multiple exchanges/brokers into one internal representation.
- **[BUILT]** `graceful-degradation-to-polling-fallback` — Falling back to REST polling when a WebSocket feed is degraded, without duplicating or missing data at the handover.
- **[BUILT]** `order-book-depth-processing-l2-l3` — Processing L2/L3 order book updates without introducing race conditions between bid/ask update messages.
- _(capacity for 24 more skills in this category — not yet titled)_

## backtesting-methodology  _(target: 30)_

- **[BUILT]** `walk-forward-optimization-window-management` — Managing in-sample and out-of-sample time windows for walk-forward optimization without lookahead leak.
- **[BUILT]** `survivorship-bias-free-universe-construction` — Building historical instrument universes that include delisted/defunct symbols, not just current constituents.
- **[BUILT]** `corporate-action-adjusted-backtesting` — Correctly applying splits, dividends, and mergers to historical price series without double-adjusting.
- **[BUILT]** `monte-carlo-strategy-robustness-testing` — Randomizing trade sequence/entry timing to test whether a strategy's edge survives reasonable perturbation.
- **[BUILT]** `multi-asset-backtest-currency-normalization` — Backtesting a multi-currency portfolio without silently mixing P&L across currencies.
- **[planned]** `benchmark-relative-performance-attribution` — Attributing backtest outperformance to specific factors rather than reporting only raw returns.
- _(capacity for 25 more skills in this category — not yet titled)_

## financial-ml  _(target: 40)_

- **[planned]** `regime-detection-for-strategy-switching` — Detecting market regime shifts (trending/ranging/high-vol) to switch between strategy variants live.
- **[planned]** `ensemble-signal-combination-without-overfitting` — Combining multiple models' signals without simply overfitting the combination weights to history.
- **[planned]** `feature-store-for-live-and-backtest-parity` — Building a feature store that guarantees identical computation between backtest and live paths.
- **[planned]** `reinforcement-learning-safety-constraints-for-execution` — Constraining an RL-based execution agent so it cannot learn to violate risk limits.
- **[planned]** `explainability-for-live-trading-signals` — Generating human-readable explanations for why a live ML signal fired, for post-hoc audit and trust-building.
- _(capacity for 35 more skills in this category — not yet titled)_

## risk-management  _(target: 40)_

- **[planned]** `value-at-risk-var-live-monitoring` — Computing and monitoring a live portfolio VaR estimate, not just a backtest-time figure.
- **[planned]** `stress-testing-against-historical-crash-scenarios` — Replaying a live portfolio's positions against historical crash scenarios (2020 COVID crash, 2015 flash crash, etc.) for tail-risk sizing.
- **[planned]** `multi-strategy-capital-allocation-limits` — Allocating and capping capital across multiple concurrently-running strategies sharing one account.
- **[planned]** `margin-utilization-circuit-breaker` — Halting new orders when margin utilization crosses a defined threshold, independent of P&L-based breakers.
- **[planned]** `counterparty-and-broker-concentration-risk` — Limiting exposure to any single broker/custodian to bound counterparty risk, not just market risk.
- _(capacity for 35 more skills in this category — not yet titled)_

## deployment-ops  _(target: 30)_

- **[planned]** `blue-green-deployment-for-live-strategy-updates` — Rolling out a strategy code update without a gap in market coverage or duplicate order risk.
- **[planned]** `secrets-rotation-without-bot-downtime` — Rotating broker API keys/secrets on a schedule without requiring a full bot restart.
- **[planned]** `multi-region-failover-for-broker-connectivity` — Failing over to a backup network path/region if the primary connection to a broker degrades.
- **[planned]** `structured-logging-for-post-incident-forensics` — Designing log schemas that make a post-incident timeline reconstruction possible without guesswork.
- _(capacity for 26 more skills in this category — not yet titled)_

## global-market-integration  _(target: 45)_

- **[BUILT]** `crypto-exchange-api-integration` — (see skills/<name>/SKILL.md for full description)
- **[BUILT]** `forex-broker-integration-oanda-mt5` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `binance-futures-testnet-to-mainnet-promotion` — Safely promoting a crypto futures bot from Binance testnet to mainnet with a distinct credential/config path.
- **[planned]** `coinbase-advanced-trade-api-migration` — Migrating from Coinbase's legacy Pro API to Advanced Trade without silently breaking order semantics.
- **[planned]** `kraken-websocket-v2-auth-and-subscriptions` — Authenticating and subscribing to Kraken's WebSocket v2 private feeds for order/fill updates.
- **[planned]** `interactive-brokers-global-multi-exchange-routing` — Routing orders correctly across IBKR's many supported global exchanges (LSE, HKEX, ASX, etc.) with correct contract specification.
- **[planned]** `mt5-python-bridge-for-forex-bots` — Bridging MetaTrader 5's native environment to a Python strategy engine reliably.
- **[planned]** `cme-globex-futures-api-integration` — Integrating with CME Globex for futures order routing and market data.
- **[planned]** `eurex-market-data-and-order-api` — Handling Eurex-specific contract specs and API quirks for European derivatives.
- **[planned]** `hong-kong-exchange-hkex-orion-api` — Integrating with HKEX's Orion trading API and its specific session/lot-size conventions.
- **[planned]** `singapore-exchange-sgx-api-integration` — Integrating with SGX's API for Singapore-listed derivatives and equities.
- **[planned]** `australian-securities-exchange-asx-api` — Handling ASX's API and T+2 settlement conventions for an Australian equities bot.
- **[planned]** `japan-exchange-group-jpx-api-integration` — Integrating with JPX/Tokyo Stock Exchange APIs, including their distinct trading-hour and tick-size rules.
- _(capacity for 32 more skills in this category — not yet titled)_

## regulatory-compliance-global  _(target: 40)_

- **[BUILT]** `pattern-day-trader-rule-compliance-us` — (see skills/<name>/SKILL.md for full description)
- **[BUILT]** `mifid-ii-algo-trading-compliance-eu` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `sec-rule-15c3-5-risk-controls-us` — Implementing the pre-trade risk controls required under SEC Rule 15c3-5 (the 'market access rule') for US broker-dealer routed flow.
- **[planned]** `finra-algo-trading-registration-requirements` — Understanding FINRA's algorithmic trading registration and testing requirements for US-based strategies.
- **[planned]** `esma-double-volume-cap-mechanism` — Accounting for ESMA's double volume cap mechanism when trading EU dark-pool venues.
- **[planned]** `uk-fca-algorithmic-trading-systems-controls` — Implementing the systems-and-controls requirements the UK FCA expects of algorithmic trading firms.
- **[planned]** `asic-market-integrity-rules-automated-trading` — Complying with ASIC's market integrity rules for automated order processing in Australia.
- **[planned]** `mas-singapore-algo-trading-guidelines` — Following MAS guidelines on automated trading systems risk management for Singapore-based operations.
- **[planned]** `wash-trade-and-spoofing-self-detection` — Building self-checks that flag a strategy's own order pattern if it could resemble wash trading or spoofing, before a regulator does.
- **[planned]** `best-execution-record-keeping-global` — Maintaining best-execution evidence across jurisdictions with differing regulatory expectations (US Reg NMS, EU MiFID II, etc.).
- _(capacity for 30 more skills in this category — not yet titled)_

## multi-asset-derivatives  _(target: 45)_

- **[BUILT]** `options-margin-span-calculation-global` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `futures-contract-roll-automation` — Automatically rolling futures positions ahead of expiry without a naive same-day roll causing slippage spikes.
- **[planned]** `options-greeks-real-time-portfolio-aggregation` — Aggregating delta/gamma/vega/theta across a multi-leg options portfolio in real time for risk monitoring.
- **[planned]** `calendar-spread-and-multi-leg-order-atomicity` — Ensuring multi-leg option/futures spread orders either fill entirely or roll back, not partially.
- **[planned]** `cross-margining-across-asset-classes` — Handling brokers that offer cross-margining between equities, options, and futures without misreporting available capital.
- **[planned]** `perpetual-futures-funding-rate-handling` — Accounting for perpetual futures funding-rate payments/receipts in live P&L for crypto derivatives.
- **[planned]** `fx-forward-and-swap-position-tracking` — Tracking FX forward/swap positions and their forward-point carry correctly, distinct from spot P&L.
- _(capacity for 38 more skills in this category — not yet titled)_

## execution-algorithms  _(target: 35)_

- **[BUILT]** `execution-algo-twap-vwap-slicing` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `participation-of-volume-pov-execution` — Implementing a POV execution algorithm that scales order slicing to real-time observed volume.
- **[planned]** `implementation-shortfall-minimization` — Designing an execution schedule that minimizes implementation shortfall versus the arrival price benchmark.
- **[planned]** `iceberg-order-simulation-and-detection` — Simulating iceberg/hidden-quantity orders in a backtest, and detecting when a strategy is inadvertently signaling its own size.
- **[planned]** `smart-order-routing-across-venues` — Routing an order across multiple venues/exchanges to minimize cost when a single venue lacks sufficient liquidity.
- **[planned]** `adaptive-execution-under-volatility-spikes` — Switching an execution algorithm's aggressiveness in response to a real-time volatility spike detector.
- _(capacity for 29 more skills in this category — not yet titled)_

## data-management-global  _(target: 40)_

- **[BUILT]** `global-exchange-holiday-calendar-handling` — (see skills/<name>/SKILL.md for full description)
- **[BUILT]** `multi-timezone-session-scheduling` — (see skills/<name>/SKILL.md for full description)
- **[BUILT]** `multi-currency-pnl-and-fx-conversion` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `daylight-saving-time-transition-handling` — Handling DST transitions correctly for exchanges/brokers whose local trading hours shift twice a year.
- **[planned]** `point-in-time-fundamentals-data-joins` — Joining fundamentals/reference data by as-of publish date rather than calendar date, across global data vendors.
- **[planned]** `reference-data-symbol-mapping-across-vendors` — Mapping instrument identifiers (ISIN, CUSIP, ticker, exchange-specific codes) consistently across data vendors and brokers.
- **[planned]** `historical-tick-data-storage-and-compaction` — Storing and compacting historical tick data at scale without exhausting storage or query latency budgets.
- _(capacity for 33 more skills in this category — not yet titled)_

## crypto-custody-security  _(target: 30)_

- **[BUILT]** `crypto-wallet-key-custody-security` — (see skills/<name>/SKILL.md for full description)
- **[planned]** `hot-cold-wallet-split-for-trading-bots` — Splitting a crypto trading bot's operational hot-wallet balance from cold storage to bound loss from a compromise.
- **[planned]** `exchange-withdrawal-whitelist-enforcement` — Enforcing withdrawal address whitelisting so a compromised bot credential can't exfiltrate funds to an arbitrary address.
- **[planned]** `multi-signature-approval-for-large-transfers` — Requiring multi-sig approval for any transfer above a threshold, independent of the bot's own logic.
- _(capacity for 26 more skills in this category — not yet titled)_

## portfolio-multi-strategy  _(target: 30)_

- **[planned]** `cross-strategy-correlation-monitoring` — Monitoring correlation between concurrently-running strategies to detect unintended aggregate concentration.
- **[planned]** `capital-reallocation-based-on-live-performance` — Reallocating capital between strategies based on live (not just backtested) rolling performance, with safeguards against reallocation churn.
- **[planned]** `strategy-lifecycle-retirement-criteria` — Defining explicit, pre-agreed criteria for retiring a live strategy rather than letting it run indefinitely on inertia.
- _(capacity for 27 more skills in this category — not yet titled)_

## market-microstructure-latency  _(target: 30)_

- **[planned]** `colocation-latency-budget-accounting` — Accounting for colocation and network latency budgets when a strategy's edge depends on sub-millisecond response time.
- **[planned]** `clock-synchronization-ptp-for-trading-hosts` — Using PTP (Precision Time Protocol) instead of NTP for trading-host clock sync where microsecond accuracy matters.
- **[planned]** `tick-to-trade-latency-measurement` — Measuring true tick-to-trade latency end to end, not just the strategy's own compute time.
- _(capacity for 27 more skills in this category — not yet titled)_

---

**Totals: 28 built, 78 authored-and-planned, 394 further slots reserved across categories to reach the 500 target as titles are added.**

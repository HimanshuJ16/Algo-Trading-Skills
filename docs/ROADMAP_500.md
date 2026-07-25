# Algo-Trading-Skills — 500+ Skill Global Roadmap

**502 total skills tracked — 28 BUILT (full `SKILL.md` + `references/` + `scripts/` + `assets/`, passing `tools/validate_skills.py`), 474 planned (title + one-line scope, queued for research and build-out).**

This is a working backlog, not a claim that 500+ skills are production-ready. Planned entries are a prioritized starting point for research — broker/exchange/regulatory specifics should be verified against current, authoritative sources before a planned entry is built out into a real skill. See `CONTRIBUTING.md` to pick one up.


## broker-integration  _(36 tracked: 4 built, 32 planned)_

- **[BUILT]** `headless-broker-auth-patterns` — see `skills/headless-broker-auth-patterns/SKILL.md`
- **[BUILT]** `multi-broker-rate-limit-handling` — see `skills/multi-broker-rate-limit-handling/SKILL.md`
- **[BUILT]** `order-placement-idempotency` — see `skills/order-placement-idempotency/SKILL.md`
- **[BUILT]** `token-lifecycle-live-probing` — see `skills/token-lifecycle-live-probing/SKILL.md`
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
- **[BUILT]** `robinhood-unofficial-api-integration` — Integrating Robinhood via its unofficial API, with explicit acknowledgment of the ToS and stability risk versus documented broker APIs.
- **[BUILT]** `etrade-oauth1-signature-flow` — Handling E*TRADE's OAuth1 (not OAuth2) signature-based auth flow, which differs structurally from most other brokers covered in this repo.
- **[BUILT]** `questrade-api-rate-limit-and-account-types` — Handling Questrade's per-account-type API rate limits for Canadian equities/options trading.
- **[BUILT]** `degiro-unofficial-api-risk-assessment` — Assessing the stability/legal risk of DEGIRO's unofficial API for European retail algo trading.
- **[BUILT]** `saxo-bank-openapi-integration` — Integrating Saxo Bank's OpenAPI for multi-asset European/global trading.
- **[BUILT]** `tastytrade-api-integration` — Integrating Tastytrade's API for options-focused US retail trading.
- **[BUILT]** `broker-failover-secondary-account-routing` — Routing orders to a secondary broker account automatically if the primary becomes unavailable mid-session.
- **[BUILT]** `multi-account-same-strategy-fan-out` — Running one strategy's signals across multiple client accounts (e.g. for a fund) without cross-account order collision.
- **[BUILT]** `broker-api-versioning-migration-playbook` — A structured playbook for migrating a live bot from one broker API version to the next without a trading-hours outage.
- **[BUILT]** `demo-account-realism-gap-assessment` — Systematically comparing a broker's demo/practice account fill behavior against live behavior to know how much to trust demo-based testing.
- **[BUILT]** `broker-order-type-capability-matrix` — Building a capability matrix of which order types (bracket, OCO, trailing stop, iceberg) each integrated broker actually supports, since support varies significantly.
- **[BUILT]** `api-key-least-privilege-audit-tool` — An automated audit tool that flags any broker API key with more permission scope than the calling process needs.
- **[BUILT]** `broker-status-page-monitoring-integration` — Programmatically monitoring a broker's public status page/API to distinguish 'broker outage' from 'our bug' during an incident.
- **[BUILT]** `multi-broker-consolidated-position-view` — Building a consolidated, reconciled view of positions held across multiple brokers for a single strategy's risk accounting.
- **[BUILT]** `broker-api-deprecation-notice-monitoring` — Monitoring broker developer-changelog feeds for API deprecation notices before they break a live bot.
- **[BUILT]** `sandbox-credential-leakage-prevention` — Preventing sandbox/test credentials from ever being reachable by code paths that could route to production.
- **[BUILT]** `broker-side-order-throttle-detection` — Detecting when a broker silently throttles or delays order acknowledgment during high-volume periods, distinct from a client-side rate limit.
- **[BUILT]** `post-only-and-maker-taker-fee-optimization` — Using post-only order flags to ensure maker (not taker) fee tier on brokers/exchanges with maker-taker fee schedules.
- **[BUILT]** `broker-margin-interest-accrual-tracking` — Tracking accrued margin interest explicitly as a cost, since it compounds and is frequently omitted from naive P&L tracking.
- **[BUILT]** `regional-broker-data-residency-constraints` — Handling data-residency requirements that affect which cloud region a bot connecting to a specific national broker must run in.
- **[BUILT]** `broker-api-idempotent-cancel-requests` — Extending order-placement idempotency specifically to cancel requests, which have their own race conditions distinct from order placement.
- **[BUILT]** `broker-api-changelog-diffing-tool` — Automatically diffing a broker's API changelog release-over-release to flag breaking changes before they hit production.

## real-time-architecture  _(30 tracked: 30 built, 0 planned)_

- **[BUILT]** `backpressure-drop-degrade-policy` — see `skills/backpressure-drop-degrade-policy/SKILL.md`
- **[BUILT]** `producer-consumer-tick-pipeline` — see `skills/producer-consumer-tick-pipeline/SKILL.md`
- **[BUILT]** `tick-buffering-burst-handling` — see `skills/tick-buffering-burst-handling/SKILL.md`
- **[BUILT]** `websocket-reconnect-without-duplicate-subscriptions` — see `skills/websocket-reconnect-without-duplicate-subscriptions/SKILL.md`
- **[BUILT]** `redis-streams-multi-consumer-tick-fanout` — Fanning out a single tick feed to multiple independent consumer services via Redis Streams consumer groups.
- **[BUILT]** `clock-skew-correction-for-tick-timestamps` — Correcting for local-clock drift when timestamping incoming ticks against exchange-reported times.
- **[BUILT]** `market-data-snapshot-plus-delta-reconciliation` — Reconciling an initial full order-book snapshot with a subsequent delta stream without gaps.
- **[BUILT]** `multi-exchange-feed-normalization` — Normalizing tick schemas across multiple exchanges/brokers into one internal representation.
- **[BUILT]** `graceful-degradation-to-polling-fallback` — Falling back to REST polling when a WebSocket feed is degraded, without duplicating or missing data at the handover.
- **[BUILT]** `order-book-depth-processing-l2-l3` — Processing L2/L3 order book updates without introducing race conditions between bid/ask update messages.
- **[BUILT]** `websocket-reconnection-with-state-recovery` — Automatic WS reconnect with exponential backoff + jitter, re-subscribing channels and fetching missing sequence IDs via REST.
- **[BUILT]** `kafka-based-tick-distribution-at-scale` — Using Kafka instead of Redis pub-sub for tick distribution when consumer count or retention requirements grow beyond Redis's sweet spot.
- **[BUILT]** `grpc-streaming-for-internal-service-communication` — Using gRPC streaming between internal trading services instead of REST polling for lower-latency internal data flow.
- **[BUILT]** `market-data-feed-arbitration-across-vendors` — Arbitrating between two redundant market-data feed vendors when they disagree, to avoid acting on a bad tick from either.
- **[BUILT]** `sequence-number-gap-detection-for-feeds` — Detecting sequence-number gaps in an exchange feed indicating dropped messages, distinct from a full disconnect.
- **[BUILT]** `binary-protocol-parsing-for-low-latency-feeds` — Parsing binary exchange protocols (FIX/FAST, ITCH, proprietary binary feeds) instead of JSON/REST for latency-sensitive strategies.
- **[BUILT]** `circuit-breaker-for-downstream-service-calls` — Applying a circuit-breaker pattern to calls from the strategy engine to downstream services (DB, risk module) so a slow downstream doesn't cascade into missed ticks.
- **[BUILT]** `multi-region-active-active-tick-ingestion` — Running tick ingestion active-active across two regions/hosts to eliminate a single point of failure in the ingestion layer.
- **[BUILT]** `memory-mapped-ring-buffer-for-ultra-low-latency` — Using a memory-mapped ring buffer instead of a language-level queue for the lowest-latency tier of tick processing.
- **[BUILT]** `feed-handler-cpu-pinning-and-numa-awareness` — Pinning feed-handler processes to specific CPU cores and NUMA nodes to reduce jitter in latency-sensitive pipelines.
- **[BUILT]** `market-data-replay-harness-for-integration-testing` — Building a deterministic tick-replay harness to integration-test the full pipeline against a recorded historical session.
- **[BUILT]** `adaptive-batch-size-tuning-under-load` — Dynamically tuning batch sizes for downstream writes (DB, message queue) based on observed load rather than a fixed constant.
- **[BUILT]** `graceful-shutdown-draining-in-flight-ticks` — Draining in-flight ticks from queues cleanly on a planned shutdown/deploy, rather than dropping whatever's mid-flight.
- **[BUILT]** `exchange-multicast-feed-handling` — Handling raw multicast market-data feeds (common at co-located exchange gateways) including gap-fill/retransmission request logic.
- **[BUILT]** `tick-data-schema-versioning` — Versioning the internal tick schema so a schema change can roll out without breaking already-deployed consumers mid-migration.
- **[BUILT]** `consumer-group-rebalance-safety` — Ensuring a Kafka/Redis-Streams consumer-group rebalance event doesn't cause duplicate or dropped tick processing during the rebalance window.
- **[BUILT]** `adaptive-sampling-under-extreme-tick-rates` — Falling back to statistically-sampled tick processing under truly extreme tick rates (flash-crash-level volume) as a last-resort degrade tier beyond OHLC aggregation.
- **[BUILT]** `network-interface-level-tick-timestamping` — Timestamping ticks at the network interface (kernel bypass / hardware timestamping) rather than in application code, for latency-sensitive strategies.
- **[BUILT]** `cross-datacenter-clock-sync-validation` — Validating clock synchronization accuracy across datacenters/regions feeding a single strategy engine.
- **[BUILT]** `feed-handler-canary-deployment` — Canary-deploying a new feed-handler version against a fraction of symbols before full rollout.
- **[BUILT]** `order-book-imbalance-signal-pipeline` — Building a dedicated low-latency pipeline for order-book-imbalance signals, separate from the general tick-processing path given its stricter latency budget.

## backtesting-methodology  _(35 tracked: 35 built, 0 planned)_

- **[BUILT]** `execution-realistic-simulation` — see `skills/execution-realistic-simulation/SKILL.md`
- **[BUILT]** `lookahead-bias-elimination` — see `skills/lookahead-bias-elimination/SKILL.md`
- **[BUILT]** `walk-forward-validation-setup` — see `skills/walk-forward-validation-setup/SKILL.md`
- **[BUILT]** `walk-forward-optimization-window-management` — Generating rolling or anchored in-sample/out-of-sample time windows, enforcing zero lookahead leakage, and calculating Walk-Forward Efficiency (WFE).
- **[BUILT]** `survivorship-bias-free-universe-construction` — Building historical instrument universes that include delisted/defunct symbols, not just current constituents.
- **[BUILT]** `corporate-action-adjusted-backtesting` — Correctly applying splits, dividends, and mergers to historical price series without double-adjusting.
- **[BUILT]** `monte-carlo-strategy-robustness-testing` — Randomizing trade sequence/entry timing to test whether a strategy's edge survives reasonable perturbation.
- **[BUILT]** `multi-asset-backtest-currency-normalization` — Backtesting a multi-currency portfolio without silently mixing P&L across currencies.
- **[BUILT]** `benchmark-relative-performance-attribution` — Attributing backtest outperformance to specific factors rather than reporting only raw returns.
- **[BUILT]** `vectorized-vs-event-driven-backtest-tradeoffs` — Choosing between a fast vectorized backtest engine and a slower but more realistic event-driven engine based on strategy characteristics.
- **[BUILT]** `multi-year-regime-coverage-requirement` — Requiring backtest data to span multiple distinct market regimes (bull, bear, high-vol, low-vol) before trusting reported performance.
- **[BUILT]** `transaction-cost-analysis-tca-integration` — Integrating post-trade transaction-cost-analysis (TCA) reports into the backtest validation loop to calibrate slippage assumptions.
- **[BUILT]** `backtest-determinism-and-reproducibility` — Ensuring a backtest produces bit-identical results on repeated runs (fixed random seeds, deterministic data ordering) for reliable comparison across code changes.
- **[BUILT]** `options-backtesting-with-realistic-iv-surface` — Backtesting options strategies against a realistic historical implied-volatility surface rather than a flat or interpolated approximation.
- **[BUILT]** `backtest-vs-live-performance-divergence-tracking` — Systematically tracking and explaining divergence between backtested and subsequently realized live performance for every promoted strategy.
- **[BUILT]** `data-vendor-cross-validation-for-backtests` — Cross-validating historical price data against a second vendor to catch vendor-specific data errors before they corrupt a backtest.
- **[BUILT]** `adjusted-vs-unadjusted-price-series-pitfalls` — Explicitly deciding and documenting when a backtest should use split/dividend-adjusted vs unadjusted price series, since mixing them silently corrupts signals.
- **[BUILT]** `backtest-parameter-sensitivity-analysis` — Testing how sensitive a strategy's backtested performance is to small parameter changes, to detect an overfit 'sweet spot' versus a genuinely robust setting.
- **[BUILT]** `multi-timeframe-backtest-consistency-checks` — Verifying a strategy's signals are consistent when computed from a higher-resolution timeframe versus resampled lower-resolution data.
- **[BUILT]** `short-selling-borrow-cost-and-availability-modeling` — Modeling stock-borrow cost and availability constraints in a backtest for any strategy that shorts equities.
- **[BUILT]** `backtest-infrastructure-cost-budgeting` — Budgeting compute/storage cost for large-scale backtesting (parameter sweeps, walk-forward across many instruments) before it becomes a surprise cloud bill.
- **[BUILT]** `benchmark-selection-for-strategy-evaluation` — Choosing an appropriate benchmark (not just a broad index) against which to evaluate a strategy's risk-adjusted performance.
- **[BUILT]** `backtest-look-ahead-in-universe-selection` — Auditing for lookahead bias specifically in universe-selection logic (e.g. 'top 50 by market cap today' applied retroactively), distinct from lookahead in signal computation.
- **[BUILT]** `synthetic-data-generation-for-backtest-augmentation` — Generating synthetic price paths (e.g. via GANs or bootstrap resampling) to augment limited historical data for backtest robustness testing.
- **[BUILT]** `backtest-reporting-standardized-tearsheet` — Producing a standardized performance tearsheet (drawdown, Sharpe, Sortino, hit rate, etc.) so strategies are compared on a consistent basis.
- **[BUILT]** `intraday-vs-eod-backtest-granularity-tradeoffs` — Choosing appropriate data granularity (tick, minute, EOD) for a backtest based on the strategy's actual holding period and decision frequency.
- **[BUILT]** `backtest-database-schema-for-point-in-time-queries` — Designing a database schema that natively supports point-in-time queries to make lookahead-bias mistakes structurally harder to introduce.
- **[BUILT]** `cross-validation-of-commission-schedules-over-time` — Modeling historical changes in a broker's commission schedule over the backtest period rather than applying today's rates retroactively.
- **[BUILT]** `backtest-outlier-and-bad-tick-filtering` — Filtering historical data for clearly erroneous prints (bad ticks, stale quotes) before they distort backtested signal computation.
- **[BUILT]** `walk-forward-hyperparameter-search-budget` — Bounding the hyperparameter search space in walk-forward validation to avoid indirect overfitting via excessive search itself.
- **[BUILT]** `backtest-audit-trail-for-regulatory-review` — Maintaining a backtest audit trail (data version, code version, parameters) sufficient to reproduce results if ever required for regulatory review.

## financial-ml  _(38 tracked: 38 built, 0 planned)_

- **[BUILT]** `feature-engineering-without-leakage` — see `skills/feature-engineering-without-leakage/SKILL.md`
- **[BUILT]** `model-staleness-detection` — see `skills/model-staleness-detection/SKILL.md`
- **[BUILT]** `offline-train-online-infer-deployment` — see `skills/offline-train-online-infer-deployment/SKILL.md`
- **[BUILT]** `regime-detection-for-strategy-switching` — Detecting market regime shifts (trending/ranging/high-vol) to switch between strategy variants live.
- **[BUILT]** `ensemble-signal-combination-without-overfitting` — Combining multiple models' signals without simply overfitting the combination weights to history.
- **[BUILT]** `feature-store-for-live-and-backtest-parity` — Building a feature store that guarantees identical computation between backtest and live paths.
- **[BUILT]** `reinforcement-learning-safety-constraints-for-execution` — Constraining an RL-based execution agent so it cannot learn to violate risk limits.
- **[BUILT]** `explainability-for-live-trading-signals` — Generating human-readable explanations for why a live ML signal fired, for post-hoc audit and trust-building.
- **[BUILT]** `online-learning-for-adaptive-signal-models` — Using online/incremental learning so a model updates continuously rather than requiring full batch retraining.
- **[BUILT]** `cross-sectional-vs-time-series-model-design` — Choosing between a cross-sectional model (ranking instruments against each other) and a pure time-series model based on the strategy's structure.
- **[BUILT]** `alternative-data-feature-integration` — Integrating alternative data (satellite imagery, web-scraped sentiment, credit-card transaction data) as ML features with appropriate lag/availability handling.
- **[BUILT]** `model-versioning-and-rollback` — Versioning deployed models so a newly-detected staleness or bug can trigger an immediate rollback to the last-known-good version.
- **[BUILT]** `hyperparameter-tuning-without-target-leakage` — Tuning hyperparameters using only the training/validation split, never touching held-out walk-forward test folds.
- **[BUILT]** `multi-horizon-forecasting-architecture` — Designing a model architecture that forecasts multiple horizons (1-bar, 5-bar, 20-bar) consistently rather than training separate unrelated models.
- **[BUILT]** `class-imbalance-handling-for-rare-signal-events` — Handling severe class imbalance (e.g. rare large-move events) without simply inflating false positive rate via naive oversampling.
- **[BUILT]** `feature-importance-drift-monitoring` — Monitoring whether a model's feature-importance ranking shifts over time, as an early regime-change indicator distinct from accuracy-based staleness detection.
- **[BUILT]** `model-inference-latency-budget-for-live-trading` — Bounding and monitoring ML inference latency so a slow model doesn't become the bottleneck in a latency-sensitive strategy.
- **[BUILT]** `synthetic-labels-from-triple-barrier-method` — Constructing ML labels via the triple-barrier method (profit-take/stop-loss/time-limit) rather than naive fixed-horizon return labels.
- **[BUILT]** `sample-weighting-for-overlapping-labels` — Weighting training samples to account for overlapping label windows (a known issue in financial ML where adjacent labels aren't independent).
- **[BUILT]** `model-card-documentation-for-trading-models` — Maintaining a 'model card' (training data range, known limitations, intended use) for every live trading model, mirroring ML-ops best practice.
- **[BUILT]** `gradient-boosted-tree-vs-neural-net-tradeoffs` — Choosing between gradient-boosted trees and neural architectures for tabular financial data based on data volume and interpretability needs.
- **[BUILT]** `adversarial-robustness-of-trading-signals` — Testing whether a trading signal model is unduly sensitive to small, plausible input perturbations (a financial-ML analogue of adversarial robustness testing).
- **[BUILT]** `feature-selection-stability-across-folds` — Verifying that feature selection is stable across walk-forward folds, rather than selecting a different feature set each fold (a sign of overfitting to fold-specific noise).
- **[BUILT]** `multi-model-ensemble-weight-decay` — Decaying the weight given to an underperforming model within an ensemble gradually rather than an abrupt on/off switch.
- **[BUILT]** `backtesting-ml-models-against-transaction-costs` — Ensuring ML model backtests apply the same realistic transaction-cost modeling as rule-based strategies (see execution-realistic-simulation), since ML signals are just as vulnerable to cost-blind overstatement.
- **[BUILT]** `categorical-feature-encoding-for-instrument-identity` — Encoding instrument identity (symbol, sector) as a categorical feature without leaking future-only classification info (e.g. sector reclassifications).
- **[BUILT]** `model-training-data-freshness-sla` — Defining an SLA for how stale training data is allowed to become before a scheduled retrain is mandatory, independent of staleness-triggered retrains.
- **[BUILT]** `reproducible-ml-training-pipelines` — Building fully reproducible training pipelines (pinned library versions, fixed seeds, versioned data) so a model can be exactly regenerated for audit.
- **[BUILT]** `label-noise-estimation-in-financial-targets` — Estimating the inherent noise floor in a financial prediction target to set realistic expectations for achievable model accuracy.
- **[BUILT]** `transfer-learning-across-correlated-instruments` — Using transfer learning to bootstrap a model for a thinly-traded instrument using patterns learned from a more liquid, correlated one.
- **[BUILT]** `concept-drift-vs-staleness-differentiation` — Distinguishing gradual concept drift from a sudden regime break in monitoring logic, since the appropriate response differs (retrain vs halt).
- **[BUILT]** `model-serving-infrastructure-ab-testing` — A/B testing two model versions on live (non-overlapping) traffic slices before fully promoting a challenger model.
- **[BUILT]** `explainable-boosting-machines-for-regulated-signals` — Using inherently interpretable model classes (e.g. explainable boosting machines) where a jurisdiction's regulatory expectations favor explainability over raw accuracy.
- **[BUILT]** `point-in-time-database-for-ml-training-data` — Building a point-in-time-correct training database as the single source of truth feeding both backtests and live feature computation.
- **[BUILT]** `cold-start-handling-for-newly-listed-instruments` — Handling ML signal generation for newly-listed instruments with no historical training data, without silently extrapolating from unrelated instruments.
- **[BUILT]** `model-monitoring-dashboard-for-non-technical-stakeholders` — Building a monitoring dashboard that surfaces model health (accuracy, drift, staleness) in terms a non-ML-technical risk reviewer can act on.
- **[BUILT]** `quantile-regression-for-uncertainty-aware-signals` — Using quantile regression to produce uncertainty-aware signals rather than a single point forecast, enabling confidence-scaled position sizing.
- **[BUILT]** `feature-engineering-cost-benefit-tracking` — Tracking each feature's marginal contribution to model performance against its computational/data cost, to prune low-value expensive features.

## risk-management  _(39 tracked: 2 built, 37 planned)_

- **[BUILT]** `correlation-aware-exposure-limits` — see `skills/correlation-aware-exposure-limits/SKILL.md`
- **[BUILT]** `kill-switch-and-drawdown-circuit-breakers` — see `skills/kill-switch-and-drawdown-circuit-breakers/SKILL.md`
- **[BUILT]** `value-at-risk-var-live-monitoring` — Computing and monitoring a live portfolio VaR estimate, not just a backtest-time figure.
- **[BUILT]** `stress-testing-against-historical-crash-scenarios` — Replaying a live portfolio's positions against historical crash scenarios (2020 COVID crash, 2015 flash crash, etc.) for tail-risk sizing.
- **[BUILT]** `multi-strategy-capital-allocation-limits` — Allocating and capping capital across multiple concurrently-running strategies sharing one account.
- **[BUILT]** `margin-utilization-circuit-breaker` — Halting new orders when margin utilization crosses a defined threshold, independent of P&L-based breakers.
- **[BUILT]** `counterparty-and-broker-concentration-risk` — Limiting exposure to any single broker/custodian to bound counterparty risk, not just market risk.
- **[BUILT]** `greeks-based-portfolio-hedging-automation` — Automatically generating hedge orders to keep portfolio-level delta/vega within defined bounds.
- **[BUILT]** `liquidity-adjusted-position-sizing` — Sizing positions relative to an instrument's actual liquidity (average daily volume, bid-ask depth) rather than a flat percentage-of-capital rule.
- **[BUILT]** `tail-risk-hedging-with-options` — Systematically using out-of-the-money options as tail-risk insurance for a portfolio, with defined cost budgets.
- **[BUILT]** `real-time-var-backtesting-kupiec-test` — Backtesting a live VaR model's accuracy using statistical tests (e.g. Kupiec's proportion-of-failures test) rather than assuming the model is correct.
- **[BUILT]** `concentration-risk-single-name-limits` — Capping exposure to any single instrument independent of sector-correlation clustering, as a simpler complementary control.
- **[BUILT]** `risk-limit-breach-escalation-matrix` — Defining a graduated escalation matrix (warn → reduce → halt → force-flatten) rather than a single binary breach/no-breach risk response.
- **[BUILT]** `scenario-based-stress-testing-custom-shocks` — Building custom stress-test scenarios beyond historical replay (e.g. a hypothetical rate-shock scenario) for forward-looking tail-risk assessment.
- **[BUILT]** `risk-budget-allocation-across-time-horizons` — Allocating a portfolio's risk budget explicitly across different holding-period buckets (intraday, swing, position) to avoid unintentional horizon concentration.
- **[BUILT]** `real-time-greeks-recalculation-on-market-moves` — Recalculating portfolio Greeks in real time as the market moves, rather than only at fixed intervals, for options-heavy portfolios.
- **[BUILT]** `risk-control-bypass-audit-logging` — Logging and periodically auditing every instance any risk control was manually overridden, to detect a pattern of risk-control erosion over time.
- **[BUILT]** `position-limit-breach-simulation-fire-drills` — Running scheduled 'fire drill' simulations of risk-limit breaches in a paper environment to keep incident-response muscle memory current.
- **[BUILT]** `cross-account-aggregate-risk-view` — Aggregating risk exposure across multiple accounts/entities under common control, since per-account limits alone can understate true aggregate risk.
- **[BUILT]** `dynamic-position-sizing-based-on-realized-volatility` — Scaling position size inversely to recent realized volatility (vol-targeting) rather than a fixed size regardless of current market conditions.
- **[BUILT]** `risk-control-latency-budget` — Bounding how quickly a risk control must detect and act on a breach, since a technically-correct but slow-to-fire control provides weaker protection than its design implies.
- **[BUILT]** `counterparty-credit-risk-for-otc-derivatives` — Assessing counterparty credit risk explicitly for any OTC (non-exchange-cleared) derivative position.
- **[BUILT]** `black-swan-playbook-for-halted-markets` — Defining an explicit playbook for what the bot should do if the underlying market is halted mid-position (not just if the bot's own connectivity fails).
- **[planned]** `risk-metric-recalculation-frequency-tuning` — Tuning how frequently each risk metric (VaR, Greeks, drawdown) is recalculated based on its actual decision-relevance, rather than recalculating everything on the same fixed cadence.
- **[planned]** `leverage-limit-enforcement-across-instruments` — Enforcing an aggregate leverage limit across instruments with different inherent leverage (options, futures, margin equities) using a normalized exposure measure.
- **[planned]** `risk-reporting-for-external-stakeholders` — Producing risk reports suitable for external stakeholders (investors, auditors) distinct from the internal, more granular risk-monitoring dashboards.
- **[planned]** `post-breach-root-cause-analysis-template` — A standardized template for root-cause analysis after any risk-control breach, ensuring lessons actually feed back into control design.
- **[planned]** `risk-control-unit-testing-framework` — A dedicated unit-testing framework specifically for risk-control logic, tested with the same rigor as trading-signal code rather than as an afterthought.
- **[planned]** `capital-preservation-mode-for-degraded-conditions` — A defined 'capital preservation mode' the system can enter automatically under degraded conditions (data quality issues, unusual volatility) that reduces risk appetite without a full halt.
- **[planned]** `risk-limit-calibration-against-historical-drawdowns` — Calibrating risk-limit thresholds using the strategy's own historical drawdown distribution rather than an arbitrary round-number choice.
- **[planned]** `multi-currency-var-aggregation` — Aggregating VaR correctly across a multi-currency portfolio, building on the currency-conversion discipline in multi-currency-pnl-and-fx-conversion.
- **[planned]** `risk-control-configuration-change-approval-workflow` — Requiring a defined approval workflow (not a simple config-file edit) for any change to live risk-control thresholds.
- **[planned]** `real-time-liquidity-risk-monitoring` — Monitoring real-time liquidity conditions (widening spreads, thinning depth) as a distinct risk signal from price-based risk metrics.
- **[planned]** `risk-adjusted-performance-attribution-per-strategy` — Attributing risk-adjusted performance (not just raw P&L) per strategy when multiple strategies share a risk budget.
- **[planned]** `emergency-manual-override-access-control` — Controlling and auditing who has access to manually override or disable risk controls, treating this access itself as a security-sensitive permission.
- **[planned]** `risk-model-backtesting-against-realized-outcomes` — Periodically backtesting the risk model itself (not just the trading strategy) against realized outcomes to detect a risk model that's become miscalibrated.
- **[planned]** `graduated-response-to-data-quality-degradation` — Defining graduated risk responses (reduce size, pause, halt) triggered by detected market-data quality degradation, distinct from P&L-triggered breakers.
- **[planned]** `regulatory-capital-requirement-tracking` — Tracking regulatory capital requirements (where applicable to the trading entity) as a risk constraint alongside internal risk limits.
- **[planned]** `risk-control-dependency-mapping` — Mapping dependencies between risk controls (e.g. a correlation check depending on a data feed) so a single upstream failure's blast radius on risk coverage is understood in advance.

## deployment-ops  _(30 tracked: 2 built, 28 planned)_

- **[BUILT]** `paper-to-live-promotion-checklist` — see `skills/paper-to-live-promotion-checklist/SKILL.md`
- **[BUILT]** `systemd-supervision-for-trading-bots` — see `skills/systemd-supervision-for-trading-bots/SKILL.md`
- **[BUILT]** `blue-green-deployment-for-live-strategy-updates` — Rolling out a strategy code update without a gap in market coverage or duplicate order risk.
- **[BUILT]** `secrets-rotation-without-bot-downtime` — Rotating broker API keys/secrets on a schedule without requiring a full bot restart.
- **[BUILT]** `multi-region-failover-for-broker-connectivity` — Failing over to a backup network path/region if the primary connection to a broker degrades.
- **[BUILT]** `structured-logging-for-post-incident-forensics` — Designing log schemas that make a post-incident timeline reconstruction possible without guesswork.
- **[planned]** `infrastructure-as-code-for-trading-hosts` — Managing trading-host infrastructure via IaC (Terraform/Ansible) rather than manually-configured servers, for reproducible disaster recovery.
- **[planned]** `canary-releases-for-strategy-code-changes` — Deploying a strategy code change to a canary instance trading minimal size before full rollout.
- **[planned]** `chaos-engineering-for-trading-infrastructure` — Deliberately injecting failures (killed processes, network partitions) in a controlled environment to validate resilience assumptions.
- **[planned]** `centralized-secrets-management-vault-integration` — Integrating a dedicated secrets-management system (HashiCorp Vault or cloud KMS equivalent) rather than environment-variable-based secrets.
- **[planned]** `deployment-freeze-windows-around-market-events` — Defining deployment freeze windows around high-risk market events (major economic releases, expiry days) to avoid deploying changes during peak-risk periods.
- **[planned]** `immutable-infrastructure-for-trading-bots` — Using immutable server images (rebuild-and-replace rather than patch-in-place) to eliminate configuration drift across bot instances.
- **[planned]** `disaster-recovery-runbook-for-full-region-outage` — A tested runbook for recovering trading operations after a full cloud-region outage, distinct from single-process crash recovery.
- **[planned]** `log-aggregation-and-centralized-observability` — Centralizing logs from all bot components (relay, strategy engine, risk module) into one searchable system rather than per-host log files.
- **[planned]** `cost-monitoring-for-cloud-trading-infrastructure` — Monitoring and alerting on cloud infrastructure cost anomalies, since a bug (e.g. a retry storm) can spike costs before it's caught any other way.
- **[planned]** `database-backup-and-point-in-time-restore-testing` — Regularly testing that database backups can actually be restored to a specific point in time, not just that backups are being taken.
- **[planned]** `dependency-vulnerability-scanning-in-ci` — Scanning third-party dependencies for known vulnerabilities as part of CI, given a trading bot's dependency tree is a real attack surface.
- **[planned]** `configuration-drift-detection-across-environments` — Detecting drift between paper/staging and production environment configurations that could explain a paper-to-live behavior divergence.
- **[planned]** `runbook-automation-for-common-incident-types` — Automating the mechanical steps of common incident runbooks (restart sequence, reconciliation triggers) while keeping human judgment in the decision loop.
- **[planned]** `load-testing-before-scaling-to-new-instrument-universe` — Load-testing infrastructure capacity before scaling a strategy to a significantly larger instrument universe.
- **[planned]** `graceful-degradation-priority-during-partial-outage` — Defining which system components get priority for remaining capacity during a partial infrastructure outage (e.g. risk module over dashboard).
- **[planned]** `post-mortem-culture-and-blameless-review-process` — Establishing a blameless post-mortem process for production incidents so root causes surface honestly rather than being minimized.
- **[planned]** `on-call-rotation-and-escalation-for-trading-systems` — Structuring an on-call rotation with clear escalation paths appropriate to a system where an unresolved incident has an active financial cost.
- **[planned]** `environment-parity-dev-staging-production` — Maintaining close parity between dev/staging/production environments specifically to prevent the 'works in staging' gap common in trading systems.
- **[planned]** `automated-rollback-triggers-on-anomaly-detection` — Automatically triggering a rollback to the previous deployment if post-deploy anomaly detection (unusual order rate, error rate) fires.
- **[planned]** `capacity-planning-for-symbol-universe-growth` — Forward capacity-planning infrastructure scaling as the traded symbol universe grows, rather than reactively scaling after hitting a limit.
- **[planned]** `zero-downtime-database-schema-migrations` — Performing database schema migrations for a live trading system's state store without requiring a trading-hours outage.
- **[planned]** `dependency-pinning-and-reproducible-builds` — Pinning exact dependency versions and building reproducibly, so 'it worked yesterday' incidents from a silent dependency update don't happen.
- **[planned]** `audit-logging-for-configuration-changes` — Logging every configuration change (risk limits, strategy parameters) with who/when/what-changed, independent of code-deployment logs.
- **[planned]** `network-segmentation-for-trading-infrastructure` — Segmenting trading infrastructure network access so a compromised non-critical service (e.g. dashboard) can't reach order-placement infrastructure.

## global-market-integration  _(44 tracked: 2 built, 42 planned)_

- **[BUILT]** `crypto-exchange-api-integration` — see `skills/crypto-exchange-api-integration/SKILL.md`
- **[BUILT]** `forex-broker-integration-oanda-mt5` — see `skills/forex-broker-integration-oanda-mt5/SKILL.md`
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
- **[planned]** `cboe-options-exchange-api-integration` — Integrating with Cboe's options exchange API and its specific complex-order-book conventions.
- **[planned]** `nasdaq-totalview-itch-feed-parsing` — Parsing NASDAQ's TotalView-ITCH binary market-data feed for full order-book reconstruction.
- **[planned]** `nyse-arca-integrated-feed-handling` — Handling NYSE Arca's integrated feed for consolidated US equities data.
- **[planned]** `lse-millennium-exchange-api` — Integrating with the London Stock Exchange's Millennium Exchange trading platform API.
- **[planned]** `deutsche-borse-xetra-api-integration` — Integrating with Deutsche Börse's Xetra trading system for German/European equities.
- **[planned]** `euronext-optiq-market-data-integration` — Integrating with Euronext's Optiq market-data and trading platform across its pan-European venues.
- **[planned]** `b3-brazil-exchange-api-integration` — Integrating with B3 (Brazil's exchange) for Latin American equities and derivatives.
- **[planned]** `jse-south-africa-api-integration` — Integrating with the Johannesburg Stock Exchange's API for South African equities.
- **[planned]** `tase-israel-exchange-api` — Integrating with the Tel Aviv Stock Exchange's API and its specific settlement conventions.
- **[planned]** `korea-exchange-krx-api-integration` — Integrating with the Korea Exchange's API, including its distinct circuit-breaker and price-limit rules.
- **[planned]** `taiwan-stock-exchange-twse-api` — Integrating with the Taiwan Stock Exchange's API for Taiwanese equities.
- **[planned]** `shanghai-shenzhen-connect-programs` — Handling the Stock Connect programs (Shanghai/Shenzhen-Hong Kong) for mainland China equity access via Hong Kong.
- **[planned]** `dubai-financial-market-dfm-api` — Integrating with the Dubai Financial Market's API for UAE equities.
- **[planned]** `moscow-exchange-moex-api-integration` — Integrating with the Moscow Exchange API, noting the distinct sanctions/access-restriction landscape that must be checked independently.
- **[planned]** `borsa-istanbul-api-integration` — Integrating with Borsa Istanbul's API for Turkish equities and derivatives.
- **[planned]** `philippine-stock-exchange-api` — Integrating with the Philippine Stock Exchange's API for Southeast Asian equity access.
- **[planned]** `bursa-malaysia-api-integration` — Integrating with Bursa Malaysia's trading API.
- **[planned]** `idx-indonesia-stock-exchange-api` — Integrating with the Indonesia Stock Exchange (IDX) API.
- **[planned]** `new-zealand-exchange-nzx-api` — Integrating with the New Zealand Exchange's API, including its distinct small-market liquidity considerations.
- **[planned]** `cme-group-fix-api-for-futures` — Using CME Group's FIX API specifically (as distinct from Globex's native binary protocol) for futures order routing.
- **[planned]** `ice-futures-us-eu-integration` — Integrating with ICE Futures US/EU for energy and agricultural futures.
- **[planned]** `lme-london-metal-exchange-integration` — Integrating with the London Metal Exchange's specific ring/electronic trading conventions.
- **[planned]** `deribit-crypto-options-api` — Integrating with Deribit's API, the dominant crypto options venue, including its specific margining model.
- **[planned]** `bybit-derivatives-api-integration` — Integrating with Bybit's derivatives API for crypto perpetuals and options.
- **[planned]** `okx-unified-account-api` — Integrating with OKX's unified-account API model spanning spot, margin, and derivatives.
- **[planned]** `ftx-style-exchange-post-collapse-risk-lessons` — Documenting the specific counterparty and custody risk lessons from the FTX collapse as a checklist for evaluating any centralized crypto exchange.
- **[planned]** `decentralized-exchange-dex-integration-uniswap-style` — Integrating with an AMM-style DEX (Uniswap-family) including slippage/impermanent-loss-aware execution logic distinct from centralized order-book exchanges.
- **[planned]** `cross-chain-bridge-risk-for-multi-chain-strategies` — Assessing cross-chain bridge risk explicitly for any strategy that moves crypto assets between chains.
- **[planned]** `prime-brokerage-multi-venue-consolidation` — Using a prime-broker/prime-of-prime relationship to consolidate access to multiple venues under one credential and margin relationship.
- **[planned]** `fix-protocol-session-management-across-venues` — Managing FIX protocol sessions (logon/heartbeat/sequence-number recovery) consistently across multiple venues with different FIX dialect quirks.
- **[planned]** `market-data-entitlement-and-licensing-per-venue` — Tracking market-data entitlement/licensing requirements per venue, since redistribution or algorithmic-consumption rights vary and are commonly violated unknowingly.

## regulatory-compliance-global  _(38 tracked: 2 built, 36 planned)_

- **[BUILT]** `mifid-ii-algo-trading-compliance-eu` — see `skills/mifid-ii-algo-trading-compliance-eu/SKILL.md`
- **[BUILT]** `pattern-day-trader-rule-compliance-us` — see `skills/pattern-day-trader-rule-compliance-us/SKILL.md`
- **[planned]** `sec-rule-15c3-5-risk-controls-us` — Implementing the pre-trade risk controls required under SEC Rule 15c3-5 (the 'market access rule') for US broker-dealer routed flow.
- **[planned]** `finra-algo-trading-registration-requirements` — Understanding FINRA's algorithmic trading registration and testing requirements for US-based strategies.
- **[planned]** `esma-double-volume-cap-mechanism` — Accounting for ESMA's double volume cap mechanism when trading EU dark-pool venues.
- **[planned]** `uk-fca-algorithmic-trading-systems-controls` — Implementing the systems-and-controls requirements the UK FCA expects of algorithmic trading firms.
- **[planned]** `asic-market-integrity-rules-automated-trading` — Complying with ASIC's market integrity rules for automated order processing in Australia.
- **[planned]** `mas-singapore-algo-trading-guidelines` — Following MAS guidelines on automated trading systems risk management for Singapore-based operations.
- **[planned]** `wash-trade-and-spoofing-self-detection` — Building self-checks that flag a strategy's own order pattern if it could resemble wash trading or spoofing, before a regulator does.
- **[planned]** `best-execution-record-keeping-global` — Maintaining best-execution evidence across jurisdictions with differing regulatory expectations (US Reg NMS, EU MiFID II, etc.).
- **[planned]** `cftc-commodity-pool-operator-registration` — Determining whether a multi-client algo strategy trading futures triggers CFTC commodity-pool-operator registration requirements.
- **[planned]** `canada-iiroc-electronic-trading-rules` — Complying with IIROC's (now CIRO's) electronic trading rules for Canadian markets.
- **[planned]** `hong-kong-sfc-algorithmic-trading-guidelines` — Following Hong Kong SFC's guidelines on the use of algorithmic trading by licensed firms.
- **[planned]** `japan-fsa-high-speed-trading-registration` — Understanding Japan FSA's registration requirements specifically for high-speed trading firms.
- **[planned]** `india-sebi-algo-trading-tagging-requirements` — Complying with SEBI's algo-order tagging and approval requirements for Indian markets (extends the original India-first pass with current specifics).
- **[planned]** `australia-asic-drt-obligations` — Meeting ASIC's Direct Market Access / automated order processing obligations for Australian trading participants.
- **[planned]** `eu-market-abuse-regulation-mar-surveillance` — Building self-surveillance checks aligned with EU Market Abuse Regulation (MAR) requirements around algorithmic order patterns.
- **[planned]** `uk-senior-managers-regime-algo-accountability` — Understanding how the UK's Senior Managers and Certification Regime assigns individual accountability for algorithmic trading systems.
- **[planned]** `us-reg-sho-short-sale-locate-requirements` — Complying with Reg SHO's locate requirements before placing short-sale orders in US equities.
- **[planned]** `us-reg-nms-order-protection-rule-compliance` — Ensuring order routing respects Reg NMS's order-protection (trade-through) rule across US equity venues.
- **[planned]** `eu-short-selling-regulation-disclosure-thresholds` — Tracking EU Short Selling Regulation disclosure thresholds that trigger mandatory position reporting.
- **[planned]** `swiss-finma-algorithmic-trading-expectations` — Understanding FINMA's expectations for algorithmic trading risk controls for Swiss-regulated entities.
- **[planned]** `singapore-mas-notice-on-cyber-hygiene-for-trading-systems` — Complying with MAS's cyber-hygiene notice requirements as they apply to automated trading system infrastructure.
- **[planned]** `cross-border-data-transfer-restrictions-for-trade-data` — Navigating cross-border data-transfer restrictions (e.g. GDPR-adjacent rules) when trade/account data crosses jurisdictions.
- **[planned]** `kyc-aml-considerations-for-algo-trading-entities` — Understanding KYC/AML obligations that apply to the entity operating an algo-trading system, distinct from the trading logic itself.
- **[planned]** `position-limit-reporting-cftc-large-trader` — Meeting CFTC large-trader position-reporting thresholds for US futures positions.
- **[planned]** `eu-benchmark-regulation-for-strategies-referencing-indices` — Understanding EU Benchmark Regulation implications for any strategy that references a regulated benchmark/index.
- **[planned]** `insider-trading-controls-for-alternative-data-usage` — Building controls to ensure alternative-data usage (e.g. web-scraped data) doesn't inadvertently incorporate material non-public information.
- **[planned]** `cross-jurisdiction-regulatory-conflict-resolution` — Resolving cases where two jurisdictions' rules conflict for a strategy trading across both (e.g. differing pre-trade risk-control specifics).
- **[planned]** `record-retention-periods-by-jurisdiction` — Tracking differing minimum record-retention periods for trade/order data across jurisdictions the bot operates in.
- **[planned]** `algo-trading-disclosure-to-exchange-membership` — Understanding exchange-membership-level disclosure obligations (distinct from national regulator obligations) for running algorithmic strategies on a given venue.
- **[planned]** `sanctions-screening-for-counterparties-and-instruments` — Screening counterparties and instruments against sanctions lists, particularly relevant for cross-border/crypto/emerging-market strategies.
- **[planned]** `regulatory-sandbox-programs-for-fintech-testing` — Evaluating regulatory sandbox programs (offered by several regulators) as a lower-friction path for testing novel algo strategies under supervision.
- **[planned]** `conflict-of-interest-disclosure-for-prop-vs-client-flow` — Understanding disclosure obligations where a firm runs both proprietary and client algorithmic flow.
- **[planned]** `annual-compliance-attestation-workflow` — Building a repeatable annual workflow to produce the compliance attestations several regulatory regimes (echoing mifid-ii-algo-trading-compliance-eu's RTS 6 self-assessment) require.
- **[planned]** `regulatory-change-monitoring-service-integration` — Integrating a regulatory-change monitoring service/feed so upcoming rule changes are flagged before they take effect, not discovered after.
- **[planned]** `data-localization-requirements-for-trade-records` — Understanding data-localization requirements (certain jurisdictions require trade records stored within-country) affecting infrastructure design.
- **[planned]** `algorithmic-trading-firm-licensing-thresholds` — Understanding the activity thresholds (order volume, message rate) at which a firm crosses into requiring formal algorithmic-trading-specific licensing in a given jurisdiction.

## multi-asset-derivatives  _(28 tracked: 1 built, 27 planned)_

- **[BUILT]** `options-margin-span-calculation-global` — see `skills/options-margin-span-calculation-global/SKILL.md`
- **[planned]** `futures-contract-roll-automation` — Automatically rolling futures positions ahead of expiry without a naive same-day roll causing slippage spikes.
- **[planned]** `options-greeks-real-time-portfolio-aggregation` — Aggregating delta/gamma/vega/theta across a multi-leg options portfolio in real time for risk monitoring.
- **[planned]** `calendar-spread-and-multi-leg-order-atomicity` — Ensuring multi-leg option/futures spread orders either fill entirely or roll back, not partially.
- **[planned]** `cross-margining-across-asset-classes` — Handling brokers that offer cross-margining between equities, options, and futures without misreporting available capital.
- **[planned]** `perpetual-futures-funding-rate-handling` — Accounting for perpetual futures funding-rate payments/receipts in live P&L for crypto derivatives.
- **[planned]** `fx-forward-and-swap-position-tracking` — Tracking FX forward/swap positions and their forward-point carry correctly, distinct from spot P&L.
- **[planned]** `variance-swap-and-volatility-derivative-pricing` — Pricing and risk-managing variance swaps and other volatility derivatives distinct from vanilla options.
- **[planned]** `credit-default-swap-basics-for-algo-context` — Understanding CDS basics sufficiently to incorporate credit-spread signals into a broader multi-asset strategy.
- **[planned]** `interest-rate-swap-exposure-in-multi-asset-portfolios` — Accounting for interest-rate-swap exposure (where used for hedging) in an overall portfolio risk view.
- **[planned]** `commodity-futures-storage-and-carry-cost-modeling` — Modeling storage/carry costs specific to physical commodity futures, distinct from financial futures.
- **[planned]** `weather-derivatives-and-niche-instrument-handling` — Handling niche derivative types (weather derivatives, freight derivatives) that don't fit standard equity/futures/options tooling assumptions.
- **[planned]** `binary-options-regulatory-and-risk-considerations` — Understanding the distinct (and in many jurisdictions restricted) regulatory status of binary options before any related strategy work.
- **[planned]** `warrants-and-structured-product-integration` — Integrating warrants and structured products, which often have issuer-specific (not exchange-standardized) terms requiring bespoke handling.
- **[planned]** `convertible-bond-arbitrage-data-requirements` — Understanding the specific multi-asset data requirements (bond terms, equity price, credit spread, volatility) for convertible-bond arbitrage strategies.
- **[planned]** `dividend-futures-and-forward-modeling` — Modeling dividend futures/forwards for strategies that trade dividend risk separately from underlying equity risk.
- **[planned]** `vix-and-volatility-index-derivative-strategies` — Handling VIX futures/options' specific term-structure and contango/backwardation dynamics distinct from standard equity options.
- **[planned]** `single-stock-futures-where-available` — Handling single-stock futures (available in some markets, e.g. certain European and Indian exchanges) as a distinct instrument class from equity options.
- **[planned]** `total-return-swap-synthetic-exposure` — Understanding total-return-swaps as a synthetic-exposure mechanism and its distinct counterparty/funding considerations versus direct ownership.
- **[planned]** `cross-asset-correlation-regime-shifts` — Monitoring for cross-asset-class correlation regime shifts (e.g. equity-bond correlation flipping sign) that affect multi-asset portfolio risk assumptions.
- **[planned]** `physical-vs-cash-settlement-handling` — Handling the operational difference between physically-settled and cash-settled derivatives, particularly the risk of unintended physical delivery on expiry.
- **[planned]** `exchange-for-physical-efp-transactions` — Understanding Exchange-for-Physical (EFP) transaction mechanics for futures-to-physical conversions.
- **[planned]** `multi-leg-strategy-margin-optimization` — Optimizing multi-leg strategy construction specifically to reduce margin requirement (per options-margin-span-calculation-global) while preserving the intended risk profile.
- **[planned]** `american-vs-european-style-option-exercise-handling` — Handling the operational difference between American-style (exercisable anytime) and European-style (exercisable only at expiry) options correctly in position management.
- **[planned]** `early-exercise-assignment-risk-management` — Managing early-exercise/assignment risk for American-style short options positions, including dividend-driven early-exercise scenarios.
- **[planned]** `futures-expiry-week-liquidity-and-volatility-handling` — Handling the distinct liquidity and volatility characteristics common in futures/options expiry week.
- **[planned]** `quanto-options-and-cross-currency-derivative-structures` — Understanding quanto options and other cross-currency derivative structures where the payoff currency differs from the underlying's natural currency.
- **[planned]** `options-pin-risk-management-at-expiry` — Managing pin-risk (uncertainty near expiry when the underlying settles very close to a strike) for short options positions.

## execution-algorithms  _(33 tracked: 1 built, 32 planned)_

- **[BUILT]** `execution-algo-twap-vwap-slicing` — see `skills/execution-algo-twap-vwap-slicing/SKILL.md`
- **[planned]** `participation-of-volume-pov-execution` — Implementing a POV execution algorithm that scales order slicing to real-time observed volume.
- **[planned]** `implementation-shortfall-minimization` — Designing an execution schedule that minimizes implementation shortfall versus the arrival price benchmark.
- **[planned]** `iceberg-order-simulation-and-detection` — Simulating iceberg/hidden-quantity orders in a backtest, and detecting when a strategy is inadvertently signaling its own size.
- **[planned]** `smart-order-routing-across-venues` — Routing an order across multiple venues/exchanges to minimize cost when a single venue lacks sufficient liquidity.
- **[planned]** `adaptive-execution-under-volatility-spikes` — Switching an execution algorithm's aggressiveness in response to a real-time volatility spike detector.
- **[planned]** `arrival-price-benchmark-execution-algo` — Building an execution algorithm explicitly benchmarked against arrival price rather than TWAP/VWAP, appropriate for urgency-driven orders.
- **[planned]** `dark-pool-routing-logic` — Routing a portion of large orders to dark pools to reduce market impact, with logic for detecting adverse selection in dark-pool fills.
- **[planned]** `liquidity-seeking-algorithm-across-lit-and-dark-venues` — Building a liquidity-seeking algorithm that dynamically allocates order flow across lit and dark venues based on observed fill quality.
- **[planned]** `close-auction-participation-strategy` — Participating correctly in an exchange's closing auction, including its distinct order-type and timing rules versus continuous trading.
- **[planned]** `opening-auction-imbalance-based-execution` — Using published opening-auction imbalance information to inform execution timing around the open.
- **[planned]** `peg-order-types-for-passive-execution` — Using pegged order types (mid-peg, primary-peg) for passive execution that tracks the market without requiring constant re-pricing logic.
- **[planned]** `execution-algo-parameter-optimization-via-backtest` — Backtesting execution-algorithm parameter choices (interval count, aggressiveness) against historical intraday data, mirroring the discipline in walk-forward-validation-setup.
- **[planned]** `cross-venue-latency-arbitrage-defensive-design` — Designing execution logic defensively against being on the losing side of cross-venue latency arbitrage by other participants.
- **[planned]** `algo-wheel-broker-execution-quality-comparison` — Building an 'algo wheel' that rotates order flow across multiple execution algorithms/brokers and tracks comparative execution quality.
- **[planned]** `conditional-order-logic-for-execution-triggers` — Building conditional execution logic (e.g. only begin slicing once a volume or volatility condition is met) rather than a purely time-triggered schedule.
- **[planned]** `execution-cost-model-recalibration-cadence` — Defining how often the execution-cost model (informing slicing decisions) is recalibrated against realized TCA data.
- **[planned]** `multi-order-netting-before-routing` — Netting multiple internal orders in the same instrument/direction before routing to market, to avoid unnecessarily crossing the spread against oneself.
- **[planned]** `auction-only-order-types-for-illiquid-names` — Using auction-only order types specifically for illiquid instruments where continuous-session execution would incur excessive impact.
- **[planned]** `post-trade-execution-quality-scorecard` — Building a standardized post-trade scorecard comparing achieved execution against multiple benchmarks (arrival, TWAP, VWAP, close) for ongoing algo-selection decisions.
- **[planned]** `smart-order-router-failover-on-venue-outage` — Ensuring a smart order router fails over gracefully to alternate venues if a primary venue experiences an outage mid-execution.
- **[planned]** `minimum-fill-size-and-lot-rounding-logic` — Handling minimum-fill-size and lot-rounding constraints correctly when a slicing schedule's computed child-order size falls below an exchange's minimum.
- **[planned]** `execution-algorithm-kill-switch-integration` — Ensuring execution algorithms respect the same kill-switch signal as the rest of the system (extends kill-switch-and-drawdown-circuit-breakers to in-flight multi-child-order executions specifically).
- **[planned]** `queue-position-modeling-for-passive-orders` — Modeling expected queue position for passive limit orders to decide when to re-price versus wait, for latency-tolerant strategies.
- **[planned]** `execution-algo-behavior-under-halted-instrument` — Defining explicit behavior for an in-progress execution algorithm if the underlying instrument is halted mid-execution.
- **[planned]** `cross-asset-hedge-execution-synchronization` — Synchronizing execution of a primary position and its hedge (e.g. an option and its delta-hedge in the underlying) to minimize the window of unhedged exposure.
- **[planned]** `algo-parameter-defaults-by-instrument-liquidity-tier` — Defining sensible default execution-algorithm parameters segmented by instrument liquidity tier rather than one-size-fits-all defaults.
- **[planned]** `execution-slippage-attribution-timing-vs-sizing` — Attributing realized execution slippage to timing decisions versus sizing decisions separately, to know which part of the algorithm to improve.
- **[planned]** `iceberg-order-native-broker-support-vs-simulation` — Deciding whether to use a broker's native iceberg order type versus simulating one via the bot's own slicing logic, based on feature/reliability tradeoffs.
- **[planned]** `multi-day-execution-schedules-for-very-large-orders` — Extending TWAP/VWAP-style scheduling across multiple trading days for orders too large to complete in a single session without excessive impact.
- **[planned]** `execution-algorithm-regression-testing-suite` — Building a regression-test suite for execution-algorithm logic itself, since a scheduling bug here directly costs money on every single trade it touches.
- **[planned]** `post-only-limit-repricing-under-fast-markets` — Handling post-only limit order repricing correctly during fast-moving markets where naive re-pricing can chase price unprofitably.
- **[planned]** `execution-venue-fee-tier-optimization` — Optimizing venue selection/order type to reach favorable fee tiers (e.g. maker-rebate thresholds) without compromising execution quality for the sake of fee optimization alone.

## data-management-global  _(37 tracked: 3 built, 34 planned)_

- **[BUILT]** `global-exchange-holiday-calendar-handling` — see `skills/global-exchange-holiday-calendar-handling/SKILL.md`
- **[BUILT]** `multi-currency-pnl-and-fx-conversion` — see `skills/multi-currency-pnl-and-fx-conversion/SKILL.md`
- **[BUILT]** `multi-timezone-session-scheduling` — see `skills/multi-timezone-session-scheduling/SKILL.md`
- **[planned]** `daylight-saving-time-transition-handling` — Handling DST transitions correctly for exchanges/brokers whose local trading hours shift twice a year.
- **[planned]** `point-in-time-fundamentals-data-joins` — Joining fundamentals/reference data by as-of publish date rather than calendar date, across global data vendors.
- **[planned]** `reference-data-symbol-mapping-across-vendors` — Mapping instrument identifiers (ISIN, CUSIP, ticker, exchange-specific codes) consistently across data vendors and brokers.
- **[planned]** `historical-tick-data-storage-and-compaction` — Storing and compacting historical tick data at scale without exhausting storage or query latency budgets.
- **[planned]** `isin-cusip-sedol-cross-reference-service` — Building a reliable cross-reference service between ISIN, CUSIP, SEDOL, and exchange-local ticker identifiers.
- **[planned]** `vendor-specific-adjustment-methodology-reconciliation` — Reconciling differing corporate-action adjustment methodologies between data vendors before merging their data.
- **[planned]** `real-time-vs-delayed-data-entitlement-handling` — Correctly handling the distinction between real-time and (commonly 15-minute) delayed data entitlements, ensuring a bot never mistakes delayed data for real-time.
- **[planned]** `historical-data-backfill-rate-limit-management` — Managing rate limits specifically for large historical-data backfill jobs, distinct from live-trading API rate limits.
- **[planned]** `market-data-cost-optimization-tiered-subscriptions` — Optimizing which instruments get real-time (paid) versus delayed (free/cheap) data subscriptions based on actual strategy need.
- **[planned]** `reference-data-golden-source-designation` — Designating a single 'golden source' for each reference-data field (sector classification, share count) when multiple vendors disagree.
- **[planned]** `data-quality-monitoring-dashboard` — Building a dashboard that surfaces data-quality anomalies (gaps, stale prints, outliers) across all ingested feeds for proactive detection.
- **[planned]** `options-chain-data-normalization-across-vendors` — Normalizing options-chain data (strike conventions, expiry-date formats) across vendors with differing schemas.
- **[planned]** `corporate-action-event-calendar-integration` — Integrating a corporate-action event calendar (splits, dividends, mergers) to pre-empt rather than react to adjustment-related backtest/live discrepancies.
- **[planned]** `currency-pair-quoting-convention-normalization` — Normalizing currency-pair quoting conventions (which currency is base vs quote) consistently across data sources, since this flips silently between vendors.
- **[planned]** `exchange-tick-size-regime-tracking` — Tracking exchange-specific (and sometimes price-tier-dependent) tick-size regimes correctly for order-price validation.
- **[planned]** `historical-order-book-reconstruction-from-message-logs` — Reconstructing a historical order book from raw message logs for backtest realism beyond simple OHLCV bars.
- **[planned]** `data-pipeline-schema-contract-testing` — Contract-testing data-pipeline schemas so an upstream vendor's silent schema change is caught before it corrupts downstream processing.
- **[planned]** `point-in-time-index-constituent-tracking` — Tracking historical index constituents (not just current membership) for accurate survivorship-bias-free backtest universes.
- **[planned]** `market-data-latency-monitoring-per-vendor` — Monitoring actual observed latency per data vendor/feed, since advertised 'real-time' can vary meaningfully in practice.
- **[planned]** `unicode-and-encoding-issues-in-global-instrument-names` — Handling encoding issues (non-ASCII characters in company/instrument names) correctly across data pipelines spanning multiple countries.
- **[planned]** `data-retention-policy-and-storage-tiering` — Defining a data-retention policy with storage tiering (hot/warm/cold) balancing query-latency needs against storage cost for years of tick history.
- **[planned]** `reference-data-change-notification-pipeline` — Building a notification pipeline for reference-data changes (ticker renames, exchange migrations) that could silently break symbol-keyed logic elsewhere.
- **[planned]** `cross-vendor-timestamp-precision-reconciliation` — Reconciling differing timestamp precision (millisecond vs microsecond vs second) across vendors feeding the same pipeline.
- **[planned]** `synthetic-continuous-futures-contract-construction` — Constructing a synthetic continuous futures contract series (back-adjusted or ratio-adjusted across rolls) for backtesting futures strategies.
- **[planned]** `options-implied-volatility-surface-construction` — Constructing and validating a smooth implied-volatility surface from raw options-chain quotes for use in pricing and backtesting.
- **[planned]** `data-vendor-contractual-usage-restriction-tracking` — Tracking contractual usage restrictions per data vendor (e.g. no algorithmic redistribution, internal-use-only clauses) to avoid unintentional contract violation.
- **[planned]** `multi-source-price-reconciliation-tie-breaking` — Defining explicit tie-breaking rules when two 'authoritative' data sources disagree on a price at the same timestamp.
- **[planned]** `global-macro-economic-calendar-integration` — Integrating a global economic-release calendar (rate decisions, employment data) for strategies that need to avoid or specifically target these events.
- **[planned]** `data-lineage-tracking-for-audit-and-debugging` — Tracking full data lineage (which vendor, which pipeline version, which transformation) for any figure that feeds a live trading decision, for post-incident debugging.
- **[planned]** `market-data-simulator-for-offline-development` — Building a market-data simulator that lets developers work on strategy code offline without a live feed subscription, using realistic synthetic or replayed data.
- **[planned]** `instrument-universe-change-detection-and-alerting` — Alerting when the tradeable instrument universe changes unexpectedly (new listings, delistings, ticker changes) rather than silently adapting or silently breaking.
- **[planned]** `cross-region-data-replication-lag-monitoring` — Monitoring replication lag for data stores replicated across regions, since a stale replica silently serving reads can misinform a strategy.
- **[planned]** `options-chain-expiry-cycle-conventions-by-exchange` — Tracking exchange-specific options expiry-cycle conventions (weekly/monthly/quarterly availability) which vary significantly by exchange and underlying.
- **[planned]** `vendor-outage-fallback-data-source-hierarchy` — Defining an explicit fallback hierarchy of data sources to use if a primary vendor has an outage, tested rather than assumed to work.

## crypto-custody-security  _(29 tracked: 1 built, 28 planned)_

- **[BUILT]** `crypto-wallet-key-custody-security` — see `skills/crypto-wallet-key-custody-security/SKILL.md`
- **[planned]** `hot-cold-wallet-split-for-trading-bots` — Splitting a crypto trading bot's operational hot-wallet balance from cold storage to bound loss from a compromise.
- **[planned]** `exchange-withdrawal-whitelist-enforcement` — Enforcing withdrawal address whitelisting so a compromised bot credential can't exfiltrate funds to an arbitrary address.
- **[planned]** `multi-signature-approval-for-large-transfers` — Requiring multi-sig approval for any transfer above a threshold, independent of the bot's own logic.
- **[planned]** `hardware-security-module-hsm-for-signing-keys` — Using a dedicated HSM for signing transactions rather than software-based key storage, for institutional-scale crypto custody.
- **[planned]** `shamir-secret-sharing-for-key-backup` — Using Shamir's Secret Sharing to split a key backup across multiple custodians/locations, avoiding a single point of failure or compromise.
- **[planned]** `custodial-vs-non-custodial-tradeoff-assessment` — Assessing the tradeoffs between using a third-party custodian versus self-custody for a given operational scale and risk tolerance.
- **[planned]** `smart-contract-audit-requirements-before-defi-integration` — Requiring a completed third-party smart-contract audit before integrating with any DeFi protocol as a counterparty.
- **[planned]** `exchange-proof-of-reserves-verification` — Verifying an exchange's published proof-of-reserves attestation independently rather than accepting it at face value.
- **[planned]** `key-rotation-schedule-for-hot-wallet-keys` — Defining and following a key-rotation schedule for hot-wallet operational keys, distinct from one-time initial setup.
- **[planned]** `insurance-coverage-assessment-for-custodied-crypto` — Assessing what insurance coverage (if any) actually applies to custodied crypto assets, since coverage terms vary widely and are often narrower than assumed.
- **[planned]** `air-gapped-signing-workflow-for-cold-storage` — Implementing an air-gapped signing workflow for cold-storage transactions, ensuring the signing device never has network connectivity.
- **[planned]** `withdrawal-velocity-limits-and-anomaly-detection` — Setting withdrawal velocity limits (max value/frequency in a time window) with anomaly detection distinct from simple threshold breaches.
- **[planned]** `multi-party-computation-mpc-custody-solutions` — Evaluating MPC-based custody solutions as an alternative to traditional multi-sig for distributing key control without a single complete key ever existing.
- **[planned]** `smart-contract-approval-scope-minimization` — Minimizing and periodically auditing token-approval scopes granted to smart contracts, since unlimited approvals are a common attack vector.
- **[planned]** `phishing-resistant-authentication-for-custody-access` — Using phishing-resistant authentication (hardware security keys, not SMS/email OTP) for any human access to custody infrastructure.
- **[planned]** `custody-solution-vendor-due-diligence-checklist` — A structured due-diligence checklist for evaluating a third-party custody vendor before entrusting them with operational funds.
- **[planned]** `on-chain-transaction-monitoring-for-anomalies` — Monitoring on-chain transaction activity in real time for anomalies (unexpected destination, unusual size) as a detection layer independent of application-level logging.
- **[planned]** `recovery-plan-for-lost-or-compromised-keys` — Maintaining a tested recovery plan for lost or compromised keys, distinct from the initial custody design, since custody design alone doesn't guarantee recoverability.
- **[planned]** `segregation-of-duties-for-custody-operations` — Enforcing segregation of duties (no single person can both initiate and approve a large transfer) for custody operations.
- **[planned]** `cross-chain-address-reuse-privacy-risk` — Assessing privacy/security risk from address reuse across chains and transactions, relevant for operational security of a trading entity's on-chain footprint.
- **[planned]** `custody-solution-uptime-and-liveness-guarantees` — Evaluating a custody solution's actual uptime/liveness guarantees, since a highly secure but frequently-unavailable custody setup creates its own operational risk.
- **[planned]** `regulatory-custody-requirements-by-jurisdiction` — Tracking jurisdiction-specific regulatory requirements for crypto custody (where they exist) as a compliance layer on top of technical security design.
- **[planned]** `post-incident-forensics-for-suspected-key-compromise` — Defining a forensics process for a suspected (even if unconfirmed) key compromise, including immediate containment steps before full confirmation.
- **[planned]** `cold-storage-geographic-distribution-strategy` — Distributing cold-storage key shares/backups geographically to protect against a localized disaster (fire, natural disaster) affecting a single location.
- **[planned]** `vendor-lock-in-risk-for-proprietary-custody-formats` — Assessing vendor lock-in risk where a custody solution uses a proprietary (non-standard) key format that complicates migration to another provider.
- **[planned]** `employee-offboarding-procedure-for-custody-access` — Defining a strict, tested offboarding procedure to revoke a departing employee's custody-related access immediately.
- **[planned]** `third-party-custody-audit-report-review-cadence` — Establishing a recurring cadence for reviewing a custody vendor's third-party security audit reports (SOC 2 or equivalent), not just at initial vendor selection.
- **[planned]** `test-transaction-verification-before-large-transfers` — Requiring a small test transaction before any large transfer to a new or infrequently-used destination address.

## portfolio-multi-strategy  _(30 tracked: 0 built, 30 planned)_

- **[planned]** `cross-strategy-correlation-monitoring` — Monitoring correlation between concurrently-running strategies to detect unintended aggregate concentration.
- **[planned]** `capital-reallocation-based-on-live-performance` — Reallocating capital between strategies based on live (not just backtested) rolling performance, with safeguards against reallocation churn.
- **[planned]** `strategy-lifecycle-retirement-criteria` — Defining explicit, pre-agreed criteria for retiring a live strategy rather than letting it run indefinitely on inertia.
- **[planned]** `strategy-correlation-matrix-live-recomputation` — Recomputing the live strategy-correlation matrix on a defined schedule, mirroring the discipline in correlation-aware-exposure-limits but at the strategy level rather than instrument level.
- **[planned]** `risk-parity-allocation-across-strategies` — Allocating capital across strategies using a risk-parity approach (equalizing risk contribution) rather than equal-dollar allocation.
- **[planned]** `strategy-performance-attribution-vs-market-beta` — Decomposing each strategy's returns into market-beta-driven and idiosyncratic-alpha-driven components for honest cross-strategy comparison.
- **[planned]** `portfolio-level-stop-loss-independent-of-strategy-stops` — Implementing a portfolio-level stop-loss that can halt all strategies simultaneously, independent of and in addition to each strategy's own internal stops.
- **[planned]** `new-strategy-onboarding-checklist` — A standardized checklist (data requirements, risk-control integration, paper-trading gate) for onboarding a new strategy into a multi-strategy portfolio.
- **[planned]** `strategy-capacity-estimation-before-scaling-capital` — Estimating a strategy's capacity (the capital level beyond which its own market impact degrades returns) before scaling allocated capital.
- **[planned]** `cross-strategy-shared-infrastructure-resource-contention` — Managing resource contention (API rate limits, compute) when multiple strategies share underlying infrastructure, so one strategy's load doesn't degrade another's.
- **[planned]** `strategy-decommissioning-and-position-unwind-procedure` — A defined procedure for unwinding a retired strategy's positions in an orderly (not panic-liquidation) manner.
- **[planned]** `portfolio-construction-with-transaction-cost-awareness` — Incorporating transaction-cost estimates directly into portfolio-construction/rebalancing decisions, not just as a post-hoc backtest adjustment.
- **[planned]** `meta-strategy-signal-arbitration` — Building an arbitration layer when multiple strategies generate conflicting signals for the same instrument at the same time.
- **[planned]** `strategy-specific-vs-shared-risk-budget-allocation` — Deciding which risk controls apply per-strategy versus at the shared-portfolio level, and ensuring both layers are actually enforced rather than assuming one implies the other.
- **[planned]** `rebalancing-frequency-optimization-cost-vs-drift` — Optimizing portfolio rebalancing frequency, trading off tracking-drift cost against transaction cost.
- **[planned]** `strategy-performance-decay-detection-vs-market-wide-decay` — Distinguishing a strategy-specific performance decay from a market-wide phenomenon affecting all strategies similarly, to correctly target remediation.
- **[planned]** `capital-efficiency-across-cross-margined-strategies` — Optimizing capital efficiency when multiple strategies share a cross-margined account, without one strategy's margin usage silently starving another.
- **[planned]** `strategy-committee-governance-for-capital-allocation-decisions` — Establishing a lightweight governance process (even for a small team) for capital-allocation decisions across strategies, rather than ad hoc reallocation.
- **[planned]** `benchmark-portfolio-for-multi-strategy-performance-context` — Maintaining a simple benchmark portfolio to contextualize whether the full multi-strategy portfolio's complexity is actually adding value over a simpler alternative.
- **[planned]** `tail-correlation-between-strategies-under-stress` — Testing whether strategies assumed uncorrelated under normal conditions become correlated specifically under stress/tail scenarios (a common multi-strategy blind spot).
- **[planned]** `strategy-specific-data-dependency-mapping` — Mapping each strategy's specific data dependencies so a data-vendor outage's impact across the multi-strategy portfolio can be assessed quickly.
- **[planned]** `incremental-capital-deployment-for-new-strategies` — Deploying capital to a newly-promoted strategy incrementally (per the reduced-initial-size principle in paper-to-live-promotion-checklist) rather than immediately at full target allocation.
- **[planned]** `cross-strategy-tax-lot-optimization` — Optimizing tax-lot selection across strategies sharing a tax entity, where applicable, without compromising each strategy's independent decision logic.
- **[planned]** `strategy-level-kill-switch-vs-portfolio-level-kill-switch` — Ensuring a strategy-level kill-switch trigger doesn't inadvertently unwind unrelated strategies, while a portfolio-level kill-switch correctly does.
- **[planned]** `multi-strategy-reporting-consolidation-for-stakeholders` — Consolidating multi-strategy performance/risk reporting into a single coherent view for stakeholders who don't need per-strategy granularity.
- **[planned]** `strategy-research-to-production-pipeline-governance` — Governing the pipeline from research idea to production strategy with defined gates, extending paper-to-live-promotion-checklist to the earlier research-stage decision points.
- **[planned]** `opportunity-cost-tracking-for-idle-capital` — Tracking the opportunity cost of capital held idle (e.g. as a risk buffer) across the portfolio, to periodically reassess whether buffer sizing is still appropriate.
- **[planned]** `cross-strategy-signal-reuse-and-licensing` — Managing cases where one strategy's computed features/signals are reused by another, including versioning so a change to the shared signal doesn't silently affect multiple strategies at once.
- **[planned]** `strategy-underperformance-remediation-decision-tree` — A structured decision tree (investigate → adjust → reduce size → retire) for responding to strategy underperformance, rather than ad hoc case-by-case reactions.
- **[planned]** `portfolio-stress-test-including-liquidity-crunch-scenarios` — Stress-testing the full multi-strategy portfolio against scenarios combining price shock with a simultaneous liquidity crunch across correlated instruments.

## market-microstructure-latency  _(24 tracked: 0 built, 24 planned)_

- **[planned]** `colocation-latency-budget-accounting` — Accounting for colocation and network latency budgets when a strategy's edge depends on sub-millisecond response time.
- **[planned]** `clock-synchronization-ptp-for-trading-hosts` — Using PTP (Precision Time Protocol) instead of NTP for trading-host clock sync where microsecond accuracy matters.
- **[planned]** `tick-to-trade-latency-measurement` — Measuring true tick-to-trade latency end to end, not just the strategy's own compute time.
- **[planned]** `order-book-microstructure-signal-research` — Researching order-book microstructure signals (queue dynamics, order-flow imbalance) as a distinct signal class from price-based technical signals.
- **[planned]** `exchange-fee-tier-and-rebate-structure-analysis` — Analyzing exchange fee-tier and rebate structures in detail, since these materially affect the true profitability of high-turnover strategies.
- **[planned]** `market-maker-vs-taker-strategy-classification` — Classifying a strategy's own behavior as predominantly maker or taker, and understanding the fee/risk implications of each posture.
- **[planned]** `adverse-selection-measurement-for-passive-orders` — Measuring realized adverse selection on filled passive orders (did the market move against the fill immediately after) to assess passive-order strategy quality.
- **[planned]** `latency-arbitrage-defensive-order-sizing` — Sizing orders defensively to limit exposure to latency arbitrage by faster participants reacting to the same signal.
- **[planned]** `co-location-provider-selection-and-network-topology` — Evaluating co-location provider options and network topology (cross-connects, switch fabric) for latency-sensitive infrastructure.
- **[planned]** `fpga-based-market-data-processing-evaluation` — Evaluating FPGA-based market-data processing as an option for the lowest-latency tier of a strategy, including the engineering cost/benefit tradeoff versus software-only approaches.
- **[planned]** `microwave-vs-fiber-network-links-for-cross-market-latency` — Understanding the microwave-vs-fiber network-link tradeoff (speed vs reliability) for cross-market latency-sensitive strategies.
- **[planned]** `exchange-matching-engine-behavior-under-load` — Understanding how a specific exchange's matching engine behavior (e.g. FIFO vs pro-rata allocation) changes strategy design for that venue.
- **[planned]** `tick-size-pilot-program-impact-assessment` — Assessing the impact of tick-size pilot programs or regime changes on strategies sensitive to minimum price increments.
- **[planned]** `message-rate-limit-vs-latency-tradeoff-tuning` — Tuning strategy message rate against exchange-imposed message-rate limits (which can carry fee penalties for excessive order-to-trade ratios) while preserving latency-sensitive responsiveness.
- **[planned]** `order-to-trade-ratio-fee-penalty-avoidance` — Avoiding order-to-trade ratio fee penalties (charged by several exchanges for excessive cancel/replace activity relative to executed trades) through deliberate order-management design.
- **[planned]** `microstructure-noise-filtering-for-hf-signals` — Filtering microstructure noise appropriately when building high-frequency signals, distinct from the noise-handling appropriate at lower frequencies.
- **[planned]** `latency-monitoring-percentile-based-slas` — Monitoring latency using percentile-based SLAs (p50/p99/p999) rather than only average latency, since tail latency often matters more for strategy correctness.
- **[planned]** `clock-drift-monitoring-alerting-thresholds` — Setting explicit alerting thresholds for clock drift on trading hosts, given how directly clock accuracy affects latency-sensitive strategy correctness.
- **[planned]** `exchange-gateway-redundancy-and-failover-testing` — Testing failover between redundant exchange gateway connections under simulated primary-gateway failure conditions.
- **[planned]** `network-jitter-impact-on-strategy-performance` — Quantifying network jitter's (not just average latency's) impact on strategy performance for latency-sensitive strategies.
- **[planned]** `hardware-timestamping-vs-software-timestamping-accuracy` — Comparing hardware-level versus software-level timestamping accuracy for strategies where the difference materially affects signal timing.
- **[planned]** `matching-engine-throttle-and-message-gapping-detection` — Detecting exchange-side message throttling or gapping under high load, distinct from the bot's own client-side rate limiting.
- **[planned]** `strategy-latency-budget-decomposition` — Decomposing a strategy's total tick-to-trade latency budget into its component stages (feed handler, strategy logic, risk check, order gateway) to target optimization effort correctly.
- **[planned]** `exchange-self-match-prevention-configuration` — Configuring exchange-level self-match-prevention correctly across venues with differing default behaviors, extending the self-trade-prevention concern beyond crypto exchanges to traditional venues.

## quant-research-alt-data  _(20 tracked: 0 built, 20 planned)_

- **[planned]** `satellite-imagery-based-signal-research` — Researching signal construction from satellite-imagery alternative data (e.g. parking-lot traffic, shipping activity) with appropriate lag/availability constraints.
- **[planned]** `credit-card-transaction-data-signal-construction` — Constructing signals from aggregated, anonymized credit-card transaction data while respecting data-provider licensing and privacy constraints.
- **[planned]** `web-scraped-sentiment-data-pipeline` — Building a compliant web-scraping pipeline for sentiment-relevant text data, respecting site terms of service and rate limits.
- **[planned]** `supply-chain-data-for-earnings-prediction` — Using supply-chain relationship data (supplier/customer networks) as a feature for earnings-related signal research.
- **[planned]** `google-trends-and-search-volume-signal-research` — Researching signal construction from search-volume/trends data, accounting for its own reporting lag and normalization quirks.
- **[planned]** `social-media-sentiment-signal-with-bot-filtering` — Building social-media sentiment signals with explicit bot/spam-account filtering, since raw sentiment volume is heavily gameable.
- **[planned]** `job-posting-data-as-a-growth-signal` — Using aggregated job-posting data as a leading indicator for company growth/hiring-trend signals.
- **[planned]** `options-flow-unusual-activity-detection` — Detecting unusual options order-flow activity (volume/open-interest anomalies) as a signal input, with care to avoid overfitting to noise.
- **[planned]** `insider-transaction-filing-signal-research` — Researching signal construction from public insider-transaction filings, with correct point-in-time filing-date (not transaction-date) alignment.
- **[planned]** `patent-filing-data-for-innovation-signal-research` — Using patent-filing data as a long-horizon innovation/R&D-intensity signal for equity research.
- **[planned]** `esg-data-signal-research-and-vendor-comparison` — Comparing ESG data across vendors (which frequently disagree substantially) before incorporating as a research signal.
- **[planned]** `app-download-and-usage-data-for-consumer-companies` — Using app-download/usage data as an alternative-data signal for consumer-facing public companies.
- **[planned]** `weather-data-signal-research-for-commodity-strategies` — Researching weather-data-driven signals for agricultural/energy commodity strategies.
- **[planned]** `central-bank-communication-nlp-analysis` — Applying NLP analysis to central-bank communications (meeting minutes, speeches) for macro-signal research, with awareness of well-known overfitting pitfalls in this literature.
- **[planned]** `earnings-call-transcript-nlp-signal-research` — Building NLP-based signals from earnings-call transcripts, distinguishing management tone/sentiment from the literal content.
- **[planned]** `alternative-data-vendor-due-diligence-checklist` — A due-diligence checklist for evaluating a new alternative-data vendor's data quality, legal compliance, and point-in-time integrity before integration.
- **[planned]** `backtesting-alt-data-strategies-with-realistic-availability-lag` — Ensuring alt-data-driven backtests use the data's actual historical availability lag (which is often substantial and vendor-specific), not its nominal event date.
- **[planned]** `research-environment-vs-production-environment-parity` — Maintaining parity between the quant research environment (notebooks, ad hoc scripts) and the production feature-computation path, extending feature-store-for-live-and-backtest-parity to the research stage.
- **[planned]** `factor-research-multiple-testing-correction` — Applying multiple-testing correction (e.g. controlling false discovery rate) when researching many candidate factors, to avoid presenting a spuriously significant factor as real.
- **[planned]** `research-idea-pipeline-tracking-and-prioritization` — Tracking and prioritizing a pipeline of research ideas systematically, rather than ad hoc exploration with no record of what's already been tried and rejected.

## tax-accounting-reporting-global  _(16 tracked: 0 built, 16 planned)_

- **[planned]** `wash-sale-rule-tracking-us` — Tracking wash-sale rule violations (US) across a high-turnover strategy's trades to correctly disallow losses for tax purposes.
- **[planned]** `fifo-vs-specific-lot-tax-accounting-methods` — Implementing and choosing between FIFO and specific-lot-identification tax accounting methods correctly per applicable jurisdiction rules.
- **[planned]** `mark-to-market-election-for-active-traders-us` — Understanding the US mark-to-market (Section 475) election's implications for active-trader tax treatment and its accounting-system requirements.
- **[planned]** `crypto-transaction-tax-lot-tracking` — Tracking tax lots for crypto transactions specifically, including the added complexity of frequent small transactions and cross-chain movements.
- **[planned]** `multi-jurisdiction-tax-residency-implications` — Understanding how multi-jurisdiction tax residency affects reporting obligations for a globally-distributed trading operation.
- **[planned]** `1099-b-and-broker-tax-reporting-reconciliation` — Reconciling broker-issued tax forms (e.g. US 1099-B) against internal trade records to catch discrepancies before filing.
- **[planned]** `vat-gst-treatment-of-trading-related-services` — Understanding VAT/GST treatment of trading-related service fees (data subscriptions, infrastructure) across jurisdictions.
- **[planned]** `transfer-pricing-considerations-for-multi-entity-trading-operations` — Understanding transfer-pricing implications when a trading operation spans multiple related legal entities across jurisdictions.
- **[planned]** `automated-tax-lot-reporting-pipeline` — Building an automated pipeline that produces tax-lot reports on a defined schedule rather than manual reconstruction at filing time.
- **[planned]** `capital-gains-vs-business-income-classification` — Understanding the distinction (and jurisdiction-specific tests) between capital-gains and business-income classification for trading profits, which affects both rate and reporting requirements.
- **[planned]** `estimated-tax-payment-scheduling-for-active-trading-income` — Scheduling estimated tax payments appropriately given the timing of active-trading income, to avoid underpayment penalties.
- **[planned]** `record-keeping-requirements-for-tax-audit-defense` — Maintaining trade records at the level of detail required to defend a tax position under audit, distinct from the operational record-keeping in order-placement-idempotency.
- **[planned]** `currency-gain-loss-tax-treatment-for-forex-trading` — Understanding the specific tax treatment (which can differ materially from capital gains treatment) applicable to forex trading gains/losses in relevant jurisdictions.
- **[planned]** `section-1256-contract-tax-treatment-us-futures` — Understanding US Section 1256 contract tax treatment (60/40 blended rate) for eligible futures and options, and its accounting-system implications.
- **[planned]** `double-taxation-treaty-considerations-cross-border-trading` — Understanding double-taxation treaty implications for a trader/entity operating across two treaty countries.
- **[planned]** `constructive-sale-rule-considerations-us` — Understanding the US constructive-sale rule's implications for hedged appreciated positions before assuming a hedge alone avoids a taxable event.

---

**Grand total: 502 skills tracked (28 built, 474 planned) across 16 categories.**

# Glossary

Terms as used consistently across this repo's skills.

**Lookahead bias** — a backtest or feature using information that would not actually
have been available at the decision timestamp it's being applied to.

**Train/serve skew** — any difference between how a feature or signal is computed
during offline training/backtesting versus how it's computed in the live inference
path, causing live predictions to diverge from validated offline behavior.

**Walk-forward validation** — chronological, non-shuffled train/test splitting for
time-series models, as opposed to random k-fold cross-validation.

**Idempotency key** — a client-generated identifier attached to an order so retries
or reconnects can be reconciled against broker state instead of blindly resubmitted.

**Circuit breaker (this repo's sense)** — a risk-module-level, strategy-independent
hard limit (position size, daily loss, drawdown) that halts trading and force-flattens
positions on breach, structurally separate from strategy signal logic.

**Backpressure** — the condition where a data consumer falls behind its producer;
this repo treats the *response* to backpressure (drop/sample/degrade/never-drop) as
something to choose deliberately per data stream, not something to leave to a
library's default queue behavior.

**Live probing (of a token/session)** — checking whether a cached broker auth token
is still valid by making an actual low-cost API call, rather than trusting the
broker-documented expiry timestamp.

**Paper trading** — running the full live code path (signal generation, risk checks,
order-placement logic) with only the final broker order submission redirected to a
simulated fill, as opposed to a separately-implemented "simulation mode."

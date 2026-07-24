---
name: forex-broker-integration-oanda-mt5
description: >-
  Use when integrating a forex broker (OANDA's REST/streaming API, or MetaTrader 5 for brokers that only expose an MT5 terminal) where pip-based pricing, rollover/swap charges, and MT5's non-Python-native environment introduce integration patterns distinct from equities brokers
domain: algorithmic-trading
subdomain: global-market-integration
tags: ["global-market-integration", "oanda-v20-rest-api", "metatrader-5-python-integration"]
brokers_frameworks: ["OANDA v20 REST API", "MetaTrader 5 Python integration"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when building a bot against a forex-specific broker interface. Two common paths: OANDA-style brokers that expose a modern REST + streaming API directly usable from Python or Node, and brokers whose only programmatic access is through MetaTrader 5's own terminal and Expert Advisor (MQL5) environment, requiring a bridge to reach a Python-based strategy engine. Forex-specific concerns — pip/pipette pricing conventions, overnight rollover/swap charges, and 24/5 (not 24/7) market hours with a weekly close — need explicit handling that doesn't map directly from equities-broker patterns.

## Prerequisites

- For OANDA-style APIs: API token with the correct account-type scope (practice vs live are fully separate account universes, not a flag on one account)
- For MT5: the MetaTrader 5 terminal installed and logged in on the host running the bridge (MT5's Python package requires a running terminal instance, unlike a pure REST client), or a broker-provided bridge/gateway if running headless on Linux (native MT5 terminal is Windows-only; Linux deployments typically need Wine or a broker-provided REST gateway)
- Clear definition of position sizing in lots vs units (forex conventions default to lot sizes — standard/mini/micro — which must be translated to actual currency-unit exposure consistently)

## Workflow

1. For OANDA-style REST+streaming APIs, treat the practice and live environments as entirely separate deployments (different base URLs, different API tokens, different account IDs) rather than a config flag on a shared client — this mirrors the archetype-separation concern in `headless-broker-auth-patterns` but is specific to forex brokers that commonly offer a full-featured practice environment which can be mistaken for a lower-stakes live-adjacent mode.
2. For MT5-based brokers, run the bridge (Python's `MetaTrader5` package, or a broker-provided gateway) on the same host/VM as a continuously logged-in MT5 terminal instance, and monitor the terminal connection state independently from the Python process's own health — a Python process can be alive and "working" while the underlying MT5 terminal has silently lost its broker connection, producing stale or absent data with no Python-level exception.
3. Handle the weekly market close/open explicitly: forex markets close Friday evening and reopen Sunday evening (in broker-server time, which varies by broker), unlike equities' daily close — any risk/P&L reset logic or position-monitoring cadence tuned for a daily equities session will misbehave across this weekly gap unless explicitly adapted (mirrors the same category of gap-handling concern as in `websocket-reconnect-without-duplicate-subscriptions`, but on a much longer, calendar-predictable timescale).
4. Model overnight rollover/swap charges (or credits) explicitly in any P&L calculation and backtest — holding a forex position overnight incurs a swap charge/credit based on the interest-rate differential between the currency pair, and omitting this from a backtest (see `execution-realistic-simulation`) systematically misstates the profitability of any strategy that holds positions overnight, especially carry-sensitive pairs.
5. Convert consistently between pip/pipette conventions and actual price moves per instrument — pip size differs by pair (most pairs quote to 4 decimal places with the pip at the 4th, JPY pairs quote to 2 decimals with the pip at the 2nd), and a hardcoded pip-size assumption silently miscalculates position sizing or stop-distance for any pair that doesn't match the assumption baked into the code.
6. Confirm lot-size-to-units conversion explicitly (1 standard lot = 100,000 units is the common convention, but mini/micro lots and broker-specific conventions vary) before any position-sizing logic derived from percentage-risk-per-trade calculations, since an off-by-10x lot-size error is a common, expensive mistake.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Treating a forex broker's practice/demo environment as a lower-risk variant of the same live account rather than a fully separate credential/config universe, risking credential or logic bleed-through.
- Monitoring only the Python bridge process's liveness for an MT5 integration, missing a silently disconnected underlying MT5 terminal.
- Applying daily-equities-style risk-reset or monitoring cadences across a forex weekly close without adapting for the multi-day gap.
- Omitting overnight swap/rollover charges from backtests, overstating the profitability of any position-holding strategy.
- Hardcoding a pip-size assumption that doesn't hold for JPY-quoted pairs (2 decimal places) when the rest of the codebase assumes 4.
- Confusing lot-size conventions (standard/mini/micro) leading to position sizes off by a factor of 10 or 100.

## Verification

- Confirm practice and live credentials/configs are structurally separate (e.g., different environment variables, different config files) such that no code path can accidentally route a live order through practice credentials or vice versa.
- For MT5 bridges, confirm a test that disconnects the underlying terminal (or simulates its broker-connection loss) is detected by the bridge's health check independent of the Python process's own liveness.
- Confirm a backtest's reported returns change measurably when overnight swap charges are included versus excluded, for a strategy that holds positions overnight — if there's no difference, swap modeling likely isn't actually wired in.
- Confirm position-sizing output for a known risk-per-trade percentage matches a manual calculation for at least one JPY pair and one non-JPY pair, verifying pip-size handling is correct for both.

## Related Skills

- `headless-broker-auth-patterns`
- `execution-realistic-simulation`
- `multi-currency-pnl-and-fx-conversion`
- `multi-timezone-session-scheduling`

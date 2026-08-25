---
name: forex-broker-integration-oanda-mt5
description: Use when integrating a forex broker (OANDA's REST/streaming API, or MetaTrader
  5 for brokers that only expose an MT5 terminal) where pip-based pricing, rollover/swap
  charges, and MT5's non-Python-native environment introduce integration patterns
  distinct from equities brokers
domain: algorithmic-trading
subdomain: global-market-integration
tags:
- global-market-integration
- oanda-v20-rest-api
- metatrader-5-python-integration
brokers_frameworks:
- OANDA v20 REST API
- MetaTrader 5 Python integration
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when building a bot against a forex-specific broker interface. Two common paths: OANDA-style brokers that expose a modern REST + streaming API directly usable from Python or Node, and brokers whose only programmatic access is through MetaTrader 5's own terminal and Expert Advisor (MQL5) environment, requiring a bridge to reach a Python-based strategy engine. Forex-specific concerns — pip/pipette pricing conventions, overnight rollover/swap charges, and 24/5 (not 24/7) market hours with a weekly close — need explicit handling that doesn't map directly from equities-broker patterns.

## When NOT to Use

- For the MT5 order-submission path itself — `MqlTradeRequest` construction, volume/stop-level validation, and `TRADE_RETCODE` handling — use `mt5-python-bridge-for-forex-bots`. This skill covers the surrounding integration concerns (environment isolation, pip/lot conventions, financing, terminal liveness), not order serialization.
- For equities, futures or crypto venues. Pips, lots and weekly rollover financing are forex/CFD conventions; applying them elsewhere imports the wrong cost and sizing model.
- As a source of leverage or position limits. This skill records where those rules live and who sets them; the binding numbers are jurisdiction- and broker-specific and must come from the regulator and the broker's contract specifications.

## Prerequisites

- For OANDA-style APIs: API token with the correct account-type scope (practice vs live are fully separate account universes, not a flag on one account)
- For MT5: the MetaTrader 5 terminal installed and logged in on the host running the bridge (MT5's Python package requires a running terminal instance, unlike a pure REST client). The official `MetaTrader5` package on PyPI publishes `win_amd64` wheels only, so the bridge host must be Windows x86-64 — Linux/macOS deployments need Wine or a broker-provided gateway
- Access to the broker's instrument metadata (OANDA `pipLocation` / MT5 `symbol_info().digits`) and its published swap-rate schedule, including the units those swap rates are quoted in. Both are required inputs; neither has a safe default
- Clear definition of position sizing in lots vs units (forex conventions default to lot sizes — standard/mini/micro — which must be translated to actual currency-unit exposure consistently)

## Workflow

1. For OANDA-style REST+streaming APIs, treat the practice and live environments as entirely separate deployments (different base URLs, different API tokens, different account IDs) rather than a config flag on a shared client — this mirrors the archetype-separation concern in `headless-broker-auth-patterns` but is specific to forex brokers that commonly offer a full-featured practice environment which can be mistaken for a lower-stakes live-adjacent mode.
2. For MT5-based brokers, run the bridge (Python's `MetaTrader5` package, or a broker-provided gateway) on the same host/VM as a continuously logged-in MT5 terminal instance, and monitor the terminal connection state independently from the Python process's own health — a Python process can be alive and "working" while the underlying MT5 terminal has silently lost its broker connection, producing stale or absent data with no Python-level exception.
3. Handle the weekly market close/open explicitly: forex markets close Friday evening and reopen Sunday evening (in broker-server time, which varies by broker), unlike equities' daily close — any risk/P&L reset logic or position-monitoring cadence tuned for a daily equities session will misbehave across this weekly gap unless explicitly adapted (mirrors the same category of gap-handling concern as in `websocket-reconnect-without-duplicate-subscriptions`, but on a much longer, calendar-predictable timescale). Resolve that boundary through a timezone database against the broker's stated server timezone rather than storing it as a UTC constant — the common 17:00 America/New_York convention is 21:00 UTC under US daylight saving and 22:00 UTC outside it, so a hardcoded UTC value is wrong for roughly half the year (see `daylight-saving-time-transition-handling`).
4. Model overnight rollover/swap charges (or credits) explicitly in any P&L calculation and backtest — holding a forex position overnight incurs a swap charge/credit based on the interest-rate differential between the currency pair, and omitting this from a backtest (see `execution-realistic-simulation`) systematically misstates the profitability of any strategy that holds positions overnight, especially carry-sensitive pairs. Take the rates from the broker's published schedule; an assumed rate is a fabricated cost model, so an unconfigured pair should fail loudly rather than accrue a plausible default.
5. Derive the triple-swap rollover from the instrument's settlement convention, not from the calendar. The rollover whose value date jumps Friday → Monday accrues three days of financing: Wednesday for T+2 instruments, but Thursday for the T+1 pairs (USD/CAD, USD/TRY, USD/RUB, USD/PHP). Count these rather than flagging them — a position held for several weeks crosses one per week, so a boolean "includes a triple swap" undercharges every multi-week hold.
6. Take pip size from the broker's own instrument metadata — OANDA's `pipLocation` on `GET /v3/accounts/{accountID}/instruments`, or MT5's `symbol_info().digits` — rather than inferring it from the instrument's name. Name-based inference is only defensible for currency pairs (JPY-quoted pairs price the pip at the 2nd decimal, others at the 4th) and is actively wrong for metals, index and crypto CFDs, whose pip definitions vary by broker; OANDA reports `pipLocation: 0` for some CFDs, a value no name-based rule produces. If metadata is unavailable for an instrument, refuse to size it rather than guessing.
7. Convert pip value into the account's currency explicitly. One pip on `units` of exposure is worth `units × pip_size` **in the instrument's quote currency**; treating that as an account-currency figure overstates pip value for a USD account trading USD/JPY by roughly the USD/JPY rate (~150x), and that error propagates directly into every percentage-risk-per-trade position size (see `multi-currency-pnl-and-fx-conversion`). Require the quote→account rate whenever the two currencies differ.
8. Confirm lot-size-to-units conversion explicitly (1 standard lot = 100,000 units is the common convention, but mini/micro lots and broker-specific conventions vary) before any position-sizing logic derived from percentage-risk-per-trade calculations, since an off-by-10x lot-size error is a common, expensive mistake.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Treating a forex broker's practice/demo environment as a lower-risk variant of the same live account rather than a fully separate credential/config universe, risking credential or logic bleed-through.
- Monitoring only the Python bridge process's liveness for an MT5 integration, missing a silently disconnected underlying MT5 terminal. Note that `terminal_info()` returns `None` when no terminal is attached, so the obvious `mt5.terminal_info().connected` raises `AttributeError` in precisely the case the check exists to detect — and a health check that defaults to "connected" when unconfigured reports a terminal it never probed as healthy.
- Hardcoding the weekly close as a UTC constant, which is off by an hour for whichever half of the year the broker's server timezone is not observing daylight saving.
- Omitting overnight swap/rollover charges from backtests, overstating the profitability of any position-holding strategy — or substituting an assumed rate for the broker's published one, which produces a cost model that looks calibrated and isn't.
- Charging a single triple swap for a hold of any duration, or assuming Wednesday for a T+1 pair such as USD/CAD, whose triple-swap rollover falls on Thursday.
- Inferring pip size from the instrument's ticker. It fails for JPY-quoted pairs against a 4-decimal assumption (100x), and it fails unpredictably for metals, index and crypto CFDs — note that `XAUUSD` and `BTCUSD` are six alphabetic characters, so a shape-only check treats them as ordinary currency pairs.
- Treating pip value computed as `units × pip_size` as if it were denominated in the account currency, when it is denominated in the instrument's quote currency.
- Confusing lot-size conventions (standard/mini/micro) leading to position sizes off by a factor of 10 or 100.

## Verification

- Confirm practice and live credentials/configs are structurally separate (e.g., different environment variables, different config files) such that no code path can accidentally route a live order through practice credentials or vice versa.
- For MT5 bridges, confirm a test that disconnects the underlying terminal (or simulates its broker-connection loss) is detected by the bridge's health check independent of the Python process's own liveness, and that the check reports unhealthy — not healthy — when it is unconfigured or the probe itself raises.
- Confirm a backtest's reported returns change measurably when overnight swap charges are included versus excluded, for a strategy that holds positions overnight — if there's no difference, swap modeling likely isn't actually wired in. Then confirm a two-week hold is charged more triple-swap days than a one-week hold.
- Confirm position-sizing output for a known risk-per-trade percentage matches a manual calculation for at least one JPY pair and one non-JPY pair, verifying both pip-size handling and quote→account currency conversion. Worked example: 1 standard lot USD/JPY at 150.00 in a USD account is `100,000 × 0.01 = 1,000 JPY` per pip, which is `1,000 / 150 ≈ 6.67 USD` — not 1,000.
- Confirm the pip-size path refuses to produce a number for an instrument it has no broker metadata for, rather than falling back to a default.
- Run `python -m unittest discover -s skills/forex-broker-integration-oanda-mt5/scripts` and confirm all tests pass.

## Related Skills

- `mt5-python-bridge-for-forex-bots`
- `headless-broker-auth-patterns`
- `execution-realistic-simulation`
- `multi-currency-pnl-and-fx-conversion`
- `multi-timezone-session-scheduling`
- `daylight-saving-time-transition-handling`

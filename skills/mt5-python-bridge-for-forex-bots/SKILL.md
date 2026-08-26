---
name: mt5-python-bridge-for-forex-bots
description: Use when a Python bot submits market deals to a MetaTrader 5 terminal
  and needs MqlTradeRequest construction, broker-metadata-driven volume/stop/filling
  validation, and TRADE_RETCODE triage that separates a fill from an unknown outcome
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- mt5
- metatrader-5
- mql5
- order-send
- trade-retcode
- forex-bot
brokers_frameworks:
- MetaTrader 5 Python API
- MQL5 Trade Engine
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when a Python strategy sends **market deals** (`TRADE_ACTION_DEAL`) to a MetaTrader 5 terminal via the official `MetaTrader5` package, and you need the order-submission path itself to be correct: building the `MqlTradeRequest` dictionary, validating volume and stop levels against the *broker's own* symbol metadata, choosing a filling mode the symbol actually permits, and turning the returned `MqlTradeResult.retcode` into a decision a bot can act on safely.

The core difficulty is that `order_send()` has **no client-assigned order id**. There is no MT5 equivalent of a `clientOrderId`, so a lost or ambiguous response cannot be resolved by resubmitting the same request — a resend is a genuinely new order. Everything in this skill is built around that constraint.

## When NOT to Use

- **You are not on Windows x86-64.** The `MetaTrader5` package on PyPI publishes `win_amd64` wheels only, and it requires a running, logged-in terminal on the same host. Linux/macOS deployments need Wine or a broker-provided gateway — a decision that belongs in `forex-broker-integration-oanda-mt5`, not here.
- **You need pending orders or position management.** This skill covers `TRADE_ACTION_DEAL` only. `TRADE_ACTION_PENDING` (limit/stop entry), `TRADE_ACTION_SLTP` (modify stops), `TRADE_ACTION_MODIFY`, `TRADE_ACTION_REMOVE` and `TRADE_ACTION_CLOSE_BY` have different required fields and different failure modes.
- **You are choosing pip/lot conventions, swap accounting, or terminal liveness monitoring.** Those are `forex-broker-integration-oanda-mt5`.
- **You want a generic idempotency layer.** MT5 cannot provide one at the protocol level. `order-placement-idempotency` covers the general pattern; what applies here is reconciliation-before-retry, described below.
- **You are running a hedging-vs-netting-sensitive strategy without having checked the account mode.** On a netting account an opposing deal reduces or reverses the existing position rather than opening a second one. This module submits the deal; it does not model the resulting position.

## Prerequisites

- MT5 terminal installed, running and logged in on the same Windows host, with **Algorithmic Trading enabled in the terminal** — otherwise every deal returns `10027 TRADE_RETCODE_CLIENT_DISABLES_AT`.
- `MT5Config(login, password, server, path, max_slippage_points, magic_number, preferred_filling)`. `magic_number` must be positive and unique per strategy: it is the only tag available for reconciling an ambiguous submission. `password` is kept out of the dataclass `repr`.
- A terminal adapter satisfying `MT5TerminalAdapter` (`order_send`, `symbol_info`). In production this wraps the `MetaTrader5` module; this repository's module never imports it, so the logic stays testable off-Windows.
- Per-symbol trading conditions from `symbol_info()` — `volume_min`, `volume_max`, `volume_step`, `volume_limit`, `digits`, `point`, `trade_stops_level`, `filling_mode`. None of these has a safe default; the engine refuses to trade a symbol it cannot read them for.

## Workflow

1. **Read the symbol's trading conditions before validating anything.**
   - `symbol_info()` returns `None` for a symbol the terminal does not know — including a correct symbol under the wrong broker suffix (`EURUSD.pro`, `EURUSDm`). Treat `None` as a hard stop (`MT5_SYMBOL_UNAVAILABLE`) and confirm the symbol is selected in Market Watch (`symbol_select`), not as a reason to guess defaults.
   - Never hard-code `0.01` as the lot step. It is 0.001 on micro accounts and 1.0 on many index CFDs. `_validate_volume` checks `volume_min`, `volume_max`, `volume_step` and `volume_limit` as the broker publishes them.

2. **Reject an unrecognised side before serialising.**
   - `order_type` must be exactly `BUY` or `SELL` (case-insensitive). Anything else — `LONG`, `BUY_LIMIT`, a typo — is rejected as `MT5_INVALID_ORDER_TYPE`. A side dispatcher that falls through to an `else` branch turns an unrecognised string into a live order in the *opposite* direction.

3. **Validate stops on both side and distance.**
   - For a Buy, SL must be strictly below and TP strictly above the entry; for a Sell, the reverse. Both levels are checked — a mis-signed TP is as harmful as a mis-signed SL. `0.0` (or `None`) means "no level set" and is passed through untouched.
   - Distance is checked against `SYMBOL_TRADE_STOPS_LEVEL`, in points, using the prices as they will actually be serialised. **A `trade_stops_level` of 0 does not mean "any distance is allowed"** — many brokers apply a floating, spread-derived level that the static property does not express. Passing this check makes `10016` unlikely, not impossible.

4. **Derive `type_filling` from the symbol, do not assume it.**
   - `SYMBOL_FILLING_MODE` is a **bitmask** (`FOK=1`, `IOC=2`, `BOC=4`) while `ENUM_ORDER_TYPE_FILLING` is a **plain enum** (`FOK=0`, `IOC=1`). They are different numberings; feeding the mask straight into `type_filling` is the usual cause of `10030` / "Unsupported filling mode".
   - Only FOK and IOC are candidates for a market deal — BOC applies to limit/stop-limit orders and RETURN is disabled under Market Execution. If the symbol permits neither, the deal is refused locally rather than sent to be rejected.
   - Under Market Execution, MQL5 requires five fields: `action`, `symbol`, `volume`, `type`, `type_filling`.

5. **Submit exactly once, then classify the retcode — never retry inside the send path.**
   - `10009 DONE` → filled. Read the fill from `MqlTradeResult`, not from your own request: `result.volume` is "Deal volume, confirmed by broker" and `result.price` is the confirmed deal price.
   - `10010 DONE_PARTIAL` → **a position is open**. Report it as executed with the confirmed volume and both tickets (`order` and `deal`). Any follow-up must be sized from the *shortfall*; resending the original volume doubles the intended exposure.
   - `10008 PLACED` → accepted, not yet filled. Keep the ticket, claim no exposure.
   - `10004 / 10020 / 10021 / 10024` → transient and nothing filled. Safe to re-quote and resend under a bounded attempt cap.
   - `10011 / 10012 / 10028 / 10031`, an adapter exception, or `order_send()` returning `None` → **outcome unknown**. `requires_reconciliation=True`.
   - Anything else, including an unrecognised code, is terminal. An unknown server response is never a licence to resend a non-idempotent order.

6. **Reconcile before any resend of an ambiguous submission.**
   - Query `history_deals_get(...)` / `positions_get(...)` and filter on your `magic`. Deals carry `magic`, `order`, `position_id`, `volume` and `price`, which is what makes magic-number reconciliation possible at all.
   - Do not use `comment` as a substitute client order id: MT5 order comments are short and the trade server may truncate or overwrite them.

> Full procedure: see `references/workflows.md`.
> Standards reference and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a non-`10009` retcode as "nothing happened."** `10010 DONE_PARTIAL` means volume traded. Code that branches on `retcode == 10009` and resends everything else will double up on every partial fill.
- **Resending after a timeout or a `None` result.** `order_send()` returning `None`, raising, or answering `10012`/`10031` tells you the *client* lost the answer, not that the *server* rejected the order. MQL5 states plainly that "successful sending of a request does not entail that the requested trading operation will be executed successfully." Reconcile on the magic number first.
- **Calling `.retcode` / `.get()` on the result without a `None` check.** `MetaTrader5.order_send()` returns `None` when the terminal cannot process the call, so the obvious accessor raises `AttributeError` in precisely the failure case it exists to handle.
- **Hard-coding `0.01` as the minimum lot and the lot step.** Wrong for micro accounts (0.001) and for index/metal CFDs whose step can be 0.1 or 1.0. Read `volume_min` / `volume_step` from `symbol_info()`.
- **Validating the lot step with `round(v * 100) % 1 != 0`.** `round()` returns an `int`, so `int % 1` is always `0` and the check never fires — `0.015` lots sails through to a `10014` rejection at the server.
- **Passing `SYMBOL_FILLING_MODE` straight into `type_filling`.** The mask says FOK is bit `1`; the enum says FOK is `0`. The mismatch surfaces as `10030` with the broker comment "Unsupported filling mode", not as `10013`.
- **Validating only the stop loss.** A Buy whose take profit sits below the entry is just as invalid, and passes silently if only SL is checked.
- **Reading `trade_stops_level == 0` as "no minimum distance."** Brokers commonly apply a floating, spread-derived level that the static property reports as zero.
- **Comparing a stop distance against `trade_stops_level` in raw floats.** `1.08500 - 1.08480` is `0.00019999999999997797`, so a stop placed exactly at a 20-point limit measures as 19.999… points and is falsely rejected. Compare with a sub-point tolerance.
- **Sending a stale price as the market price.** `TRADE_ACTION_DEAL` expects the current quote — Ask for a Buy, Bid for a Sell. A price from a closed bar produces requotes (`10004`) or `10015`, and `deviation` only widens the tolerance, it does not fix the reference.
- **Running with `magic = 0`.** Indistinguishable from a manually placed trade, which makes post-timeout reconciliation impossible to scope to the strategy.
- **A "simulation" default that returns `TRADE_RETCODE_DONE`.** An engine that fabricates a success when no terminal is attached is indistinguishable from a live fill to everything downstream. Require an adapter, or an explicit dry run that reports `is_executed=False`.
- **Forgetting the terminal's Algorithmic Trading toggle.** Every deal comes back `10027`, with nothing wrong in the request.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/mt5-python-bridge-for-forex-bots/scripts` — all tests must pass.
- Construct `MT5PythonBridgeEngine` with neither an adapter nor `dry_run=True` and confirm it raises `MT5BridgeError` rather than producing a fabricated fill.
- Submit `0.015` lots against a `volume_step` of `0.01` and confirm `MT5_INVALID_VOLUME` with `order_send` never called; submit `0.001` against a micro-account spec (`volume_step=0.001`) and confirm it is accepted.
- Submit a Buy with TP below entry and a Sell with TP above entry; confirm both are rejected as `MT5_INVALID_STOPS` before submission.
- Submit `order_type="LONG"` and confirm `MT5_INVALID_ORDER_TYPE` with an empty `mql_trade_request` — not a serialised `ORDER_TYPE_SELL`.
- With `filling_mode = SYMBOL_FILLING_FOK` only, confirm the serialised `type_filling` is `ORDER_FILLING_FOK` (`0`); with `SYMBOL_FILLING_BOC` only, confirm `MT5_INVALID_FILLING` and no submission.
- Place a stop exactly `trade_stops_level` points away and confirm it is accepted; one point closer and confirm it is rejected.
- Return `retcode=10010` with `volume` below the request and confirm `is_executed=True`, `status="MT5_ORDER_PARTIALLY_FILLED"`, and that both `order_id` and `deal_id` survive.
- Make the adapter return `None`, then raise, then answer `10012`; confirm all three yield `MT5_EXECUTION_AMBIGUOUS` with `requires_reconciliation=True`, and that `order_send` was called exactly once in each case.
- Return `retcode=10010` with no `volume` field and confirm the result is `MT5_EXECUTION_AMBIGUOUS`, not a fill of zero lots.
- Pass a `symbol_spec` whose `symbol` differs from the order's and confirm `MT5_SYMBOL_MISMATCH` with nothing submitted.
- Confirm `repr(MT5Config(...))` does not contain the password.

## Related Skills

- `forex-broker-integration-oanda-mt5`
- `order-placement-idempotency`
- `broker-agnostic-adapter-interface`
- `headless-broker-auth-patterns`
- `systemd-supervision-for-trading-bots`

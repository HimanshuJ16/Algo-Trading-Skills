---
name: mt5-python-bridge-for-forex-bots
description: >-
  MetaTrader 5 (MT5) Python IPC bridge engine managing automated Forex order execution, lot sizing, MqlTradeRequest formatting, and TRADE_RETCODE validation.
domain: Broker & Exchange API Integrations
subdomain: MetaTrader 5 Forex Algorithmic Connectivity
tags: ["mt5", "metatrader-5", "forex-bot", "order-send", "mql5", "trade-retcode-done", "lot-sizing", "python-bridge"]
brokers_frameworks: ["MetaTrader 5 Python API", "MQL5 Trade Engine", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting Python algorithmic trading bots to MetaTrader 5 (MT5) terminals for automated Forex, Metals, and CFD trading. The official `MetaTrader5` Python package communicates via local IPC with an active MT5 terminal. Sending order requests (`order_send()`) requires strict adherence to MQL5 `MqlTradeRequest` structure, volume float formatting, SL/TP validation, and return code parsing (`TRADE_RETCODE_DONE` = `10009`). This module abstracts MT5 terminal IPC, validates lot steps and stop levels, formats dictionary requests, and handles execution retcodes.

## Prerequisites

- MT5 terminal configuration (`login`, `password`, `server`, `path`, `max_slippage_points`: e.g. 10, `magic_number`: e.g. 234000).
- Order request payload (`symbol`: e.g. `'EURUSD'`, `order_type`: `'BUY'`/`'SELL'`, `volume_lots`: float, `price`: float, `sl_price`, `tp_price`).

## Workflow

1. **Volume Lot & Stop Level Validation**:
   - Verify `volume_lots` is a positive float multiple of 0.01 (min 0.01 lots). If invalid $\implies$ Reject (`MT5_INVALID_VOLUME`).
   - Audit SL/TP levels: For Buy, `sl_price` must be $< \text{price}$ and `tp_price` $> \text{price}$. For Sell, `sl_price` $> \text{price}$ and `tp_price` $< \text{price}$.
2. **MQL5 `MqlTradeRequest` Serialization**:
   - Format dictionary payload:
     - `action`: `1` (`TRADE_ACTION_DEAL`).
     - `symbol`: e.g. `'EURUSD'`.
     - `volume`: `float(volume_lots)`.
     - `type`: `0` (`ORDER_TYPE_BUY`) or `1` (`ORDER_TYPE_SELL`).
     - `price`, `sl`, `tp`, `deviation`, `type_filling`: `1` (`ORDER_FILLING_IOC`), `magic`, `comment`.
3. **Execution & `retcode` Audit**:
   - Parse `result.retcode`: If `10009` (`TRADE_RETCODE_DONE`) $\implies$ Status `MT5_ORDER_EXECUTED_SUCCESS`.
   - If `10013` or `10019` $\implies$ Flag `MT5_ORDER_FAILED`.
4. **Audit Report Generation**: Output structured `MT5OrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sending Volume as Integer**: Passing `volume: 1` instead of `volume: 1.0` or `0.1` as float, triggering MT5 `10014` (`INVALID_VOLUME`) rejections.
- **Omitting `type_filling`**: Failing to specify `ORDER_FILLING_IOC` or `ORDER_FILLING_FOK`, causing broker-side `10013` (`INVALID_REQUEST`) errors.
- **Ignoring MT5 `retcode`**: Assuming `order_send()` returned success without evaluating `retcode == 10009`.

## Verification

- Instantiate `MT5PythonBridgeEngine`. Audit 0.1 lot EURUSD Buy order ($P = 1.0850, \text{SL} = 1.0800, \text{TP} = 1.0950$) $\implies$ verify MQL5 request dictionary serialization, retcode `10009`, and status `MT5_ORDER_EXECUTED_SUCCESS`. Audit invalid volume ($0.005$ lots $< 0.01$) $\implies$ verify `MT5_INVALID_VOLUME`.
- Run `python scripts/test_mt5_python_bridge_for_forex_bots.py`.

## Related Skills

- `interactive-brokers-global-multi-exchange-routing`
- `broker-agnostic-adapter-interface`
---

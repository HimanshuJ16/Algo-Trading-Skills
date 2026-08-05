---
name: singapore-exchange-sgx-api-integration
description: >-
  Production-grade client for Singapore Exchange (SGX) TITAN derivatives and equities API connectivity, supporting FIX protocol order routing across FTSE China A50, Nikkei 225, MSCI Taiwan, and Iron Ore futures with tick size validation and session management.
domain: Broker Integration & Exchange Connectivity
subdomain: SGX TITAN Derivatives & Equities API
tags: ["sgx", "singapore-exchange", "titan-gateway", "fix-protocol", "nikkei-225-futures", "china-a50-futures"]
brokers_frameworks: ["SGX TITAN FIX Protocol", "Nasdaq Genium OMnet", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting algorithmic trading algorithms to Singapore Exchange (SGX) TITAN derivatives trading engine or SGX Equities platform. SGX is Asia's primary venue for offshore China A50 futures (`CN`), Nikkei 225 futures (`NK`), MSCI Taiwan futures (`TW`), and Iron Ore futures (`FE`). Order placement requires strict compliance with contract-specific tick sizes (e.g. 2.5 index points for FTSE China A50, 5.0 index points for Nikkei 225) and FIX session management.

## Prerequisites

- SGX TITAN FIX SenderCompID (`sender_comp_id`) and TargetCompID (`target_comp_id`).
- Target environment (`SIMULATION` or `PRODUCTION`).

## Workflow

1. **FIX Session Establishment**:
   - Logon to SGX TITAN FIX gateway (`connect()`); verify `SenderCompID` and heartbeat state.
2. **Contract Tick Size & Spec Resolution**:
   - Resolve contract specification (e.g. FTSE China A50 `CN` tick size 2.5; Nikkei 225 `NK` tick size 5.0).
3. **Pre-Trade Tick Size Alignment**:
   - Audit limit order price: verify `price % tick_size == 0`. Throw `ValueError` on tick mismatch.
4. **Order Routing & Execution Management**:
   - Submit order via FIX gateway and log execution status.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unaligned Order Prices**: Submitting limit prices that do not align with SGX contract tick sizes (e.g. price $38,002.00 for Nikkei 225 futures requiring 5.0 index point increments).
- **Miscalculating Contract Multipliers**: Misinterpreting index point multipliers (e.g., Nikkei 225 $500\text{ JPY} / \text{point}$ vs FTSE China A50 $\$1.00\text{ USD} / \text{point}$).
- **Unmonitored FIX Sequence Gaps**: Failing to process FIX sequence reset or resend requests during high-volatility news events.

## Verification

- Instantiate `SingaporeExchangeSGXAPIClient`. Connect session $\implies$ verify `is_connected=True`. Submit limit buy order for FTSE China A50 (`CN`) at 12,500.0 (multiple of 2.5) $\implies$ verify `status = "NEW"`. Submit limit order for Nikkei 225 (`NK`) at price 38,002.0 (invalid tick) $\implies$ verify `ValueError` raised. Disconnect session and attempt order $\implies$ verify `RuntimeError` raised.
- Run `python scripts/test_singapore_exchange_sgx_api_integration.py`.

## Related Skills

- `hong-kong-exchange-hkex-connect-integration`
- `binary-protocol-parsing-for-low-latency-feeds`
---

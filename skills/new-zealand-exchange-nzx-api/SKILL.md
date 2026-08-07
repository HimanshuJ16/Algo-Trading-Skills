---
name: new-zealand-exchange-nzx-api
description: >-
  New Zealand Exchange (NZX) FIX 4.4 trading engine enforcing NZX tick size schedules, FIX order lifecycle management (NewOrderSingle 'D', ExecutionReport '8', OrderCancelRequest 'F'), and order validation.
domain: Global Exchange Integrations
subdomain: Australasia Markets & FIX Protocol Connectivity
tags: ["nzx", "new-zealand-exchange", "fix-protocol", "tick-size-schedule", "nzd", "order-routing", "australasia"]
brokers_frameworks: ["NZX FIX 4.4 Gateway Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing equities, debt, or index derivatives to the New Zealand Exchange (NZX) via direct FIX 4.4 connectivity. Trading on the NZX requires enforcing strict exchange-specific tick size schedules ($NZD < \$0.20 \implies 0.001$, $\$0.20 - \$1.995 \implies 0.005$, $\ge \$2.00 \implies 0.01$), mapping symbols (e.g. `FPH.NZ`, `AIA.NZ`), handling NZD currency settlement, and managing FIX session messages (`TargetCompID = 'NZX_TRADING'`).

## Prerequisites

- FIX 4.4 Session credentials (`SenderCompID`, `TargetCompID = 'NZX_TRADING'`).
- NZX Tick Size schedule compliance module.

## Workflow

1. **Tick Size Compliance Audit**:
   - Validate order limit price against NZX price step schedule:
     - Price $< \$0.20 \implies$ Multiple of $0.001$ NZD.
     - Price $\$0.20 - \$1.995 \implies$ Multiple of $0.005$ NZD.
     - Price $\ge \$2.00 \implies$ Multiple of $0.01$ NZD.
2. **FIX NewOrderSingle ('D') Construction**:
   - Build FIX tags: `35=D`, `11=ClOrdID`, `55=Symbol` (e.g., `FPH`), `54=Side` (`1`=Buy, `2`=Sell), `38=OrderQty`, `44=Price`, `40=OrdType` (`1`=Market, `2`=Limit), `59=TimeInForce` (`0`=Day, `3`=IOC, `4`=FOK), `15=NZD`.
3. **ExecutionReport ('8') & Cancel ('F') Processing**:
   - Parse incoming execution status (`39=OrdStatus`, `17=ExecID`, `32=LastShares`, `31=LastPx`).
4. **Audit Report Generation**: Output structured `NZXOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sub-Tick Price Rejection**: Submitting an order for $NZD \$3.505$ (0.005 increment) when prices $\ge \$2.00$ require $0.01$ increments, causing instant exchange rejection.
- **Incorrect Timezone Awareness**: Submitting orders outside Pacific/Auckland trading hours (09:00 - 17:00 NZDT/NZST).
- **Omitting Currency Tag**: Forgetting FIX Tag `15=NZD` when submitting multi-currency order routing requests.

## Verification

- Instantiate `NewZealandExchangeNZXEngine`. Validate $NZD \$1.50$ with $0.005$ step $\implies$ PASS. Validate $NZD \$3.505$ with $0.005$ step $\implies$ REJECT (requires $0.01$ step). Build FIX NewOrderSingle for 1,000 shares of `FPH` @ $\$30.00 \implies$ verify FIX string tags `35=D`, `55=FPH`, `15=NZD`.
- Run `python scripts/test_new_zealand_exchange_nzx_api.py`.

## Related Skills

- `australian-securities-exchange-asx-api`
- `binary-protocol-parsing-for-low-latency-feeds`
---

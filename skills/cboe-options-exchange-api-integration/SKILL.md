---
name: cboe-options-exchange-api-integration
description: Quantitative integration for Cboe Options Exchange API, specializing
  in Complex Order Book (COB) multi-leg routing and Complex Order Auction (COA) participation.
domain: Market Connectivity
subdomain: Exchange API
tags:
- cboe
- options
- complex-order-book
- multi-leg
- fix-protocol
brokers_frameworks:
- Generic FIX Engine
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating directly with Cboe (Chicago Board Options Exchange) using FIX or BOEv3 to trade options. Specifically, use this to handle **New Order Multileg** (MsgType `AB`) orders to target the Complex Order Book (COB) or initiate a Complex Order Auction (COA) for price improvement, ensuring legging risk is eliminated at the exchange matching engine level.

## Prerequisites

- Direct market access to Cboe via FIX or BOE.
- A FIX parsing engine capable of handling repeating groups (for `NoLegs`).

## Workflow

1. **Order Construction**: Instantiate `CboeMultilegOrder`. Add up to 16 individual legs (options or stock legs).
2. **Ratio Normalization**: Ensure the ratio of the legs is reduced to its simplest form (e.g., 2:4 becomes 1:2), per Cboe rules.
3. **COA vs COB Configuration**:
   - If targeting COA (Complex Order Auction) for price improvement, tag the order appropriately (often using `ExecInst`).
   - Define the Net Price for the entire package.
4. **Serialization**: Convert the Python dataclass into a FIX `AB` message string with the repeating `NoLegs` group.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Leg Ratio Simplification**: Sending a 10:20 spread instead of a 1:2 spread with an order quantity of 10. Cboe matching engines may reject unsimplified ratios or execute them incorrectly.
- **Stock-Option Net Pricing**: Miscalculating the net price when one leg is a stock (equity) and the other is an option (whose price represents 100 shares). Cboe expects precise net pricing conventions for stock-option combinations.
- **Single-Leg Routing**: Sending multiple single-leg orders to simulate a spread. This incurs legging risk and misses the capital efficiency of the COB.

## Verification

- Simulate the creation of a Calendar Spread. Verify that the FIX message correctly populates `NoLegs=2`, normalizes the ratios, and outputs valid repeating groups.
- Run `python scripts/test_cboe_complex_order_engine.py`.

## Related Skills

- `calendar-spread-and-multi-leg-order-atomicity`
- `fix-protocol-session-management-across-venues`

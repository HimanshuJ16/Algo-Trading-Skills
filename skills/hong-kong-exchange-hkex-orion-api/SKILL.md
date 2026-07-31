---
name: hong-kong-exchange-hkex-orion-api
description: >-
  Quantitative market gateway engine for Hong Kong Exchange (HKEX Orion OMD-C/OMD-D API), enforcing 5-digit stock codes, HKEX Spread Table tick sizes, and Board Lot sizing.
domain: Global Market Integration & FX
subdomain: Asian Market Connectivity & HKEX Orion
tags: ["hkex", "orion-api", "omd-c", "omd-d", "hong-kong-exchange", "board-lot", "spread-table", "dual-counter"]
brokers_frameworks: ["HKEX Orion OMD-C / OMD-D", "Stock Connect (Northbound/Southbound)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in Asian market trading algorithms, Stock Connect routing systems, and HKEX execution gateways. Trading on the Hong Kong Exchange requires strict adherence to HKEX market conventions: 5-digit stock codes (e.g. `00700` for Tencent), HKD/RMB Dual Counter models (`00700` vs `80700`), dynamic tick sizes based on the **HKEX Spread Table** (Second Schedule Rules of the Exchange), and mandatory **Board Lot** quantity multiples.

## Prerequisites

- HKEX security details (`raw_code`, `currency`: `HKD`/`RMB`, `board_lot_size`, `price`, `quantity`).
- Official HKEX Spread Table minimum price increment schedule.

## Workflow

1. **5-Digit Stock Code & Dual Counter Normalization**:
   - Zero-pad stock code to 5 digits (e.g., `700` $\rightarrow$ `"00700"`).
2. **HKEX Spread Table Tick Size Validation**:
   - Compute dynamic tick size $\Delta P$ based on price tier:
     - $\$0.50 \le P < \$10.00 \implies \Delta P = \$0.010$.
     - $\$10.00 \le P < \$20.00 \implies \Delta P = \$0.020$.
     - $\$20.00 \le P < \$100.00 \implies \Delta P = \$0.050$.
     - $\$100.00 \le P < \$200.00 \implies \Delta P = \$0.100$.
     - $\$200.00 \le P < \$500.00 \implies \Delta P = \$0.200$.
     - $P \ge \$500.00 \implies \Delta P = \$0.500$.
   - Verify order price is an exact integer multiple of $\Delta P$.
3. **Board Lot Quantity Audit**:
   - Verify order quantity is a multiple of `board_lot_size`. Non-multiples are flagged `ODD_LOT_BOOK`.
4. **Audit Report Generation**: Output structured `HkexOrionOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Unpadded Stock Codes**: Sending `"700"` instead of `"00700"`, causing order gateway rejection.
- **Ignoring Price-Dependent Spread Tables**: Using a fixed $0.01$ tick size across all HKEX stocks regardless of price tier, submitting invalid price ticks.
- **Submitting Odd Lots to Standard Book**: Routing non-board-lot order quantities (e.g. 50 shares of Tencent) to the primary continuous order book.

## Verification

- Instantiate `HkexOrionApiEngine`. Submit order for Tencent HKD (`raw_code="700"`, Price $=\$300.20$, Qty $=200$, Board Lot $= 100$). Verify engine formats code `"00700"`, validates tick size $\Delta P = \$0.20$ ($\$300.20 \pmod{\$0.20} == 0$), verifies Board Lot multiplier ($200 = 2 \times 100$), and outputs `ORDER_VALIDATED`.
- Run `python scripts/test_hong_kong_exchange_hkex_orion_api.py`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
---

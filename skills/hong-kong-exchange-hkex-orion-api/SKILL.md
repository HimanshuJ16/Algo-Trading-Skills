---
name: hong-kong-exchange-hkex-orion-api
description: >-
  Use when a Hong Kong equity, ETF, REIT, warrant or CBBC order must satisfy the SEHK
  rulebook before reaching the Orion Central Gateway, including the correct Part of the
  Second Schedule spread table for that security.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: hkex, ocg-c, omd-c, hong-kong-exchange, second-schedule, spread-table, board-lot, odd-lot, dual-counter
  brokers_frameworks: "HKEX Orion Central Gateway - Securities Market (OCG-C); HKEX Orion Market Data Platform - Securities Market (OMD-C); SEHK Rules of the Exchange (Second Schedule); Python Decimal"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a Hong Kong equity, ETF, REIT, warrant or CBBC order is constructed
programmatically and has to be legal under the SEHK rulebook before it reaches the
exchange. HKEX rejects an order whose price is not an exact multiple of that security's
minimum spread, and does not auto-match a quantity that is not an integral multiple of
that security's board lot.

Two facts drive everything here, and both are routinely got wrong:

- **The Second Schedule is not one spread table.** It has five Parts. **Part A** covers
  all securities except those in the other Parts; **Part B** is debt securities and
  Exchange-authorised securities (a flat `0.050`); **Part C** defers Exchange Traded
  Options to the Operational Trading Procedures; **Part D** is Exchange Traded Funds;
  **Part E** is Structured Products — Derivative Warrants, CBBCs, Inline Warrants.
  A HK$5.00 equity ticks at `0.005` under Part A; a HK$5.00 CBBC ticks at `0.010` under
  Part E. Applying Part A to both produces an off-tick price on one of them.
- **Band boundaries are upper-inclusive.** The Schedule reads "From 0.01 to 0.25",
  "Over 0.25 to 10.00", "Over 200.00 to 500.00". A price of exactly `500.00` is a
  `0.200` price, not a `0.500` one. An upper-exclusive `price < bound` comparison is
  wrong at every one of the eleven Part A band edges.

Part A also moved recently. The **Reduction of Minimum Spreads** cut it in two phases:
**Phase 1 (launched 2025-08-04)** took `10.00`–`20.00` from `0.020` to `0.010` and split
the old `20.00`–`100.00` band into `20.00`–`50.00` at `0.020` and `50.00`–`100.00` at
`0.050`; **Phase 2 (launched 2026-08-03)** merged `0.50`–`10.00` into the `0.25`–`10.00`
band at `0.005`. Structured Products were carved out of spread table code 01 into the
new code 06 at Phase 1 and keep the pre-reduction bands. A tick table copied from
pre-2025 documentation is now wrong across four price bands.

## When NOT to Use

- **As an OCG-C gateway.** `scripts/hong_kong_exchange_hkex_orion_api.py` is an
  in-memory rulebook validator. It opens no sockets, speaks neither the OCG-C Binary
  Trading Protocol nor the OCG-C FIX Trading Protocol, holds no session, assigns no
  ClOrdID and tracks no order state. Use a real gateway client for transport and this
  module for the pre-send checks.
- **For market data.** OMD-C and OMD-D are HKEX's market *data* platforms and carry no
  orders; an OCG session carries orders and no securities market data. This module
  reads OMD-C only in the sense that the **Spread Table Code** it needs is published
  there, in the Security Definition (11) message.
- **For derivatives.** HKEX futures and options trade on HKATS/OAPI, not OCG-C, and
  Part C of the Second Schedule points to the Operational Trading Procedures for their
  spreads. `SpreadTable.PART_C` deliberately raises rather than inventing a scale.
- **For Northbound Stock Connect.** SSE/SZSE A-shares have their own board lot, price
  limit and quota rules — see `shanghai-shenzhen-connect-programs`.
- **For price *reasonableness* checks.** The 24-spreads opening quotation rule and the
  9-times-nominal-price rule are not implemented, because both need the security's
  nominal or previous closing price, which this module does not take. Enforce them
  separately; a tick-legal price can still be rejected by either.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations`); standard library only.
- Per-security reference data from your security master or OMD-C Security Definition
  (11): the **Spread Table Code**, the issuer-set **board lot size**, and the 5-digit
  stock code. None of the three is derivable from the price or the ticker.
- Prices as `str` or `Decimal`. A `float` is accepted and read through its shortest
  round-tripping repr, but exchange prices should not round-trip through binary floats
  at all.

## Workflow

1. **Resolve the security's spread table from reference data — never from its price.**
   Call `SpreadTable.from_omd_c_code(code)` with the Spread Table Code from the OMD-C
   Security Definition (11) message. Code `01` maps to Part A and code `06` to Part E.
   Codes `03`, `04` and `05` are in use for debt securities, Exchange Traded Options and
   Exchange Traded Products, but HKEX's published FAQ does not say which numeric code is
   which — so the mapper raises `SpreadTableUnavailableError` rather than guessing, and
   you resolve them from the OMD-C interface specification your feed actually runs and
   pass the `SpreadTable` explicitly.
2. **Normalise the stock code, and reject what cannot be one.** `format_hkex_stock_code`
   zero-pads to five digits (`"700"` → `"00700"`) but also refuses a code longer than
   five digits, a non-numeric code and `"00000"` — `zfill(5)` alone passes `"123456"`
   through untouched. `classify_counter` then labels the code `HKD_COUNTER` (`0XXXX`) or
   `RMB_COUNTER` (`8XXXX`) under the Dual Counter Model. Treat that as a label, not as
   proof the security is dual-counter: check HKEX's Dual Counter Securities list.
3. **Look the minimum spread up with upper-inclusive bands.** `get_hkex_spread_table_tick_size(price, table)`
   returns the tick for the band whose *upper bound the price does not exceed*. Outside
   the table's published range it raises `PriceOutOfRangeError` — Part A stops at
   `9,995.00` and there is no minimum spread above it to fall back on, so extrapolating
   the top band would manufacture a rule that does not exist.
4. **Test tick alignment with exact decimal arithmetic.** The check is
   `Decimal(price) % tick == 0`, with no tolerance. A tolerance on a tick count is
   expressed in tick units, so it silently loosens as the tick coarsens — and any
   tolerance wide enough to absorb `8.615 / 0.005 = 1722.9999999999998` in binary float
   is also wide enough to accept prices the matching engine will reject.
5. **Classify the quantity against the board lot, and distinguish odd from special.**
   Below one board lot is an **odd lot**; above one board lot but not an integral
   multiple is a **special lot**. Neither auto-matches — both belong to the
   semi-automatic odd/special lot facility — so both are rejected here rather than
   routed to the continuous book. The board lot is issuer-set and ranges from 10 to
   100,000 shares; there is no market-wide 100, and a `board_lot_size` of `0` or a
   negative value raises rather than dividing by zero or validating every quantity.
6. **Check the automatch order size cap.** A single order is capped at **3,000 board
   lots** in all trading sessions. A large parent order must be sliced before it is
   sent, not after it is rejected.
7. **Read `violations`, not just `status`.** An order can breach the spread table *and*
   the board lot rule at once. `status` carries the highest-precedence breach for
   routing logic; `violations` carries all of them, so a fix-and-resubmit loop does not
   burn one round trip per rule.

> Full validation sequence: see `references/workflows.md`.
> Second Schedule tables, spread table codes and rule citations: see `references/standards.md`.
> Pre-production readiness checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using one spread table for the whole HKEX universe.** A HK$5.00 equity ticks at
  `0.005` (Part A) and a HK$5.00 CBBC at `0.010` (Part E). Price a warrant off the
  equity table and every second increment you generate is off-tick.
- **Treating band boundaries as upper-exclusive.** `price < 500.00 → 0.200 else 0.500`
  puts a HK$500.00 order in the wrong band. The Schedule says "Over 200.00 to 500.00",
  so 500.00 belongs to the *lower* band. The same off-by-one exists at all eleven edges.
- **Shipping a pre-2025 Part A table.** After Phases 1 and 2, `0.25`–`10.00` is `0.005`,
  `10.00`–`20.00` is `0.010` and `20.00`–`50.00` is `0.020`. A stale table rejects legal
  prices — a HK$15.01 limit looks off-tick against a `0.020` increment that no longer
  applies — and quietly costs you the fills those prices would have won.
- **Stopping the table at HK$500.** The Schedule runs to `9,995.00`: `1.000` above
  `1,000.00`, `2.000` above `2,000.00`, `5.000` above `5,000.00`. A table that returns
  `0.500` for everything above `500.00` *accepts* a HK$1,500.50 price the exchange will
  reject — the dangerous direction, because it fails at the venue and not in your tests.
- **Extrapolating past the top band.** Above `9,995.00` there is no published minimum
  spread. Returning the top band's tick invents a rule; raise instead.
- **Validating ticks in binary floating point.** `300.20 / 0.20` is `1501.0000000000002`
  and `8.615 / 0.005` is `1722.9999999999998`. Every fix for this is a tolerance, and
  every tolerance is a decision about which illegal prices to let through. Use `Decimal`.
- **Defaulting `board_lot_size` to 100.** HKEX board lots are set by the issuer and run
  from 10 to 100,000 shares. A default is a wrong answer for most of the market; read it
  from the security master.
- **Passing an unvalidated board lot size.** `quantity % 0` raises `ZeroDivisionError`
  mid-validation, and `200 % -100 == 0` in Python — so a negative lot size makes every
  quantity look like a clean multiple and validates the order.
- **Calling every non-multiple an "odd lot".** Below one board lot is an odd lot; above
  one board lot and not a multiple is a *special lot*. They are different HKEX trade
  types with different operation codes, and conflating them mislabels your audit trail.
- **Assuming an odd lot just gets a worse price on the same book.** It is not accepted
  for auto-matching at all; it goes to the semi-automatic odd/special lot facility.
  Code that treats a rejected odd lot as a transient failure and retries it will keep
  failing.
- **Forgetting the 3,000 board lot cap.** It applies in all trading sessions. An
  un-sliced institutional parent order is rejected on size before any price check.
- **Calling OMD-C or OMD-D an order API.** They are market data platforms; an OCG
  session carries orders and no securities market data. Naming the wrong system in an
  integration design sends the connectivity, entitlement and certification work to the
  wrong HKEX team.
- **Assuming the RMB counter has its own spread table.** The Second Schedule is stated
  as "applicable to all types of currencies" — the `8XXXX` RMB counter of a Part A
  security uses the same bands as its `0XXXX` HKD counter, in RMB units.
- **Treating tick and lot compliance as sufficient.** A legal price can still breach the
  24-spreads opening quotation rule or the 9-times-nominal-price rule. Neither is
  checked here.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/hong-kong-exchange-hkex-orion-api/scripts`
- Assert the boundary invariant directly, because it is the defect most tick tables
  carry: `get_hkex_spread_table_tick_size("500.00")` must be `Decimal("0.200")` and
  `get_hkex_spread_table_tick_size("500.20")` must be `Decimal("0.500")`.
- Assert the top of the table: `"9995.00"` returns `Decimal("5.000")`, `"1500.00"`
  returns `Decimal("1.000")`, and `"10000.00"` raises `PriceOutOfRangeError`.
- Assert Part A and Part E disagree where they should: `"5.00"` is `0.005` under Part A
  and `0.010` under Part E.
- Assert `SpreadTable.from_omd_c_code("03")` raises rather than returning a table.
- Validate a Tencent (`00700`) HKD order: `raw_stock_code="700"`, `price="615.50"`,
  `quantity=10000`, `board_lot_size=100`. Expect code `"00700"`, counter
  `HKD_COUNTER`, tick `Decimal("0.500")`, 100 board lots, status `ORDER_VALIDATED`.
- Assert an order breaching two rules reports both: `price="300.15"`, `quantity=50`
  must return `violations == ("INVALID_TICK_SIZE", "INVALID_BOARD_LOT")`.
- Reconcile your spread table against the current Second Schedule PDF on hkex.com.hk
  before each release, and against HKEX circulars whenever a spread reduction phase is
  announced. This module's tables were verified on 2026-08-25.
- Complete HKEX's OCG-C certification for the interface you use. Passing these unit
  tests is not certification and grants no production access.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `shanghai-shenzhen-connect-programs`
- `hong-kong-sfc-algorithmic-trading-guidelines`
- `reference-data-symbol-mapping-across-vendors`
- `singapore-exchange-sgx-api-integration`

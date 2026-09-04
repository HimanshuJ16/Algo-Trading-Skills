---
name: singapore-exchange-sgx-api-integration
description: >-
  Use when an SGX order must be legal for the contract it names before dispatch: the
  per-contract minimum price fluctuation on Titan-DT derivatives and the price-tiered
  minimum bid size on the securities market.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: sgx, singapore-exchange, titan-dt, reach-st, tick-size, minimum-bid-size, china-a50-futures, nikkei-225-futures, iron-ore-futures
  brokers_frameworks: "SGX Titan-DT (Nasdaq Genium INET, OUCH and FIX order entry, ITCH/GLIMPSE market data); SGX Reach-ST securities trading engine (Iris-ST from H2 2027); SGX-ST Regulatory Notice 8.5.2 (Minimum Bid Size); SGX derivatives contract specifications (CN, NK, TWN, FEF); Python Decimal"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an SGX order is constructed programmatically and its price has to be
legal for the specific contract before it reaches the exchange. SGX is Asia's offshore
venue for FTSE China A50 (`CN`), Nikkei 225 (`NK`), FTSE Taiwan (`TWN`) and Iron Ore
(`FEF`) futures, and the Singapore cash equity market sits behind the same brand and a
completely different engine.

Three facts drive everything here, and all three are routinely got wrong:

- **SGX runs two trading engines.** Derivatives trade on **Titan-DT** (Nasdaq Genium
  INET; OUCH and FIX order entry, ITCH/GLIMPSE market data). Securities trade on
  **Reach-ST**, which SGX RegCo is replacing with **Iris-ST** in H2 2027. An equity
  order does not go to Titan, the two markets do not share a tick regime, and a design
  document that says "the SGX Titan API" for equities sends the connectivity,
  entitlement and certification work to the wrong SGX team.
- **The tick tables move, and stale copies are everywhere.** SGX cut the FTSE China A50
  minimum price fluctuation from **2.5 index points to 1 index point on 5 October 2020**,
  and SGX's own 2018-vintage contract-specification PDFs still hosted on api2.sgx.com
  show the superseded 2.5. On 22 June 2026 the Mini Nikkei became the Micro Nikkei
  **under the same product code `NS`**: JPY 100 x index at a 1-point tick became JPY 10
  x index at a 2.5-point tick. A hard-coded table is a wrong answer waiting for a date.
- **One contract has several minimum price fluctuations.** SGX publishes separate
  increments for outright, strategy/calendar-spread, Negotiated Large Trade and
  Trade-At-Index-Close prices. Nikkei 225 is 5 index points outright, 1 point on a
  calendar spread and 0.25 on a T@IC trade. Validate a spread differential against the
  outright increment and you reject legal prices.

The securities side has its own trap: the SGX-ST minimum bid size is **price-tiered**,
not a flat cent. An ordinary share below S$0.20 bids in S$0.001, from S$0.20 in S$0.005,
and only from S$1.00 in S$0.01 — and a structured warrant keeps the half-cent bid all
the way to S$1.995.

## When NOT to Use

- **As an SGX gateway or session manager.**
  `scripts/singapore_exchange_sgx_api_integration.py` is an in-memory specification
  validator. It opens no sockets, speaks neither Titan OUCH nor Titan FIX order entry,
  holds no session, sends no logon, assigns no ClOrdID and tracks no order state. Use a
  real Titan client for transport and this module for the pre-send price checks. The
  previous version of this skill exposed a `SingaporeExchangeSGXAPIClient` whose
  `connect()` set a flag and returned `True` with no venue behind it; it was removed
  rather than kept as a convincing-looking stub.
- **As a security master.** The shipped table holds four Titan-DT contracts whose
  figures were reconciled on 2026-08-28. SGX lists far more, and the figures change —
  pass your own reference data through `contracts=` in production.
- **For order-state, retry or idempotency questions.** A timeout on an order submission
  is an ambiguous state, not a validation failure; see `order-placement-idempotency`.
- **For quantity sizing.** Board lot and minimum quantity are per-security reference
  data, and SGX-ST board lots become price-tiered on **5 October 2026** (100 units,
  falling to 10 above S$10 and to 1 above S$100 for the instruments SGX specifies). Size
  with `minimum-fill-size-and-lot-rounding-logic`.
- **For pre-trade risk and market-conduct gates.** The SGX-ST Forced Order Range
  (±30 bids for most SGD securities), the circuit breaker band, Clearing Member
  pre-execution limits and SFA licensing live in `mas-singapore-algo-trading-guidelines`.
  A tick-legal price can still be rejected by any of them.
- **For daily price limits and margin.** SGX derivatives price limits (A50: ±10% and
  ±15% with a cooling-off period) and SGX-DC margin rates are not modelled here.
  `SGXContractSpec.notional()` returns contract value, not a margin requirement.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations`); standard library only.
- Per-contract reference data from your security master or the current SGX contract
  specification: product code, contract multiplier, currency, and the minimum price
  fluctuation **for the trade type you are pricing**.
- Prices as `str` or `Decimal`. A `float` is accepted and read through its shortest
  round-tripping repr, but exchange prices should not round-trip through binary floats.
- For production access: SGX membership or a member's sponsored access, plus SGX
  conformance testing for the Titan interface you use. Passing these unit tests is not
  conformance and grants no access.

## Workflow

1. **Decide which market the order belongs to first.** A derivatives order is validated
   against a Titan-DT contract specification (`validate_derivatives_order`); a Singapore
   cash equity order is validated against the SGX-ST minimum bid size scale
   (`validate_securities_order`). `SGXMarket` is on every result so a downstream router
   cannot send a Reach-ST order down a Titan session.
2. **Resolve the contract by product code, and let an unknown code fail loudly.**
   `validate_derivatives_order` raises `UnknownContractError` for a code that is not in
   the table — including `TW`, the retired MSCI Taiwan contract that SGX replaced with
   `TWN` (US$40 per index point, 0.25 index point outright tick) on 20 July 2020. Skipping
   tick validation for unrecognised symbols would skip it in precisely the case where
   validation matters most.
3. **Select the increment by trade type, not by contract.** Pass
   `trade_type=SGXTradeType.CALENDAR_SPREAD` for a spread differential,
   `TRADE_AT_INDEX_CLOSE` for a T@IC price (entered under the `NKTI` / `TWNTI` ticker),
   `NEGOTIATED_LARGE_TRADE` for an NLT report. Where an increment is not published for
   that contract in a verifiable source, `TickSizeUnavailableError` is raised rather
   than falling back to the outright tick — resolve it from your Titan-DT reference data.
4. **Test tick alignment with exact decimal arithmetic.** The check is
   `Decimal(price) % tick == 0`, with no tolerance. `100.03 % 0.05` is
   `0.0299999999999956` in binary float and `1.005 % 0.005` is `0.004999999999999873`;
   every fix for that is a tolerance, and every tolerance decides which illegal prices
   to let through. Rounding the remainder to four decimals, for instance, accepts
   `12500.00004` as an exact multiple of `2.5`.
5. **On the securities side, derive the bid size from the order's price, not the
   symbol.** The same share bids in S$0.005 at S$0.95 and S$0.01 at S$1.00, so a bid
   size cached per symbol is wrong the moment the stock crosses a band edge. A
   stop-limit's trigger is checked in *its own* band, which need not be the limit
   price's band. Pass `security_class` — structured warrants and debt run on different
   scales, and ETFs/ETNs raise `TickSizeUnavailableError` because SGX-ST sets their bid
   size (S$0.01 or S$0.001) per instrument.
6. **Keep foreign-currency counters out of the SGD scale.** SGX RegCo removed the
   requirement to align HKD, RMB and JPY minimum bid sizes with their home markets from
   15 July 2026, so the SGD table cannot be assumed to carry over.
   `validate_securities_order` refuses a non-SGD `currency` rather than applying it.
7. **Read `violations`, not just `status`.** An order can be off tick *and* carry a
   fractional quantity. `status` is the highest-precedence breach for routing;
   `violations` lists all of them, so a fix-and-resubmit loop does not burn one round
   trip per rule.
8. **Re-verify the table before each release.** Every `SGXContractSpec` carries `source`
   and `verified_on`. Reconcile against the current SGX contract specification and SGX
   circulars — not against an archived PDF, which is how the 2.5-point A50 tick survives.

> Full validation sequence: see `references/workflows.md`.
> Contract specifications, bid size table and citations: see `references/standards.md`.
> Pre-production readiness checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Shipping the 2.5-point A50 tick.** It has been 1 index point since 5 October 2020.
  A stale table rejects every legal odd-numbered price — half the tradeable grid — and
  the source it came from is still on SGX's own file server.
- **Treating a product code as a stable specification.** `NS` kept its code when it went
  from JPY 100 x index / 1-point ticks to JPY 10 x index / 2.5-point ticks on 22 June
  2026. Code-keyed caches with no `as_of` date silently priced the wrong contract size.
- **Sending an equity order to "the SGX Titan API".** Equities trade on Reach-ST
  (Iris-ST from H2 2027). Titan-DT is the derivatives engine. They are separate systems
  with separate connectivity and separate tick regimes.
- **Pricing MSCI Taiwan futures on SGX.** That contract left; SGX lists FTSE Taiwan
  (`TWN`) at US$40 per index point with a 0.25 point tick, not US$100 and 0.1. A
  validator that still knows `TW` will happily approve an order for an instrument the
  venue cannot match.
- **Using the outright increment for a calendar spread.** Nikkei 225 spreads tick at 1
  index point against 5 outright, and T@IC at 0.25. Over-strict validation looks safe
  and quietly suppresses legal spread quotes.
- **Guessing an increment that is not published.** Where SGX does not publish a spread
  or T@IC increment for a contract, interpolating one manufactures a rule. Raise, then
  resolve it from reference data.
- **Validating ticks in binary floating point.** `12500.00004` passes a rounded-remainder
  check against a 2.5-point tick, and the exchange rejects it. Use `Decimal`.
- **Skipping validation for unrecognised symbols.** A missing table entry is the case
  most likely to be a typo, a vendor symbol or a delisted contract. `if spec is not None`
  around a tick check turns the highest-risk order into the only unvalidated one.
- **Applying a flat S$0.01 tick to Singapore equities.** S$0.615 is a legal price for an
  ordinary share and off tick for a structured warrant above S$2.00; S$1.005 is legal
  for neither. The scale is tiered by price *and* by security class.
- **Caching an equity's bid size per symbol.** It changes as the price crosses S$0.20 or
  S$1.00 — mid-session, with no reference-data event to invalidate the cache.
- **Assuming a tick-legal price will be accepted.** SGX-ST also enforces a Forced Order
  Range (±30 bids for most SGD securities) and a circuit breaker band, SGX derivatives
  enforce daily price limits, and Clearing Members apply pre-execution value limits.
  None of them is a tick rule.
- **Treating a fake session as connectivity.** A `connect()` that sets a boolean and
  returns `True` will make an agent report an order as routed when nothing left the
  process. Validation and transport are separate concerns; keep them visibly separate.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/singapore-exchange-sgx-api-integration/scripts`
- Assert the A50 tick regime directly, because it is the figure most tables get wrong:
  `validate_derivatives_order("CN", OrderSide.BUY, 1, price="12501")` must validate, and
  `price="12500.00004"` must return `INVALID_TICK_SIZE`.
- Assert the trade-type split: `"NK"` at `price="38001"` is `INVALID_TICK_SIZE` as an
  outright and valid with `trade_type=SGXTradeType.CALENDAR_SPREAD`.
- Assert the refusal to guess: `SGX_DERIVATIVES_CONTRACTS["CN"].tick_size_for(
  SGXTradeType.CALENDAR_SPREAD)` raises `TickSizeUnavailableError`, and
  `validate_derivatives_order("TW", ...)` raises `UnknownContractError` naming `TWN`.
- Assert the securities band edges: `get_sgx_st_minimum_bid_size("0.1999")` is
  `0.001`, `("0.20")` is `0.005`, `("0.9999")` is `0.005` and `("1.0000")` is `0.01`;
  a structured warrant at `"1.50"` is `0.005` where an ordinary share is `0.01`.
- Assert tick values independently: `NK` is JPY 2,500 outright, JPY 500 on a spread and
  JPY 125 at T@IC; `TWN` is US$10; `FEF` is US$1 per contract.
- Reconcile the contract table against the current SGX contract specifications and any
  SGX circulars issued since each spec's `verified_on` date before every release. The
  shipped figures were verified on 2026-08-28.
- Complete SGX conformance testing for the Titan interface you will use. These unit
  tests are not conformance and grant no production access.

## Related Skills

- `mas-singapore-algo-trading-guidelines`
- `hong-kong-exchange-hkex-orion-api`
- `minimum-fill-size-and-lot-rounding-logic`
- `exchange-tick-size-regime-tracking`
- `reference-data-symbol-mapping-across-vendors`
- `order-placement-idempotency`
- `binary-protocol-parsing-for-low-latency-feeds`

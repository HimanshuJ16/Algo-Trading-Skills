# Workflows for Singapore Exchange SGX API Integration

The full pre-trade validation sequence behind the summary in `SKILL.md`. Figures and
citations are in `references/standards.md`.

## A. Derivatives order bound for Titan-DT

1. **Confirm the destination engine.** Derivatives go to Titan-DT; a Singapore cash
   equity does not. If the instrument is an SGX-listed security, jump to section B.
   `SGXMarket` is returned on every validation result so a router can assert this
   rather than infer it from the symbol's shape.

2. **Resolve the contract specification by product code.**
   `validate_derivatives_order(product_code=..., contracts=...)` looks the code up
   after stripping whitespace and upper-casing. Three outcomes:
   - found → continue;
   - not found → `UnknownContractError`. Do **not** fall back to "no spec, no check":
     an unrecognised code is most often a vendor symbol, a typo, or a contract that
     left the exchange (`TW`);
   - found but the spec's `verified_on` is older than your reconciliation policy →
     treat it as reference data to refresh before trading, not as a fact.

3. **Choose the trade type before choosing the tick.**
   - `OUTRIGHT` — a single-contract price.
   - `CALENDAR_SPREAD` — a strategy price, i.e. the differential, not a leg price.
   - `TRADE_AT_INDEX_CLOSE` — entered under the T@IC ticker (`NKTI`, `TWNTI`), and on
     the finest increment SGX publishes for that contract.
   - `NEGOTIATED_LARGE_TRADE` — an off-book NLT report.
   `SGXContractSpec.tick_size_for()` raises `TickSizeUnavailableError` when SGX's
   published increment for that combination could not be verified. Resolve it from
   Titan-DT reference data; never substitute the outright increment, in either
   direction.

4. **Validate the price fields the order type actually has.**
   - `LIMIT` → limit price required, checked on tick.
   - `STOP_LIMIT` → limit price *and* stop trigger price required, both checked.
   - `MARKET` → no price; nothing to check. Do not synthesise one.
   A price of zero or below is `INVALID_PRICE`, not an off-tick price — they are
   different defects with different upstream causes (a missing quote versus a rounding
   bug).

5. **Check tick alignment in exact decimal arithmetic.** `Decimal(price) % tick == 0`,
   no tolerance. Pass prices as `str` or `Decimal`. A `float` is accepted and read
   through its shortest round-tripping repr, which recovers the intended decimal but
   does not make the upstream pipeline exact.

6. **Check the quantity.** A whole number of contracts, greater than zero. A `float`
   quantity that is integral (`10.0`) passes; `1.5`, `0`, `-5`, `"10"`, `None` and
   `NaN` do not. Lot sizing itself is out of scope.

7. **Compute notional if you need it for a downstream limit.**
   `SGXContractSpec.notional(price)` is `multiplier x price` in the contract currency:
   JPY 500 x index for `NK`, US$40 x index for `TWN`, 100 tonnes x US$/tonne for `FEF`.
   It is a contract value, not margin, and it is denominated in the contract currency —
   convert before aggregating across `CN` (USD), `NK` (JPY) and a SGD book.

8. **Branch on `status`, log `violations`.** `status` is the highest-precedence breach
   (`INVALID_PRICE` → `MISSING_LIMIT_PRICE` → `INVALID_TICK_SIZE` → `INVALID_QUANTITY`);
   `violations` is every breach found. Reporting only `status` makes a two-defect order
   take two round trips to fix.

## B. Securities order bound for Reach-ST

1. **Classify the security.** `ORDINARY` (stocks excluding preference shares, REITs,
   business trusts, company warrants), `STRUCTURED_WARRANT`, `DEBT`, or `ETF_ETN`. The
   class changes the scale: a structured warrant bids in half-cents to S$1.995 where an
   ordinary share moves to cents at S$1.00. `ETF_ETN` raises, because SGX-ST sets those
   bid sizes per instrument.

2. **Confirm the counter is SGD-denominated.** Non-SGD counters are refused: since
   15 July 2026 SGX no longer aligns HKD/RMB/JPY bid sizes to the home market, so the
   SGD scale is not transferable. Read those from reference data.

3. **Derive the minimum bid size from the order's price, every time.**
   `get_sgx_st_minimum_bid_size(price, security_class)`. Bands are keyed on an inclusive
   lower bound, so S$0.20 is the first price at S$0.005 and S$1.00 the first at S$0.01.
   Never cache a bid size against a symbol: the same stock changes band mid-session as
   it crosses S$0.20 or S$1.00.

4. **Check each price in its own band.** For a stop-limit, the trigger price may sit in
   a different band from the limit price and is validated on that band's bid size.

5. **Size the quantity elsewhere.** Board lot and minimum quantity are per-security and
   SGX-ST board lots become price-tiered on 5 October 2026. Use
   `minimum-fill-size-and-lot-rounding-logic`, then validate the price here.

## C. Before the order leaves the process

Tick legality is one gate of several, and it is the cheapest one. Still to clear:

1. Forced Order Range and the SGX-ST circuit breaker band, Clearing Member
   pre-execution value limits, and SFA licensing / SGX Approved Trader registration —
   `mas-singapore-algo-trading-guidelines`.
2. Daily price limits for the contract (A50: ±10% and ±15% with a cooling-off period).
3. Idempotent submission and ambiguous-timeout handling —
   `order-placement-idempotency`. A validated order that times out on submission may
   still have been accepted.

## D. Keeping the table current

1. Reconcile every `SGXContractSpec` against the current SGX contract specification on
   a fixed cadence and after any SGX circular affecting a contract you trade. Update
   `source` and `verified_on` in the same commit as the figure.
2. Do not reconcile against an archived PDF. SGX's own 2018-vintage specification files
   are still reachable and still show the pre-2020 A50 tick.
3. Watch for changes that keep the product code: the 22 June 2026 Mini-to-Micro Nikkei
   conversion changed both multiplier and tick under the unchanged code `NS`. A
   reference-data refresh keyed on "new code appeared" would have missed it entirely.
4. Purge and re-derive resting orders after any increment change; a price that was legal
   under the old increment is not necessarily legal under the new one.

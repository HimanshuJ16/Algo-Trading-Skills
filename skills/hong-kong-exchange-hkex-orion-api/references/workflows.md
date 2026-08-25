# Workflow: validating an HKEX order before OCG-C entry

Order of operations matters. Each step below either produces reference data the next
step needs, or fails in a way the next step cannot recover from.

## 1. Resolve reference data (before any price maths)

From the security master or the OMD-C Security Definition (11) message, obtain:

- **Spread Table Code** → `SpreadTable.from_omd_c_code(code)`. Code `01` is Part A,
  code `06` is Part E. Codes `03`/`04`/`05` raise `SpreadTableUnavailableError` because
  HKEX publishes no mapping for them; resolve those from your feed's OMD-C interface
  specification and pass the `SpreadTable` explicitly.
- **Board lot size** — issuer-set, 10 to 100,000 shares. Never default it.
- **Stock code** — the 5-digit HKEX security code.

Failing here is a *configuration* failure, not an order rejection. Do not fall back to
Part A and a board lot of 100; that produces a plausible-looking order priced against
the wrong rulebook.

## 2. Normalise and validate the stock code

`format_hkex_stock_code(raw)` zero-pads to five digits and rejects:

- non-numeric or empty codes,
- codes longer than five digits (`zfill(5)` would pass `"123456"` through unchanged),
- `"00000"`.

`classify_counter(code)` then labels the code under the HKD-RMB Dual Counter Model:
`0XXXX` → `HKD_COUNTER`, `8XXXX` → `RMB_COUNTER`, anything else → `OTHER`. This is a
label for the audit trail. Confirm actual dual-counter eligibility against HKEX's Dual
Counter Securities list before assuming the two legs are interchangeable.

## 3. Validate the order envelope

`side` ∈ {`BUY`, `SELL`}; `order_type` ∈ {`LIMIT`, `ENHANCED_LIMIT`, `SPECIAL_LIMIT`,
`AT_AUCTION`, `AT_AUCTION_LIMIT`}; `currency` ∈ {`HKD`, `RMB`, `CNY`, `USD`};
`quantity` a positive `int` of **shares**; `board_lot_size` an `int` in 1–100,000.

The board lot bound is not cosmetic: `quantity % 0` raises `ZeroDivisionError`
mid-validation, and `200 % -100 == 0` in Python, so a negative lot size would validate
every quantity.

## 4. Look up the minimum spread

`get_hkex_spread_table_tick_size(price, spread_table)` scans the Part's bands in
ascending order and returns the first band whose **upper-inclusive** bound the price
does not exceed.

Outside the Part's published range it raises `PriceOutOfRangeError`. Part A runs
`0.01`–`9,995.00`, Part B `0.50`–`9,999.95`, Part D `0.01`–`9,999.00`, Part E
`0.01`–`9,995.00`. There is no minimum spread outside those ranges, so there is nothing
to validate against and nothing safe to assume.

## 5. Test tick alignment exactly

`Decimal(price) % tick == 0`. No tolerance, and no float division. `float` inputs are
converted through `str()` so `0.005` reads as `Decimal("0.005")` rather than the binary
value actually stored.

## 6. Classify the quantity against the board lot

- integral multiple → `BOARD_LOT`, eligible for auto-matching;
- fewer shares than one board lot → `ODD_LOT`;
- more than one board lot, not an integral multiple → `SPECIAL_LOT`.

`ODD_LOT` and `SPECIAL_LOT` are both rejected for the continuous book. Route them to
the semi-automatic odd/special lot facility or reshape the quantity — do not retry them
against the auto-matching book, which will keep rejecting them.

## 7. Check the automatch size cap

`quantity / board_lot_size` must be ≤ **3,000 board lots**, in every trading session.
Slice a larger parent order before submission.

## 8. Emit the report and act on `violations`

`HkexOrionOrderReport.status` carries the highest-precedence breach —
`INVALID_TICK_SIZE`, then `INVALID_BOARD_LOT`, then `INVALID_ORDER_SIZE` — for routing
logic. `violations` carries **all** of them. A repair loop that reads only `status`
will fix the tick, resubmit, and be rejected again for the board lot.

`audit_notes` is a single human-readable line naming every breach, logged at `WARNING`
on rejection and `INFO` on success. It is the record to keep for post-trade review.

## 9. Outside this module

Before the order is genuinely safe to send, still check:

- the **24-spreads** opening quotation rule and the **9-times-nominal-price** rule
  (both need the security's nominal / previous closing price);
- trading session state, halts and VCM cooling-off;
- OCG-C session state, ClOrdID uniqueness and order-state tracking;
- your own pre-trade risk limits.

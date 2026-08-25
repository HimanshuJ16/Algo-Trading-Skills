# Standards — historical-order-book-reconstruction-from-message-logs

## Protocol facts (verified against primary sources)

Source: Nasdaq, **TotalView-ITCH 5.0 Interface Specification**, version 5.0, 03/06/2015
([nasdaqtrader.com](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification_5.0.pdf)).
This module implements the ITCH model; other market-by-order feeds are mapped onto it.

| Fact | Spec location |
|---|---|
| Order Reference Numbers are "day-unique". An `ADD` for an already-live id is a log-integrity error, not an update. | Sec. 4.3 Add Order Message |
| Modify messages carry a share count that is **deducted** from the order's remaining displayed shares; "NASDAQ may send multiple Modify Order messages for the same order reference number and the effects are **cumulative**". | Sec. 4.4 Modify Order Messages |
| "When the number of display shares for an order reaches zero, the order is dead and should be removed from the book." Reaching exactly zero is normal, not an anomaly. | Sec. 4.4 |
| Order Cancel (`X`) is "sent whenever an order on the book is modified as a result of a **partial cancellation**"; its Canceled Shares field is "the number of shares being removed from the display size of the order". | Sec. 4.4.3 |
| Order Delete (`D`) — "All remaining shares are no longer accessible so the order **must be removed from the book**." The message carries **no share count**. `CANCEL` and `DELETE` are therefore distinct message types. | Sec. 4.4.4 |
| Order Replace (`U`) carries **both** an Original Order Reference Number and a New Order Reference Number: "the NASDAQ system will use this new order reference number for all subsequent updates". | Sec. 4.4.5 |
| Order Replace `Shares` is "the new total **displayed quantity**" — an absolute value, unlike the deduction semantics of `X`/`E`. | Sec. 4.4.5 |
| "Since the side, stock symbol and attribution (if any) cannot be changed by an Order Replace event, these fields are not included in the message. Firms should retain the side, stock symbol and MPID from the original Add Order message." | Sec. 4.4.5 |
| Order Executed (`E`) carries Executed Shares; Order Executed With Price (`C`) additionally carries an Execution Price and a Printable flag. Both are deductions against the resting order. | Sec. 4.4.1, 4.4.2 |
| Trade (`P`) messages are for non-displayed order types and "do not affect the book"; Cross Trade (`Q`) reports bulk auction volume. Neither decrements a resting displayed order. | Sec. 4.5.1, 4.5.2 |
| "A field flagged as Price (4) has an implied 4 decimal places." Prices are integers on the wire; the maximum `Price (4)` value is 200,000.0000. | Sec. 3 Data Types |
| "Timestamps are represented as nanoseconds since midnight." Multiple messages may share a timestamp, so only a strict regression is an ordering error. | Sec. 3 Data Types |

Source: **LOBSTER** limit order book data, output data structure
([lobsterdata.com](https://php.lobsterdata.com/info/DataStructure.php)).

| Fact | Value |
|---|---|
| Message file event types | 1 submission · 2 cancellation (**partial** deletion) · 3 deletion (**total** deletion) · 4 execution of a visible limit order · 5 execution of a **hidden** limit order · 6 cross trade · 7 trading halt |
| Price unit | "Dollar price times 10000 (i.e. a stock price of $91.14 is given by 911400)" — the same 4 implied decimals as ITCH `Price (4)` |
| Direction | `1` = buy limit order, `-1` = sell limit order |
| Time | Seconds after midnight, millisecond to nanosecond precision |

LOBSTER types 5, 6 and 7 have **no** counterpart in this engine's message set by design: a
hidden execution has no resting displayed order to decrement, and cross trades and halts do
not modify individual displayed orders.

## Feed mapping

| Canonical type | Nasdaq ITCH 5.0 | LOBSTER |
|---|---|---|
| `ADD` | `A` (no MPID), `F` (with MPID) | 1 |
| `CANCEL` (partial deduction) | `X` | 2 |
| `DELETE` (total removal) | `D` | 3 |
| `EXECUTE` (deduction) | `E`, `C` | 4 |
| `REPLACE` (absolute re-quote, new id) | `U` | — (LOBSTER emits 3 + 1) |
| *not applicable* | `P`, `Q`, `B` | 5, 6, 7 |

**CME MDP 3.0 Market by Order — partially verified.** CME's Market by Order book is built
from per-order actions (New / Change / Delete) carried on `MDIncrementalRefreshOrderBook`,
with orders identified by `OrderID` and ranked within a price/side by tag
37707-`MDOrderPriority`
([CME Group Client Systems Wiki](https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/MDP+3.0+-+Market+by+Order+-+Book+Management)).
The detailed pages describing whether `OrderID` and priority are retained across a
cancel-replace could not be retrieved during this review, so **no mapping of CME's
cancel-replace onto `REPLACE` is asserted here**. Confirm that behaviour against the current
CME MDP 3.0 specification before using this engine on MDP data — if CME retains the OrderID,
the replacement must be sent with `new_order_id` equal to `order_id`.

## Engineering standards enforced by this module

| Standard | Rationale |
|---|---|
| Order lookups are O(1) hash-map operations. | Multi-million-message replays; a linear scan makes replay O(N·M). |
| L2 price-level aggregation is maintained incrementally, not rebuilt per snapshot. | Tick-by-tick snapshotting is the intended workload. Measured on a 20,000-order book with a snapshot after every message: 52.7s rebuilding vs 0.68s incremental. |
| Prices are stored as integer ticks, never as float dictionary keys. | Binary floats for the same tick can differ in their last bits and split one price level in two, understating BBO depth. |
| Bids sort descending, asks ascending. | Best price first at index 0. |
| Crossed (bid > ask) and locked (bid == ask) are reported as **separate** conditions. | They are distinct microstructure states; collapsing them into one flag loses information and mislabels a locked book. |
| Every message that cannot be applied to a consistent book is counted, never silently dropped. | A gap-corrupted book replays without error; without a counter the corruption is invisible. |
| Malformed input raises; ambiguous-but-well-formed input is recorded as a violation. | Separates "your data is broken" from "your call is broken". |

## Known limitations

- **Displayed book only.** Hidden and reserve (iceberg) quantity is not modelled; see
  `iceberg-order-simulation-and-detection`.
- **No queue position.** Per-level order counts are tracked, but not each order's rank
  within its level, so this cannot answer "where am I in the queue".
- **No transport-layer gap detection.** Integrity violations are detected only once a
  message fails to match the book; a dropped `ADD` whose `CANCEL` also went missing leaves
  no trace here. Pair with sequence-number checking at the transport layer.
- **Single symbol per engine instance.** `symbol` is used for audit output only; messages
  are not filtered by symbol.
- **A retired order id is not remembered.** An `ADD` reusing the id of an order already
  deleted earlier in the session is accepted silently. Detecting it would mean retaining
  every id for the whole day, which is unbounded memory over a multi-million-order session.
- **Recovery from a duplicate id is a heuristic.** A duplicate `ADD` supersedes the stale
  order on the assumption that a `DELETE`/`EXECUTE` was dropped. That is the more likely
  cause, not a certainty, which is why it is always reported.

## Category

`backtesting-methodology`

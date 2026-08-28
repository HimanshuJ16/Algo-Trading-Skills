# Standards for Nasdaq TotalView-ITCH Feed Parsing

Primary source: **Nasdaq TotalView-ITCH 5.0**, Nasdaq Stock Market LLC —
<https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf>
(sections cited below). Transport framing: **MoldUDP64 Protocol Specification V 1.00** —
<https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf>.

These are vendor technical specifications, not regulation. Nothing in this skill
imposes a regulatory obligation.

## Wire-format invariants

| Item | Engineering standard | Spec source |
|---|---|---|
| Byte order | Big-endian `>` MUST be used for all struct unpacking. "All integer fields are big endian (network byte order) binary encoded numbers. Unless otherwise noted, they are unsigned." | ITCH 5.0, Data Types |
| Price encoding | `Price (4)`: 4-byte integer with 4 implied decimal places. Divide by 10,000. | ITCH 5.0, Data Types |
| Price ceiling | Maximum `Price (4)` value in TotalView-ITCH is 200,000.0000 (`0x77359400` = 2,000,000,000 ticks). A larger value indicates a scaling or offset error. | ITCH 5.0, Data Types |
| Alpha fields | ASCII, "left justified and padded on the right with spaces." Strip right padding only. | ITCH 5.0, Data Types |
| Timestamp | 6 bytes, "Nanoseconds since midnight." 48-bit unsigned; cannot wrap within a session (2⁴⁸ ns ≈ 78 h). The message tables name no time zone — wall-clock mapping needs the session date and the venue's local zone. | ITCH 5.0, Data Types |
| Buy/Sell Indicator | `"B"` = Buy Order, `"S"` = Sell Order. No other value is valid. | ITCH 5.0 §1.3.1 |
| Framing | MoldUDP64 downstream packet: 20-byte header, then message blocks each prefixed by a 2-byte big-endian Message Length that excludes itself. | MoldUDP64 V1.00 |

## Decoded message layouts

Offsets and lengths are quoted from the spec's message tables. Total length
includes the 1-byte Message Type at offset 0.

| Type | Message | Spec | Total bytes | Fields after Message Type |
|---|---|---|---|---|
| `A` | Add Order – No MPID Attribution | §1.3.1 | 36 | Stock Locate(2) Tracking(2) Timestamp(6) OrderRef(8) Buy/Sell(1) Shares(4) Stock(8) Price(4) |
| `F` | Add Order with MPID Attribution | §1.3.2 | 40 | …as `A`, plus Attribution(4) |
| `E` | Order Executed | §1.4.1 | 31 | Locate(2) Tracking(2) Timestamp(6) OrderRef(8) ExecutedShares(4) MatchNumber(8) |
| `C` | Order Executed With Price | §1.4.2 | 36 | …as `E`, plus Printable(1) ExecutionPrice(4) |
| `X` | Order Cancel | §1.4.3 | 23 | Locate(2) Tracking(2) Timestamp(6) OrderRef(8) CancelledShares(4) |
| `D` | Order Delete | §1.4.4 | 19 | Locate(2) Tracking(2) Timestamp(6) OrderRef(8) |
| `U` | Order Replace | §1.4.5 | 35 | Locate(2) Tracking(2) Timestamp(6) OrigOrderRef(8) NewOrderRef(8) Shares(4) Price(4) |
| `P` | Trade Message (Non-Cross) | §1.5.1 | 44 | Locate(2) Tracking(2) Timestamp(6) OrderRef(8) Buy/Sell(1) Shares(4) Stock(8) Price(4) MatchNumber(8) |

## Book-state semantics

| Rule | Engineering standard | Spec source |
|---|---|---|
| Cumulative deduction | Deduct the modify message's share count from the order's current displayed size; effects of multiple modify messages are cumulative. | §1.4 |
| Zero means dead | "When the number of display shares for an order reaches zero, the order is dead and should be removed from the book." | §1.4 |
| `E` and `C` both execute | Both message types execute against the same order and are cumulative; `C` exists only because the fill price differed from the display price. | §1.4.2 |
| `X` ≠ `D` | `X` is a partial cancellation carrying Cancelled Shares; `D` removes all remaining shares and carries no count. | §1.4.3, §1.4.4 |
| Replace re-identifies the order | "Please note that the Nasdaq system will use this new order reference number for all subsequent updates." | §1.4.5 |
| Replace omits side/stock | Side, stock symbol and attribution cannot change and are absent from the message: "Firms should retain the side, stock symbol and MPID from the original Add Order message." | §1.4.5 |
| Replace quantity is absolute | Shares on `U` is "The new total displayed quantity", not a deduction. | §1.4.5 |
| `P` does not touch the book | "Since Trade Messages do not affect the book, however, they may be ignored by firms just looking to build and track the Nasdaq execution system display." | §1.5.1 |
| `P` reference number is zero | "Effective December 6, 2010, Nasdaq will populate the Order Reference Number field within the Trade (Non-Cross) message as zero." | §1.5.1 |
| `P` side is constant | "Effective 07/14/2014, this field will always be 'B' regardless of the resting side." | §1.5.1 |
| Reference numbers are day-unique | The Add Order message "includes a day-unique Order Reference Number used by Nasdaq to track the order." | §1.3 |

## Out of scope

System Event `S`, Stock Directory `R`, Stock Trading Action `H`, Reg SHO `Y`,
Market Participant Position `L`, MWCB, LULD, Quoting Period Update, Operational
Halt, Cross Trade `Q`, Broken Trade `B`, NOII `I`, RPII `N` and the Direct
Listing message are not decoded here. Skip them by their transport-declared
length rather than guessing a layout.

Nasdaq BX and Nasdaq PSX publish separate TotalView-ITCH 5.0 specifications with
the same message formats but independent reference-number spaces; do not replay
two venues through one engine instance.

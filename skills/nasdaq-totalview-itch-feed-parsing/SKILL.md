---
name: nasdaq-totalview-itch-feed-parsing
description: >-
  Use when decoding Nasdaq TotalView-ITCH 5.0 add, execute, cancel, delete and replace
  messages to maintain a market-by-order book. MoldUDP64 framing, line arbitration and
  gap recovery sit upstream of this decode step.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: nasdaq-itch, binary-protocol, totalview-itch, itch-5.0, l3-order-book, feed-parsing, struct-unpack
  brokers_frameworks: "Nasdaq TotalView-ITCH 5.0 Spec; MoldUDP64; SoupBinTCP; Python Struct Binary Unpacking; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when decoding raw binary direct market data from Nasdaq venues (Nasdaq, Nasdaq BX, Nasdaq PSX) and maintaining the market-by-order (L3) book that results. TotalView-ITCH 5.0 publishes every displayed order's full lifecycle with nanosecond timestamps over MoldUDP64 or SoupBinTCP, which is what makes queue-position modelling, micro-price construction, and order-flow research possible in the first place.

The engine decodes the order-lifecycle message set — Add Order `A`/`F`, Order Executed `E`/`C`, Order Cancel `X`, Order Delete `D`, Order Replace `U` — plus the non-book Trade message `P`, and keys the book on the day-unique Order Reference Number.

Its second job is **telling you when the book is wrong**. A feed with a dropped message replays without raising; it just produces a book that quietly disagrees with the venue. Every message that cannot be applied to a consistent book is counted and surfaced rather than absorbed.

## When NOT to Use

- **As a transport or gap-detection layer.** This decodes one already de-framed message. MoldUDP64 sequencing, A/B line arbitration and retransmission all sit upstream — see `sequence-number-gap-detection-for-feeds` and `exchange-multicast-feed-handling`.
- **For the full ITCH message set.** System Event `S`, Stock Directory `R`, Trading Action `H`, LULD, MWCB, NOII and Cross Trade `Q` are not decoded. Skip unknown types by their transport-declared length; never guess a layout.
- **For a non-Nasdaq feed.** ITCH 5.0 layouts do not transfer. NYSE XDP is little-endian with a different message set — see `nyse-arca-integrated-feed-handling`.
- **For aggregated L2 depth or BBO.** This maintains the per-order map only. Aggregation into price levels and BBO is `historical-order-book-reconstruction-from-message-logs`; consuming pre-aggregated depth is `order-book-depth-processing-l2-l3`.
- **On a latency-critical production hot path in CPython.** Sustained full-depth ITCH rates need C++/Rust/FPGA; this is a reference decoder for replay, research and conformance testing. See `binary-protocol-parsing-for-low-latency-feeds` for the zero-copy decode techniques.
- **For multi-venue replay in one instance.** One engine holds one venue's reference-number space; Nasdaq, BX and PSX numbering is independent. Shard upstream.

## Prerequisites

- A **de-framed** ITCH 5.0 message: exactly one message starting at its 1-byte Message Type. A MoldUDP64 downstream packet carries a 20-byte header followed by message blocks, each prefixed with a 2-byte big-endian message length that excludes the length field itself — strip that prefix before calling in.
- The **ITCH 5.0 specification at the version the feed publishes**. Field offsets change between protocol versions; ITCH 4.1 timestamps are not even the same width.
- A **chronologically ordered** stream. The engine flags a timestamp regression but cannot repair one.
- Python 3.9+. Standard library only.

## Workflow

1. **De-frame before you decode.** Read the transport's length prefix and confirm the whole message is present. The engine rejects any message whose length is not the spec's exact total for its type (`A` 36, `F` 40, `E` 31, `C` 36, `X` 23, `D` 19, `U` 35, `P` 44 bytes including the type byte) — a leftover 2-byte MoldUDP64 prefix shows up here rather than as silent field shift.

2. **Dispatch on the type byte, and treat both Add variants as adds.**
   - **Decision point — `F` is not an optional extra.** Add Order with MPID Attribution carries the identical layout to `A` plus a 4-byte Attribution field, and rests on the book exactly the same way. Attributed quotes are a large share of displayed liquidity; dropping `F` does not degrade the book, it hollows it out.

3. **Apply modify messages as cumulative deductions.**
   - Spec §1.4: deduct the shares stated in the modify message from the order's current displayed size, and when it reaches zero the order is dead and must be removed.
   - **Decision point — `E` and `C` are both executions.** Order Executed With Price `C` exists only because the fill happened away from the display price; it carries the same Executed Shares semantics. Ignoring `C` leaves already-executed shares resting on the book.
   - **Decision point — `X` is not `D`.** Order Cancel carries a Cancelled Shares count and is a *partial* reduction. Order Delete carries no share count and removes everything remaining. Mapping a delete onto a cancel requires inventing a share count.

4. **Handle `U` Replace as a delete-plus-add under a new identity.**
   - **Decision point — the reference number changes.** The spec is explicit that "the Nasdaq system will use this new order reference number for all subsequent updates." Keeping the replacement under the old number makes every later message for that order look like a gap.
   - **Decision point — `U` carries neither side nor stock.** They cannot change, so they are omitted; "Firms should retain the side, stock symbol and MPID from the original Add Order message." If the original is not on the book, the replacement *cannot* be created — there is no side to place it on. Report the gap instead of inventing liquidity.
   - **Decision point — Shares on `U` is the new total displayed quantity**, not a deduction.

5. **Route `P` away from the book.** Trade Message (Non-Cross) reports a match between non-displayable order types. The spec states trade messages do not affect the book, and since 2010-12-06 its Order Reference Number is populated as zero. Use it for time-and-sales and volume; never as an order lifecycle event.

6. **Classify every anomaly before trusting the book.** Malformed bytes (wrong length, non-ASCII Alpha field, a Buy/Sell Indicator that is not `B`/`S`, a Printable flag that is not `Y`/`N`) raise `ITCHParseError` immediately. A well-formed message that cannot be applied is an *integrity violation*, counted in `violations_by_kind`: `UNKNOWN_ORDER`, `DUPLICATE_ORDER_ID`, `OVER_EXECUTE`, `OVER_CANCEL`, `TIMESTAMP_REGRESSION`, `PRICE_OUT_OF_RANGE`.
   - **Decision point — choose the policy before the run.** `strict=True` raises `ITCHBookIntegrityError` on the first violation, which is right for a validated production pipeline; the default records and continues, which is right for exploratory replay of an imperfect archive. Either way, **a non-zero violation count invalidates any microstructure statistic computed from that replay.**

7. **Generate the audit report.** `generate_report()` returns `PARSER_SUCCESS` only when `integrity_violation_count == 0`, and `PARSER_INTEGRITY_VIOLATIONS` with a per-kind breakdown otherwise.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Wrong endianness.** ITCH 5.0 states "All integer fields are big endian (network byte order) binary encoded numbers." Little-endian or native (`<`, `=`) unpacking does not raise — it returns a plausible message with a corrupted price and a nonsense order reference number, which is worse than a crash. `=` is especially dangerous because it also enables alignment padding, silently shifting every field after the first.
- **Forgetting the Price (4) divisor — in either direction.** Raw `1500000` is $150.00, but a raw wire integer passed straight through reads as a perfectly plausible $1,500,000.00 order. Only the spec's stated ceiling catches it: Price (4) maxes out at 200,000.0000 (`0x77359400`), which the engine enforces as `PRICE_OUT_OF_RANGE`.
- **Silently absorbing a message for an order you never saw.** The natural implementation is `if ref_num in self.active_orders:` — which turns a dropped Add Order into a no-op and leaves a book that is wrong for the rest of the session with nothing in the output saying so. Every unmatched `E`/`C`/`X`/`D`/`U` is a divergence and must be counted.
- **Letting a deduction go negative.** `shares -= executed_shares` followed by `if shares <= 0: delete` looks like it handles over-execution. It does not: it *hides* it, because removing more shares than were resting means the book already diverged. It must be flagged, not clamped.
- **Skipping `U` Replace because the book "still works".** It does not. The original order stays resting at a stale size forever, and every subsequent message under the new reference number becomes an `UNKNOWN_ORDER`. One dropped replace poisons one order for the rest of the day.
- **Feeding `P` into the book.** A Trade message with a zero Order Reference Number, run through a delete or execute path, produces an endless stream of phantom gaps at ref 0 — or, worse, silently deletes whatever real order happens to occupy that key.
- **Treating the `P` Buy/Sell Indicator as the resting side.** Effective 2014-07-14 the field is always `B` regardless of the actual resting side. Any aggressor-side classification built on it is not a signal, it is a constant.
- **Misreading the 6-byte timestamp.** It is a 48-bit big-endian nanoseconds-since-midnight counter; decoding it as 32-bit truncates and as 64-bit consumes two bytes of the next field. It cannot overflow within a session (2⁴⁸ ns ≈ 78 hours), and the spec names no time zone in the message tables — mapping to wall-clock needs the session date and the venue's local zone.
- **Stripping whitespace from both ends of an Alpha field.** Alpha fields are "left justified and padded on the right with spaces", so a *leading* space is evidence the field did not start where the layout says. `.strip()` erases that evidence; `.rstrip()` preserves it.
- **Reusing an order reference number.** Reference numbers are day-unique. A second `A` for a live number silently overwrote the resting order in the naive implementation; it is now `DUPLICATE_ORDER_ID`.
- **Treating a clean replay as a validated one.** No exception raised means only that no message was malformed. Check `integrity_violation_count`.

## Verification

- **Layout conformance**: every `STRUCT_*` size plus the type byte must equal the spec's published total — `A` 36, `F` 40, `E` 31, `C` 36, `X` 23, `D` 19, `U` 35, `P` 44 — and every format string must start with `>`.
- **Baseline lifecycle**: Add `A` for AAPL, 100 shares @ $150.00 (`1500000` ticks, ref 1001) → order rests with `price_usd == 150.00`. Execute `E` 40 shares → 60 remaining. Delete `D` → order removed, `integrity_violation_count == 0`.
- **Replace semantics**: `U` from ref 1 to ref 2 must leave 2 on the book and 1 off it, set size to the message's absolute quantity, inherit side and stock from the original Add, and accept later updates under ref 2. A `U` whose original is unknown must count `UNKNOWN_ORDER` and create nothing.
- **Non-book messages**: a `P` must leave every resting order untouched, report `affects_book is False`, and not raise a gap for its zero reference number.
- **Integrity**: an execute/cancel/delete for an unknown order counts `UNKNOWN_ORDER`; over-deduction counts `OVER_EXECUTE`/`OVER_CANCEL` while an exact-size execution counts neither; a duplicate Add counts `DUPLICATE_ORDER_ID`; a backwards timestamp counts `TIMESTAMP_REGRESSION` while an equal one does not; a price above `0x77359400` counts `PRICE_OUT_OF_RANGE`. `strict=True` must raise `ITCHBookIntegrityError` on the first.
- **Malformed input**: empty bytes, a non-printable type byte, an unsupported type, a truncated message, trailing bytes, a Buy/Sell Indicator other than `B`/`S`, a Printable flag other than `Y`/`N`, and a non-ASCII Stock field must all raise `ITCHParseError` and must not increment `parsed_messages_count`.
- Run `python -m unittest discover -s skills/nasdaq-totalview-itch-feed-parsing/scripts` and confirm 50/50 pass.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `historical-order-book-reconstruction-from-message-logs`
- `sequence-number-gap-detection-for-feeds`
- `exchange-multicast-feed-handling`
- `nyse-arca-integrated-feed-handling`
- `order-book-depth-processing-l2-l3`

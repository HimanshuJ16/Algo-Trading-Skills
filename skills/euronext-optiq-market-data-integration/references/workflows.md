# Workflows — euronext-optiq-market-data-integration

Section references are to the Euronext *Optiq MDG Messages — Interface Specification*
v6.362.3 (SBE template 362); see `standards.md` for the full citation table.

## 1. Session start-up

1. Download the SBE template XML, Standing Data file, tick tables and feed configuration
   for the trading day (published from 02:00 CET, spec section 3.10).
2. Record **Price/Index Level Decimals** per Symbol Index from Standing Data (1007). One
   `EuronextOptiqMarketDataEngine` per instrument, constructed with that value.
3. Join the A and B multicast groups for each real-time channel you need, plus the
   corresponding snapshot channel. Keep one PSN tracker per **channel**, not per instrument.
4. Treat every book as unsynchronized until a snapshot or the morning book retransmission
   (from 04:00 CET) has been applied, then call `mark_book_synchronized()`.

## 2. Per-packet processing

1. Read the 16-byte packet header with `parse_market_data_packet_header`.
2. If Packet Flags bit 0 is set, LZ4 block-decompress the body (header excluded). Bound the
   output at 8192 bytes — the specification's maximum extracted packet size.
3. Feed the header to `observe_packet` and branch on the result:
   - `is_duplicate` or `is_out_of_order` → discard the packet; the book already has it, or
     a newer state supersedes it.
   - `gap_size > 0` → request the missing PSNs from the B line; if unavailable there,
     resynchronize the affected channel from the snapshot feed.
   - `mdg_restart_detected` → discard book state and wait for the book retransmission.
4. Walk the body with `iter_sbe_messages`. A `ValueError` means the Frame lengths did not
   tile the body: discard the entire packet, not just the malformed tail.
5. Dispatch on `header.template_id`, decoding blocks with the SBE template.

## 3. Aggregated-limit book maintenance (Market Update, 1001)

1. Read the repeating section: Market Data Update Type, Symbol Index, Number Of Orders,
   Price, Quantity.
2. Route by update type:
   - New/Updated Bid (3, 5) and Offer (4, 6) → `apply_limit_update` on the matching side.
   - Clear Book (254) → `clear_book()`.
   - Best Bid/Offer (1, 2) → maintain a *separate* BBO view. Do not merge into the depth
     book (spec section 6.12).
   - Trade, collar, RFQ/RFC and statistics update types → route elsewhere; they are not
     book limits.
3. Quantity 0 deletes the limit at that price. A null price (`-2^63`) is not a limit.
4. Number Of Orders may be 0 on a limit that exists purely from implied prices
   (section 6.11) — it is not a signal that the limit is empty.

## 4. Trading-state handling (Market Status Change, 1005)

1. Decode Book State, Status Reason, Order Entry Qualifier, Trading Period and Trading Side
   from the repeating section; absent or `255` means "unchanged".
2. Call `apply_market_status_change(book_state=..., order_entry_qualifier=...)`. An
   unrecognised enum value raises: investigate it as a template-version mismatch rather
   than defaulting it.
3. Read the gate off the report:
   - `is_continuous_trading` — the engine is matching on arrival.
   - `is_order_entry_allowed` — the venue accepts order entry, modification and cancellation.
   - `is_quoting_allowed` — the library's conservative conjunction, which additionally
     requires a synchronized and uncrossed book.
4. On a transition out of Continuous, pull quotes on the strategy side. Note that Cancel
   Only (3) and Cancel and Modify Only (2) still permit you to withdraw resting orders.

## 5. Derived microstructure signals

1. `mid = (best_bid_raw + best_ask_raw) / (2 · 10^decimals)`, computed from the integer
   prices so a half-tick mid is exact.
2. `spread = (best_ask_raw − best_bid_raw) / 10^decimals`.
3. Top-of-book imbalance `= (bid_qty − ask_qty) / (bid_qty + ask_qty)`, `0.0` on an empty
   book. A one-sided book saturates the ratio at ±1.0 and has no mid or spread — report
   that as missing rather than substituting the touch price.
4. A crossed or locked book is expected during Call (orders collected without matching) and
   is an anomaly during Continuous; the report exposes `is_crossed` for both cases.

## 6. Recovery paths

| Event | Detection | Action |
|---|---|---|
| Packet lost on one line | PSN gap on line A, present on line B | Process the B copy; no book impact |
| Packet lost on both lines | PSN gap on both | Mark unsynchronized, rebuild from the snapshot channel, re-synchronize |
| MDG restart / HA failover | Packet Flags bits 1–3 change; PSN restarts at 1 | Discard book state; rebuild from the intraday book retransmission |
| Trade retransmission | Technical Notification (1106) type 10 … 11 | Invalidate trades in the stated window, then apply the rebroadcast Full Trade Information (1004) |
| Corrupted packet | Frame lengths do not sum to the body length | Discard the whole packet; treat the PSN as lost |

## 7. Pre-production verification

1. Replay a captured multicast session and confirm the reconstructed book matches the
   snapshot channel at each End Of Snapshot (2102) boundary.
2. Inject a dropped packet, a duplicated packet, a reordered packet and a simulated MDG
   restart into the replay; confirm each is classified correctly and that quoting stops
   whenever the book is unsynchronized.
3. Confirm price scaling against Standing Data for at least one instrument in each decimal
   convention you trade, including any quoted in basis points or as a percentage of nominal.
4. Re-run the checks after every SBE template upgrade — Euronext ships them with
   `sinceVersion` / `deprecated` attributes, and enum sets have changed between versions.

# Broker & Framework Coverage — market-data-snapshot-plus-delta-reconciliation

The snapshot-plus-delta pattern is **not** uniform across venues. The table below records
what each venue's own documentation specifies; the differences are load-bearing, not
cosmetic, and a procedure copied from one venue to another will desynchronize silently.

| Exchange / Feed | Snapshot source | Book version field | Zero qty behavior | Continuity rule / re-sync trigger |
|---|---|---|---|---|
| Binance spot `@depth` | REST `GET /api/v3/depth` (`lastUpdateId`) | `U` (first update ID) / `u` (final update ID) | quantity `0` removes the price level | Discard buffered events with `u <= lastUpdateId`; first processed event must satisfy `U <= lastUpdateId+1 <= u`; if `lastUpdateId < U` of the first buffered event, re-fetch the snapshot |
| Binance USD-M futures `@depth` | REST depth snapshot | `U` / `u` / `pu` (previous final update ID) | quantity `0` removes the price level | First processed event must satisfy `U <= lastUpdateId AND u >= lastUpdateId` (**no `+1`**, unlike spot); thereafter each event's `pu` must equal the previous event's `u`, otherwise restart from the snapshot step |
| Coinbase Advanced Trade `level2` (`l2_data`) | WebSocket `snapshot` event on subscribe — no REST fetch | `sequence_num` is a **per-connection message counter**, not a book version | `new_quantity` of `"0"` means the level can be removed | `sequence_num` detects dropped / out-of-order messages on that connection; it cannot be aligned against a REST snapshot's book version |
| Bybit v5 `orderbook` | WebSocket `snapshot` message on subscribe — no REST fetch | `u` (update ID); `seq` is a cross-sequence used to compare data across book depths; `cts` is a matching-engine timestamp, not a sequence | size `0` means the level was fully filled or cancelled — delete it | A `snapshot` message supersedes local state unconditionally; `u == 1` signals a service restart and requires overwriting the local book |

Sources (retrieved 2026-08-25):

- Binance spot — *How to manage a local order book correctly*, `binance/binance-spot-api-docs`,
  <https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md>
- Binance USD-M futures — *How To Manage A Local Order Book Correctly*,
  <https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly>
- Coinbase Advanced Trade — Level2 (`l2_data`) WebSocket channel reference,
  <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/websocket/level2>
- Bybit — v5 public `orderbook` WebSocket stream,
  <https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook>

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with high-frequency market microstructure order book reconstruction practice. No
regulator surveyed here prescribes a snapshot/delta reconciliation procedure — the rules above
are venue API contracts, not regulatory requirements. Where a reconstructed book feeds a
best-execution or quote-verification obligation, that obligation attaches to the jurisdiction
and instrument concerned — it is not created by this skill, and none of the crypto venues in
the table above is assumed here to fall under any particular best-execution regime. Confirm
the applicable regime separately before treating a locally reconstructed book as an
execution-quality record.

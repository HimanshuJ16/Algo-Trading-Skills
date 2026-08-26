# Workflows for Iceberg Order Execution Routing

## 1. Classify iceberg support per broker-and-exchange pair

Record one of three states, not a boolean:

| State | Where the reserve rests | Refill cost to the client | Failure exposure |
|---|---|---|---|
| `NATIVE_EXCHANGE` | Exchange matching engine | None | Exchange outage only |
| `BROKER_SIMULATED` | Broker servers | None | Broker outage, broker refill latency, no exchange order-handling protection |
| `UNSUPPORTED` | Your process | One network round trip per refill | Client disconnect leaves the parent unworked |

Source the classification from the broker's per-exchange order-type documentation, not from the presence of a display-size field in the API. Where the documentation is silent, record `BROKER_SIMULATED` — the conservative reading.

Re-verify after every broker API version change; the mapping is a per-exchange table, and exchanges get added to it. See `broker-api-changelog-diffing-tool`.

## 2. Resolve the effective display quantity

1. Round the requested peak **down** to `lot_size`.
2. If the result is below `min_display_quantity`, raise it to the next whole lot at or above the minimum.
3. If the result now covers the whole parent quantity, abandon the iceberg and route a plain limit order — an iceberg with an empty reserve is a plain order carrying extra rejection risk.
4. Report any adjustment to the caller. Silently changing the displayed size changes the order's market footprint.

`min_display_quantity` and `lot_size` are per venue **and** per security (Nasdaq: one round lot; T7: per-security minimum peak; CME: per-product). Do not carry one venue's value to another.

## 3. Native / broker-simulated dispatch

- Build a single parent payload carrying the venue's native parameter name (`DisplayQty`, `displaySize`, `icebergQty`, `MaxShow`).
- Validate time in force **before** submission. Binance spot rejects `icebergQty` without `timeInForce=GTC`. Raise rather than rewriting the caller's time in force — converting an IOC to a GTC turns a fill-or-move order into a resting one.
- Apply display randomisation only where the venue offers it natively (T7 min/max peak volume). Where it does not, report the requested randomisation as ignored; there is no way to honour it from the client on a native order.
- Expect the peak to lose time priority on every refill. Any fill-rate model that assumes the reserve inherits the original timestamp will be optimistic.

## 4. Synthetic client-side slice management

1. Compute the lot-aligned randomisation band $[\,Q_{\text{display}}(1-p),\; Q_{\text{display}}(1+p)\,]$, floored at `max(min_display_quantity, lot_size)`.
2. Bound the schedule **before** building it: worst-case slice count is $\lceil Q_{\text{total}} / Q_{\min} \rceil$. Reject above the configured ceiling rather than emitting thousands of child orders.
3. Draw each slice from a **seeded** RNG. Record the seed alongside the parent order ID so the schedule can be reconstructed.
4. Merge any final slice below `min_display_quantity` into its predecessor. The tail is the most informative message in the sequence: a lone odd lot marks both the existence and the exhaustion of the parent.
5. Dispatch one child at a time, keyed by an idempotent client order ID (`order-placement-idempotency`). Wait for the fill before sending the next — the whole point of the schedule is that only one slice is visible at a time.

### Failure handling on the synthetic path

| Event | Correct response |
|---|---|
| Child order rejected | Classify the rejection before retrying. A size or price rejection will repeat; a throttle rejection will not. |
| Client disconnect mid-schedule | On reconnect, reconcile open orders **before** sending the next slice. The last child may be live and partially filled; re-sending it double-fills the parent. |
| Partial fill of a child slice | The remaining child quantity is still displayed. Do not send the next slice until the current one is closed, or the displayed quantity doubles. |
| Instrument halted | Stop the schedule. Do not queue slices against a halted book — see `execution-algo-behavior-under-halted-instrument`. |
| Session close reached | The remaining parent quantity is unworked. Decide explicitly whether it carries to the next session; do not let the schedule silently expire. |

## 5. Cost accounting and audit output

- **Client refill latency** = $(N_{\text{slices}} - 1) \times$ `client_refill_round_trip_ms`, on the synthetic path only. The per-refill figure is the full fill-notification-in plus replacement-order-out round trip, measured from your own telemetry.
- **Venue-side refill latency** is not observable from the client. Report it as unknown. Reporting zero has repeatedly been read as "instant".
- **Message count** = number of child orders. Check against venue order-to-trade-ratio limits and message-rate fee tiers.
- **Queue priority loss** applies in every iceberg mode; carry the flag through to any downstream fill-probability model.
- Label unsent child orders `PLANNED`. A routing plan is not an execution record, and downstream reconciliation must not be able to mistake one for the other.

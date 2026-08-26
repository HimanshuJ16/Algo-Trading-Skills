# Iceberg Routing Pre-Flight Checklist

## Venue capability

- [ ] Is iceberg support recorded per **broker-and-exchange pair** as `NATIVE_EXCHANGE` / `BROKER_SIMULATED` / `UNSUPPORTED`, sourced from the broker's per-exchange order-type documentation?
- [ ] Where the documentation is silent, has the pair been recorded as `BROKER_SIMULATED` rather than assumed native?
- [ ] For a `BROKER_SIMULATED` venue, has the broker-outage exposure of the resting reserve been accepted deliberately?
- [ ] Is `native_parameter_name` set to the venue's actual field (`DisplayQty`, `displaySize`, `icebergQty`, `MaxShow`)?

## Order constraints

- [ ] Are `min_display_quantity` and `lot_size` set from this venue **and this security**, not carried over from another venue?
- [ ] Is the effective display quantity after rounding still below the parent quantity (otherwise route a plain limit order)?
- [ ] Has any display-quantity adjustment been surfaced to the caller rather than applied silently?
- [ ] Has the time in force been validated against the venue's iceberg restriction (e.g. Binance `icebergQty` requires GTC) **without** rewriting the caller's value?

## Synthetic schedule

- [ ] Is the RNG seeded, and is the seed recorded against the parent order ID?
- [ ] Is the worst-case child-order count bounded and checked before the schedule is generated?
- [ ] Does the schedule sum exactly to the parent quantity?
- [ ] Is every slice lot-aligned and at or above `min_display_quantity`?
- [ ] Is any final slice below the minimum display size merged into its predecessor rather than sent alone?
- [ ] Does the child-order count clear the venue's order-to-trade-ratio limit and message-rate fee tier?

## Cost and risk modelling

- [ ] Is `client_refill_round_trip_ms` calibrated from your own fill-to-acknowledgement telemetry rather than left at zero?
- [ ] Is venue-side refill latency reported as unknown rather than as zero?
- [ ] Does the downstream fill-probability model account for **queue priority loss on every refill, native included**?

## Dispatch safety

- [ ] Are unsent child orders labelled `PLANNED`, so no downstream reconciliation can read the plan as an execution?
- [ ] Does the dispatch layer use idempotent client order IDs?
- [ ] Is there a reconnect path that reconciles open orders **before** sending the next slice?
- [ ] Is there a defined response for child rejection, partial fill, instrument halt, and session close?

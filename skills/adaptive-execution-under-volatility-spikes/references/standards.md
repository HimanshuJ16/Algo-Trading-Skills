# Standards for Adaptive Execution Under Volatility Spikes

## Decision Policy

The thresholds below are **illustrative strategy defaults**, not regulatory or venue settings. Calibrate them per instrument, session, volatility estimator, and execution objective. Store the calibration version with every decision so a production event can be replayed deterministically.

| Metric | Default threshold | Engine action | Required caller action |
|---|---:|---|---|
| Normal volatility | `z < 2.0` | Standard participation, child size, and normal offset | Continue only after independent pre-trade checks pass. |
| High volatility | `2.0 <= z < 5.0` | Reduce participation and child size by 50%; use high-volatility offset | Re-check price collars, quantity limits, venue state, and feed freshness. |
| Critical shock | `z >= 5.0` | Return `halt_trading=True` with zero participation and zero child size | Do not submit new orders; cancel and reconcile working orders for the parent. |

A z-score must have a documented estimator, lookback, sampling interval, session treatment, and missing-data policy. A trailing 20-period mean is one possible baseline, not a universal requirement. The input is a signal in the configured units; it is not a direct measure of executable liquidity.

## Safety Invariants

- `0 <= base_participation_rate <= 1`.
- `base_child_order_size >= 1`.
- `volatility_threshold_high >= 0` and `volatility_threshold_critical > volatility_threshold_high`.
- All numeric thresholds and the evaluated volatility value must be finite.
- Missing or invalid volatility is a validation failure and must be handled as fail-closed by the caller; it is never interpreted as normal volatility.
- The engine does not enforce market-access controls, credit/position limits, price collars, trading pauses, or cancel completion. Those controls remain in the EMS/broker/exchange integration.

## Regulatory and Venue Touchpoints

- **SEC Exchange Act Rule 15c3-5 (Market Access Rule)**: electronic market access requires reasonably designed automated pre-trade financial and regulatory controls. This engine is one input to an EMS; it is not a replacement for those controls.
- **FINRA Regulatory Notice 15-09**: algorithmic strategies should have documented development/change controls, independent testing, fast disable mechanisms, real-time monitoring, alerts, reconciliation, and post-deployment review.
- **U.S. Limit Up-Limit Down Plan**: price bands and trading pauses are venue/market-structure controls. A wider strategy offset does not authorize an order outside a band or during a pause.
- **Best execution and supervision**: adaptive sizing does not by itself establish best execution, prevent manipulation, or satisfy product-specific obligations. Obtain legal/compliance review for the instruments and jurisdictions traded.

Authoritative references:

- [SEC Rule 15c3-5 market-access FAQs](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0)
- [FINRA Regulatory Notice 15-09](https://www.finra.org/industry/notices/15-09)
- [SEC Limit Up-Limit Down Plan and associated events](https://www.sec.gov/file/limit-limit-down-pilot-plan-and-associated-events)

## Operational Semantics

- `halt_trading=True` is a decision signal, not an order-management side effect.
- On a halt, cancel all working child orders for the parent using stable client/order identifiers, retry safely, and reconcile acknowledgements before considering the parent inactive.
- A validation exception, stale-data alarm, missing feed, or EMS reconciliation failure must stop new routing through the same fail-closed path.
- Resumption requires an external policy: fresh data, venue trading enabled, risk limits available, no unexpected working orders, and an explicit manual or automated release. One normal sample is insufficient.
- Record input value, input timestamp, configuration/calibration version, regime, returned parameters, parent/order identifiers, and all halt/cancel/release events.
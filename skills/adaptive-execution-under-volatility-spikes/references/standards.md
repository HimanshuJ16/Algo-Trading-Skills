# Standards for Adaptive Execution Under Volatility Spikes

## Decision Policy

The thresholds below are **illustrative strategy defaults**, not regulatory or venue settings. Calibrate them per instrument, session, volatility estimator, and execution objective. Store the calibration version with every decision so a production event can be replayed deterministically.

| Metric | Default threshold | Engine action | Required caller action |
|---|---:|---|---|
| Normal volatility | `z < 2.0` | Standard participation, child size, and normal offset | Continue only after independent pre-trade checks pass. |
| High volatility | `2.0 <= z < 5.0` | Reduce participation and child size by 50%; use high-volatility offset | Re-check price collars, quantity limits, venue state, and feed freshness. |
| Critical shock | `z >= 5.0` | Return `halt_trading=True` with zero participation and zero child size | Do not submit new orders; cancel and reconcile working orders for the parent. |

`limit_offset_bps` is defined by this engine as a distance *away from the aggressive side* of the caller's reference price: a buy limit at `ref * (1 - bps / 10_000)`, a sell limit at `ref * (1 + bps / 10_000)`. A larger offset is more passive, which is why the high-volatility offset exceeds the normal one; the same convention is used in `opening-auction-imbalance-based-execution`. This is a strategy convention, not a venue or vendor definition — if the EMS treats an offset as aggressiveness, translate the sign explicitly at the boundary or the high-volatility branch will chase a dislocating book.

A z-score must have a documented estimator, lookback, sampling interval, session treatment, and missing-data policy. A trailing 20-period mean is one possible baseline, not a universal requirement. The input is a signal in the configured units; it is not a direct measure of executable liquidity.

## Safety Invariants

- `0 <= base_participation_rate <= 1`.
- `base_child_order_size >= 1`.
- `volatility_threshold_high >= 0` and `volatility_threshold_critical > volatility_threshold_high`.
- All numeric thresholds and the evaluated volatility value must be finite.
- Missing or invalid volatility is a validation failure and must be handled as fail-closed by the caller; it is never interpreted as normal volatility.
- An engine reports `UNKNOWN` until an evaluation succeeds, and an evaluation that does not complete returns it to `UNKNOWN`. No fault may leave the regime reading `NORMAL` from an earlier successful call. `UNKNOWN` is never carried on a returned `ExecutionParameters`.
- The configuration is re-validated on every `evaluate`, so a runtime mutation or a partially applied reload fails closed instead of routing on out-of-range bounds.
- `enabled=False` is a bypass, not a safe default: it returns the base parameters, reports `NORMAL`, and performs no volatility validation whatsoever. Use it only where an independent control provides the protection.
- `current_regime` is single-writer instance state. One engine instance per instrument and parent order; the per-decision value is `ExecutionParameters.regime`.
- On a halt every numeric field is zeroed. Those zeros are sentinels, not an order instruction — a zero offset is the most aggressive value under the convention above, so callers must branch on `halt_trading` first.
- The engine does not enforce market-access controls, credit/position limits, price collars, trading pauses, or cancel completion. Those controls remain in the EMS/broker/exchange integration.

## Regulatory and Venue Touchpoints

This is engineering guidance, not legal advice. The regimes below bind **different populations of firms** and none of them universalises. Confirm which, if any, applies to the entity operating this overlay.

### US — SEC Exchange Act Rule 15c3-5 (Market Access Rule)

Jurisdiction: US. The obligation falls on **broker-dealers with market access**, or that provide customers access to an exchange or ATS — not on every firm running an algorithm through a retail broker, though the control design remains good practice. Where any electronic system is involved in executing an order, the required financial and regulatory risk-management controls must be automated and applied **pre-trade**, and must be under the direct and exclusive control of the broker-dealer with market access, subject to limited exceptions.

That last point is the architectural constraint for this skill: a strategy-side overlay cannot discharge a 15c3-5 obligation, because it is neither exclusive to nor controlled by the broker-dealer. This engine is one input to an EMS, never a substitute for those controls.

### US — FINRA Regulatory Notice 15-09

Jurisdiction: US, FINRA member firms. **Guidance ("effective practices"), not a rule.** It describes development and change-management processes, testing conducted independently of code development, mechanisms to disable an algorithm quickly with a minimal number of steps, real-time monitoring, alerts and reconciliation to identify unintended results, and post-deployment review of trading activity. The halt path, the calibration version, and the audit trail specified in this skill exist to make those practices evidenceable.

### EU / UK — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

Jurisdiction: EU, and the UK as assimilated law. Applies to investment firms engaged in algorithmic trading.

| Requirement | Source | Bearing on this skill |
|---|---|---|
| The firm must be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues ("kill functionality"). | RTS 6, Article 12 | **Mandatory, and this engine does not satisfy it.** `halt_trading=True` withholds new child orders; it cancels nothing. The cancel-and-reconcile step in `## Operational Semantics` is the integration point for real kill functionality. |
| Real-time monitoring of algorithmic trading for signs of disorderly trading, with real-time alerts generated within five seconds of the relevant event. | RTS 6, Article 16, and Article 16(5) for the five-second bound | Bounds how long a halt or a validation fault may sit unreported. A decision stream that batches or polls on a longer interval cannot meet it. |

### US — Limit Up-Limit Down (Plan to Address Extraordinary Market Volatility)

Jurisdiction: US NMS stocks. An NMS plan, approved as a pilot in 2012 and made permanent in 2019. Price bands are set around a reference price; a Limit State that persists for 15 seconds leads the primary listing exchange to declare a five-minute trading pause. These are venue and market-structure controls: a wider strategy offset does not authorize an order outside a band or during a pause, and this engine does not observe band or pause state at all.

### Best execution and supervision

Adaptive sizing does not by itself establish best execution, prevent manipulation, or satisfy product-specific obligations. Obtain legal and compliance review for the instruments and jurisdictions traded.

Authoritative references:

- [SEC Rule 15c3-5 market-access FAQs, Division of Trading and Markets](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0)
- [SEC final rule release 34-63241, Risk Management Controls for Brokers or Dealers with Market Access](https://www.sec.gov/files/rules/final/2010/34-63241.pdf)
- [FINRA Regulatory Notice 15-09, Guidance on Effective Supervision and Control Practices for Firms Engaging in Algorithmic Trading Strategies (March 2015)](https://www.finra.org/rules-guidance/notices/15-09)
- [Commission Delegated Regulation (EU) 2017/589 (RTS 6), EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589)
- [Limit Up-Limit Down Plan, official participant site](https://www.luldplan.com/)

## Operational Semantics

- `halt_trading=True` is a decision signal, not an order-management side effect.
- On a halt, cancel all working child orders for the parent using stable client/order identifiers, retry safely, and reconcile acknowledgements before considering the parent inactive.
- A validation exception, stale-data alarm, missing feed, or EMS reconciliation failure must stop new routing through the same fail-closed path.
- Resumption requires an external policy: fresh data, venue trading enabled, risk limits available, no unexpected working orders, and an explicit manual or automated release. One normal sample is insufficient.
- Record input value, input timestamp, configuration/calibration version, regime, returned parameters, parent/order identifiers, and all halt/cancel/release events.
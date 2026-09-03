# Standards — execution-algo-twap-vwap-slicing

## Regulatory obligations that actually bind an execution algorithm

Jurisdiction is stated per row. None of these are universal, and none of them prescribe
a TWAP/VWAP parameterisation — they govern the controls, testing, and records around the
algorithm, not its schedule.

### EU — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

This, not the best-execution reporting standards, is the regulation that applies to a
firm operating an execution algorithm. It is **mandatory** for investment firms engaged
in algorithmic trading in the EU.
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng))

| Article | Title | Bearing on this skill |
|---|---|---|
| Art. 5–8 | General testing / conformance testing / testing environments / controlled deployment | The schedule builder and lifecycle handlers must be tested off the production environment and deployed under control before a parent order is routed. |
| Art. 9 | Annual self-assessment and validation | Catch-up policy and cap parameters are part of what gets re-validated. |
| Art. 10 | Stress testing | Must withstand increased order flow — a slicer multiplies placements by `num_intervals`. |
| Art. 12 | Kill functionality | The firm must be able to cancel *any or all* unexecuted orders immediately. A parent order's outstanding child orders must be cancellable as a unit — see `execution-algorithm-kill-switch-integration`. |
| Art. 13 | Automated surveillance system to detect market manipulation | Child-order flow, including the jitter pattern, stays in scope for the firm's own manipulation surveillance. |
| Art. 15 | Pre-trade controls on order entry | Maximum order value and volume limits sit **outside** the slicer. This is the regulatory counterpart of `max_child_multiple`: an uncapped catch-up clip is exactly what a maximum-order-volume control exists to stop. |
| Art. 16 | Real-time monitoring | An in-flight parent order must be monitored by the responsible trader and by an independent risk function. |
| Art. 17 | Post-trade controls | Where the execution report belongs. |

### EU — best execution: what changed

The substantive obligation in **MiFID II Art. 27(1)** — take *all sufficient steps* to
obtain the best possible result for the client — is unchanged and still applies.

The two periodic reporting standards this skill's earlier revision cited are **no longer
in force**:

- **RTS 27** (venue execution-quality reports) and **RTS 28** (investment firms' top-five
  venue reports) were removed by **Directive (EU) 2024/790**, published in the Official
  Journal on 8 March 2024, which deleted MiFID II Art. 27(3) and Art. 27(6). Member
  States had 18 months to transpose it.
- ESMA had already told national regulators to **deprioritise supervisory action** on
  RTS 28 reporting in its public statement of 13 February 2024
  ([ESMA35-335435667-5871](https://www.esma.europa.eu/sites/default/files/2024-02/ESMA35-335435667-5871_Public_Statement_on_deprioritisation_of_supervisory_actions_on_RTS_28_reporting.pdf)).
- The Commission adopted a replacement RTS on **order execution policies** on 14 April
  2026, which formally repeals RTS 27 and RTS 28; it applies 18 months after entry into
  force. **Confirm the applicable date with counsel** — this timeline was still running
  at the time of writing and is the one claim here most likely to have moved.

Do not build an RTS 27/28 reporting obligation into a new execution stack.

### US — SEC

| Rule | Who it binds | Bearing on this skill |
|---|---|---|
| **Rule 15c3-5** (17 CFR 240.15c3-5), *Risk Management Controls for Brokers or Dealers with Market Access* | The broker-dealer providing market access — **not** a buy-side firm running its own algo | Requires pre-trade financial and regulatory risk controls under the BD's *direct and exclusive control*. Your child orders will be filtered by them; a schedule that ignores those limits produces rejections, not fills. Naked/unfiltered sponsored access is effectively prohibited. See `sec-rule-15c3-5-risk-controls-us`. |
| **Rule 605** of Reg NMS, *Disclosure of Order Execution Information* | Market centers, and (since the 2024 amendments) broker-dealers introducing or carrying ≥100,000 customer accounts | A **disclosure** rule for the sell side. A buy-side firm slicing its own parent order has no Rule 605 obligation. Amended by Release No. 34-99679 (adopted 6 March 2024, effective 14 June 2024); the compliance date was extended to **1 August 2026**. |
| **Rule 606** of Reg NMS | Broker-dealers routing *customer* orders | Order-routing disclosure. Same point: not an obligation of a firm executing its own orders. |

The earlier revision of this file cited Rules 605/606 as though they applied here. They
constrain the venues and brokers you route through; they are not a reporting duty this
skill creates.

## Broker / venue native algo behaviour (verified against vendor documentation)

Native venue algos are an alternative to slicing client-side. The trade-off is control
and transparency versus operational simplicity — a native algo's schedule and catch-up
behaviour are the venue's, not yours, and are generally not introspectable.

| Venue | What is actually offered | Verified detail |
|---|---|---|
| **Interactive Brokers** (IBALGO) | TWAP and **best-efforts** VWAP | TWAP targets the time-weighted average price from submission; available for US equities, options, futures, forex, and some non-US stocks. VWAP is *best-efforts* — IB's **Guaranteed VWAP is no longer supported**. `allow trading past end time` governs whether an incomplete order keeps working. GTC is not supported for IBAlgos. ([IBKR docs](https://www.interactivebrokers.com/docs/general/order-types/algorithmic-orders/ib-algorithms/vwap)) |
| **Binance** (Algo Orders) | **TWAP and VP (volume participation) — no native VWAP** | USDⓈ-M futures: `POST /sapi/v1/algo/futures/newOrderTwap`; required params `symbol`, `side`, `quantity`, `duration`, `timestamp`; `duration` min 300 s / max 86400 s; notional (quantity × mark price) must be ≥ 1,000 USDT and ≤ 1,000,000 USDT; max 30 open algo orders. ([Binance developer docs](https://developers.binance.com/docs/algo/future-algo/Time-Weighted-Average-Price-New-Order)) Spot TWAP exists with its own limits — read the spot endpoint's own page rather than assuming the futures numbers carry over. |
| **Generic DMA / FIX** | Client-side slicing | What `scripts/slicer.py` targets. You own the schedule, the catch-up policy, and the reporting. |

Treat every number above as **verify-before-use**. Venue algo parameters, notional bounds,
and rate limits change without notice; see `broker-api-changelog-diffing-tool`.

## Benchmark and cost definitions

| Concept | Definition used here | Source |
|---|---|---|
| TWAP schedule | Equal weight per interval, independent of volume. | Definitional. |
| VWAP schedule | Interval weight ∝ that interval's share of window volume. | Definitional. |
| Achieved price | $\bar{P} = \sum_i q_i p_i / \sum_i q_i$ over fills. | Definitional. |
| Slippage (bps) | $\text{side} \times (\bar{P} - P_{\text{bench}}) / P_{\text{bench}} \times 10^4$, side $= +1$ buy, $-1$ sell. **Positive means cost.** | Standard side-adjusted TCA convention. Vendors differ on sign — this module states its convention in `ExecutionReport`'s docstring rather than assuming yours. |
| Opportunity cost (bps) | Same expression with the decision/final price, applied to the **unfilled** remainder. | Perold's $C_o$ term. |
| Implementation shortfall (bps) | $f \cdot \text{slippage} + (1-f) \cdot \text{opportunity cost}$, $f$ = filled fraction. | A. Perold, *The Implementation Shortfall: Paper Versus Reality*, **Journal of Portfolio Management** 14(3), Spring 1988, pp. 4–9. |

## Known limitations of this reference implementation

- **Scheduling and accounting only.** It places no orders, cancels nothing, and enforces
  no risk limit. Pre-trade controls must sit outside it.
- **Not thread-safe.** No locks; drive it from one event loop or guard it externally.
- **No halt / auction state machine.** See `execution-algo-behavior-under-halted-instrument`.
- **No market-impact model.** It will faithfully execute a schedule far too large for the
  book. Sizing the parent order against liquidity is `liquidity-adjusted-position-sizing`
  and `strategy-capacity-estimation-before-scaling-capital`.
- **VWAP is measured against a benchmark your own trading moves.** At a meaningful share
  of window volume, tracking VWAP well and executing well stop being the same thing.
- **Defaults are library defaults, not standards.** No regulator or standards body
  publishes a mandatory `jitter_pct`, interval count, or catch-up cap. Calibrate them per
  instrument and record the rationale.

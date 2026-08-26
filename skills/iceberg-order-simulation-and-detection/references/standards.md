# Standards & Sources for Iceberg Order Detection

## What is actually specified by venues

These are documented venue behaviours, not this skill's choices. They constrain what
any price-level detector can and cannot conclude.

| Venue | Mechanism | Documented behaviour | Source |
|---|---|---|---|
| CME Globex | Native ("exchange-held") iceberg, FIX tag `1138-DisplayQty` | Refreshing the displayed quantity keeps the **same OrderID** until the order is fully executed or cancelled. Trade summary messages carry the true trade volume, which may exceed the resting display quantity. CME states this combination makes native icebergs detectable unambiguously and accurately **from Market by Order (MBO) data**. | [CME Group — Market by Order (MBO) FAQ](https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html) |
| CME Globex | ISV-held (synthetic) iceberg | Each refresh is submitted as a **new order** and receives a **new OrderID**. | Same as above |
| Nasdaq | Reserve Order, Equity 4, Rule 4703(h) | When an execution reduces the displayed portion below a normal unit of trading, a **new displayed order is entered and receives a new timestamp**, while the non-displayed reserve portion is decremented and **retains its original timestamp**. Replenishment therefore fires *below a round lot*, not strictly at zero. | [SEC / Federal Register, Order Approving SR-NASDAQ-2020-089 (Rule 4703(h))](https://www.federalregister.gov/documents/2021/02/18/2021-03214/self-regulatory-organizations-the-nasdaq-stock-market-llc-order-approving-a-proposed-rule-change-to) |
| Nasdaq | Random Reserve | The participant may elect a randomized display size rather than a fixed one. **A constant refill peak is therefore not a reliable requirement for detection.** | Same as above |

## Published detection methodology

| Claim | Source |
|---|---|
| Native CME icebergs are detected "using discrepancies between the resting volume of an order and the actual trade size as indicated by trade summary messages, as well as by tracking order modifications that follow trade events." Synthetic icebergs are identified from limit orders arriving shortly after trades. Total hidden size is then predicted with a Kaplan–Meier estimator that accounts for orders cancelled after partial execution. **This method operates on order-level messages.** | Zotikov, D., *CME Iceberg Order Detection and Prediction*, Devexperts LLC, [arXiv:1909.09495](https://arxiv.org/abs/1909.09495) |
| An iceberg specifies a price, a total size, and a visible peak; when the visible peak is fully executed it is immediately replenished by a size equal to the peak. The replenishment rule is what makes icebergs detectable by observers of the book. | Frey & Sandås, *The Impact of Iceberg Orders in Limit Order Books* ([SSRN 1108485](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1108485)) |

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is a venue
or regulatory standard**, and the numeric defaults are starting points to be calibrated
per instrument, not thresholds published by anyone.

| Rule | Requirement | Why |
|---|---|---|
| Level keying | Prices MUST be canonicalized (integer ticks when `tick_size` is known) before use as a level key. | `0.1 + 0.2 != 0.3`; raw floats split one economic level into two half-populated trackers. |
| Same-side accumulation | $V_{\text{cum}}$ MUST accumulate only volume whose aggressor consumes the tracked resting side (SELL→BID, BUY→ASK). | Contra-side prints did not come out of the resting order being measured. |
| Side classification | The signal MUST be derived from the tracked **book side**, never from the aggressor side of an individual print. | One mis-signed or crossed print would otherwise invert the directional call. |
| Duplicate suppression | A `trade_id` already accumulated MUST be ignored. | Reconnects and snapshot+delta recovery redeliver prints straight into the hidden-size estimate. |
| Snapshot ordering | A depth snapshot older than the last processed for that level MUST be dropped. | A stale snapshot reads as depth increasing and books a phantom refill. |
| Level re-baselining | A level MUST be re-baselined when it flips sides, or sits empty past the reset dwell. | Venue refreshes are immediate; a level that stays empty then returns is a different order. |
| Zero baseline | A level with $Q_0 \le 0$ MUST NOT be screened. | $V_{\text{cum}}/0$ reports ordinary displayed volume as 100% hidden. |
| Memory | The tracker table MUST be bounded (LRU eviction). | An unbounded per-price dict on a long session is a memory leak. |
| Score semantics | `confidence_score` MUST be capped strictly below 1.0 and MUST NOT be presented as a probability. | On aggregated price-level depth the iceberg hypothesis is not confirmable — reporting certainty would be false. |
| Hidden estimate | $\hat{Q}_{\text{hidden}} = \max(0, V_{\text{cum}} - Q_0)$ MUST be reported as a **lower bound**, conditional on all refills coming from one resting order. | The conditional is unverifiable at this data granularity. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `min_volume_ratio` | 1.5 | Heuristic starting point. No venue or regulator publishes this value. |
| `min_refill_count` | 2 | Heuristic. Set >= 1; 0 would flag a single sweep. |
| `level_reset_dwell_nanos` | 1e9 (1 s) | Deliberately loose; tighten for a colocated feed. |
| `PEAK_CONSISTENCY_TOLERANCE` | 0.10 | Scoring input only. Never gates detection — Random Reserve randomizes peaks. |

## Scope boundary

Iceberg and Reserve orders are ordinary, explicitly supported order types on every
venue above. A positive screen from this skill is **not** evidence of spoofing,
layering, or any other abusive practice, and must not be used as a surveillance or
enforcement artifact.

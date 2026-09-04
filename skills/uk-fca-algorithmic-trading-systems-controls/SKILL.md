---
name: uk-fca-algorithmic-trading-systems-controls
description: >-
  Use when building the order-entry gate for algorithmic trading into a UK venue under
  FCA Handbook MAR 7A.3.2R and RTS 6 Article 15. RTS 6 prescribes no numeric limits; the
  firm sets them from its capital base and clearing arrangements.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: compliance, uk-fca, mar-7a, mifid2-rts6, pre-trade-controls, kill-switch, price-collars, fail-closed, risk-governance
  brokers_frameworks: "MiFID RTS 6 (Comm. Del. Reg. (EU) 2017/589, UK assimilated law); FCA Handbook MAR 7A.3; MiFID RTS 9 (Comm. Del. Reg. (EU) 2017/566); FCA Multi-firm review of algorithmic trading controls (Aug 2025); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

# UK FCA Algorithmic Trading Systems and Controls

A pre-trade gate that sits between a strategy and the venue gateway, plus the
Article 12 kill switch. It implements **RTS 6 Article 15 only**, and it hard-blocks:
anything it cannot evaluate — a NaN price, an absent reference price, an unusable
message ceiling — is a rejection, never a pass.

## When to Use

Use this when building or auditing the order-entry gate of an algorithmic trading
system that sends orders to a UK trading venue (LSE, Cboe Europe UK, Turquoise,
Aquis), where the firm is subject to **FCA Handbook MAR 7A.3.2R** and the
order-entry controls in **MiFID RTS 6 Article 15**.

RTS 6 Art. 15(1) requires four controls "for all financial instruments", each of
which must "automatically block or cancel":

| RTS 6 | Control | Engine |
|---|---|---|
| Art. 15(1)(a) | Price collars against set price parameters, order-by-order and over a period | `PRICE_COLLAR` |
| Art. 15(1)(b) | Maximum order values, preventing "an uncommonly large order value" | `MAX_ORDER_VALUE` |
| Art. 15(1)(c) | Maximum order volumes, preventing "an uncommonly large order size" | `MAX_ORDER_VOLUME` |
| Art. 15(1)(d) | Maximum messages limits | `CAPACITY_EXCEEDED` |
| Art. 15(3) | Repeated automated execution throttle, auto-disable until re-enabled | `record_execution()` |
| Art. 15(4)-(5) | Market and credit risk limits from the firm's capital base | `CREDIT_LIMIT_EXCEEDED` |
| Art. 12 | Kill functionality — cancel unexecuted orders immediately at any or all venues | `trigger_kill_switch()` |

**Regulatory status.** RTS 6 remained in force as UK assimilated law when the FCA
assessed firms against it in its August 2025 multi-firm review. The Smarter
Regulatory Framework programme is actively moving MiFID-era assimilated law into
the FCA Handbook (the MiFID Org Reg went that way under PS25/13 in October 2025),
so re-verify the article citations against the FCA Handbook before relying on them.

## When NOT to Use

- **As a source of numeric limits.** RTS 6 prescribes **none**. It requires the
  controls to exist, and Art. 15(4) requires the firm to set the values "based on
  its capital base, its clearing arrangements, its trading strategy, its risk
  tolerance", adjusted "to account for the changing impact of the orders on the
  relevant market due to different price and liquidity levels". Every default in
  `RTS6ControlConfig` — 2.5%, £500,000, 10,000 shares, 95% — is an engineering
  placeholder. Shipping them unchanged is a calibration failure.
- **Where a soft, overridable alert is wanted.** This engine only hard-blocks, by
  design. In the FCA's 22 May 2024 CGML final notice (£27,766,200, with a
  concurrent £33.88m PRA penalty), a US$444bn basket entered from a US$58m intent;
  controls blocked US$255bn, the remaining US$189bn reached an algorithm and
  US$1.4bn was sold across European exchanges. The pop-up warning could be
  dismissed without reading its content. Any Art. 15(6) exception to a block must
  live outside this gate, applied "on a temporary basis and in exceptional
  circumstances", verified by the risk management function and authorised by a
  named individual.
- **As the firm's whole RTS 6 programme.** Out of scope here: testing methodology
  and conformance testing (Arts. 5–8), the annual self-assessment and validation
  report (Art. 9), stress testing at 2× the previous six months' peak (Art. 10),
  material-change management (Art. 11), automated market-abuse surveillance
  (Art. 13), business continuity (Art. 14), real-time monitoring with alerts
  "generated within five seconds after the relevant event" (Art. 16), and
  post-trade controls and reconciliation (Art. 17).
- **Outside the UK.** For EU venues use `mifid-ii-algo-trading-compliance-eu`; for
  US market access use `sec-rule-15c3-5-risk-controls-us`. Do not port a UK control
  set to another jurisdiction on the assumption the obligations coincide.
- **As a DEA provider's control set without extension.** RTS 6 Arts. 20–21 require
  a DEA provider to apply separate and distinct controls per client, and to be
  "solely entitled to set or modify the parameters". This engine is single-tenant.

## Prerequisites

- Python 3.10+ (standard library only).
- A **documented reference price** per instrument. There is no UK NBBO: the US
  consolidated best bid and offer is a Reg NMS construct, and the UK equity
  consolidated tape is still in procurement. RTS 6 Art. 15(1)(a) says only "set
  price parameters" — the firm chooses and documents its own reference (primary
  venue BBO mid, last trade, or an internal mark) and must feed it in as
  `OrderIntent.reference_price`.
- A **venue mass-cancel path** (FIX `OrderMassCancelRequest` (MsgType `q`), a venue
  cancel-on-disconnect facility, or the venue's drop-copy/kill port), wrapped as
  the `mass_cancel_handler` callable. Without it the engine blocks new orders but
  cancels nothing, and Art. 12 is not discharged.
- **Credit utilisation from the risk or clearing system**, not from the strategy.
- The venue's published **RTS 9 maximum ratio** of unexecuted orders to
  transactions, if you intend to self-monitor against it.
- **Single-threaded call discipline.** The engine is not thread-safe: the kill-switch
  map and the execution windows are mutated without a lock, and `record_execution` is
  check-then-act. Serialise calls, or wrap the engine in a lock. A race yields a
  duplicate trigger rather than a missed one, but the audit trail will show it.

## Workflow

1. **Calibrate and freeze the limits.** Build `RTS6ControlConfig` from the firm's
   capital base and clearing arrangements, per instrument liquidity tier. The
   config is frozen so a strategy cannot mutate it mid-session. Record the
   calibration basis — an auditor will ask why 2.5% and not 1%.
2. **Wire the kill switch to a real cancel path.** Construct
   `UKFCAAlgoControlsEngine(mass_cancel_handler=...)`. The handler receives the
   scope (`algo_id`, or `None` for firm-wide) and returns the number of orders
   cancelled. An engine built without one logs a warning at construction and every
   activation, and reports `mass_cancel_invoked=False`.
3. **Keep the counters current.** Art. 15(2) requires all orders sent to a venue to
   be included in the pre-trade limit calculation *immediately*. The engine holds no
   order-flow state: update `SystemCapacityState` before the next evaluation, or the
   message ceiling and the unexecuted-orders ratio are computed against stale numbers.
4. **Gate every order.** Call `evaluate_pre_trade_controls(order, capacity, config,
   credit)`. Forward only on `status == PASSED`. On any other status, do not send,
   and persist the `ControlCheckResult` — `violation_type`, `reason`, `order_id`,
   `algo_id` and the timezone-aware `timestamp` are the audit record.
5. **Classify the rejection before reacting.** `INVALID_ORDER`,
   `INVALID_REFERENCE_PRICE` and `INVALID_CAPACITY_STATE` mean the *inputs* are
   broken — fix the feed or the gateway; do not resubmit the same order.
   `THROTTLED` means back off and retry when utilisation falls. `PRICE_COLLAR`,
   `MAX_ORDER_VALUE`, `MAX_ORDER_VOLUME` and `CREDIT_LIMIT_EXCEEDED` mean the order
   itself is out of policy — resize or reprice, never widen the limit to fit it.
6. **Feed executions to the Art. 15(3) throttle.** Call `record_execution(algo_id,
   config)` on each execution. With `max_repeated_executions` set, exceeding the
   count inside `repeated_execution_window_seconds` latches the kill switch for that
   algorithm automatically. Leaving it `None` disables the throttle — a known
   Art. 15(3) gap the firm must close.
7. **Halt deliberately.** `trigger_kill_switch(algo_id, reason)` latches the local
   block *before* calling the handler, so a handler that raises leaves the firm
   halted with the failure in `mass_cancel_error` rather than trading on. Pass
   `algo_id=None` for firm-wide; a blank string raises `ValueError` rather than
   silently going firm-wide.
8. **Reset only with a named authoriser.** `reset_kill_switch(algo_id,
   authorised_by, reason)` requires both, appends to `kill_switch_events`, and
   clears that scope's execution counter. Resetting one algorithm does **not** lift
   a firm-wide halt. Persist `kill_switch_events` — the engine holds them in memory
   only.

## Common Pitfalls

- **Skipping the collar when the reference price is missing.** A feed gap that
  yields a reference of `0.0` or `NaN` must reject the order, not wave it through
  unchecked. This engine returns `INVALID_REFERENCE_PRICE`; a version that skipped
  the check let an order priced 100× away from the market pass every control.
- **Letting NaN through the thresholds.** `NaN > limit` is `False`, so an
  unvalidated NaN price or quantity satisfies *every* comparison and passes the
  whole gate. Validate fields before comparing them, not after.
- **Reporting a mass cancel that never happened.** Returning a canned
  "orders cancelled" count from the kill switch tells the incident review that
  Art. 12 was discharged when nothing was sent to the venue. Report what the venue
  actually confirmed, and treat a `None` count as "unknown, verify manually".
- **Unlatching on cancel failure.** If the venue gateway is down, the mass cancel is
  exactly what fails — and that is the moment the block must stay on. Latch first,
  cancel second, report the failure loudly.
- **Blank identifiers promoted to firm-wide scope.** `algo_id or "*"` turns an empty
  config field into a firm-wide halt, and worse, into a firm-wide *reset*. Require
  `None` explicitly for firm-wide and reject blanks.
- **Confusing the RTS 9 ratio with an RTS 6 control.** The order-to-trade limit is
  a **trading venue** obligation under RTS 9 (Reg. (EU) 2017/566): the venue
  calculates `total orders / total transactions − 1` per member at least daily and
  may set its own maximum. RTS 6 imposes no OTR control on the firm — what it
  imposes is the Art. 15(1)(d) *maximum messages limit*. Self-monitor the ratio
  with the venue's formula, not a different one, or your dashboard and the venue's
  fine notice will disagree.
- **Treating the message ceiling as pure infrastructure headroom.** "Don't melt our
  own gateway" (MAR 7A.3.2R(1) resilience and capacity, tested at 2× the previous
  six months' peak under Art. 10) and "don't flood the venue's order book"
  (Art. 15(1)(d)) are different limits. Configure `max_msg_rate_per_sec` as the
  lower of the two.
- **Letting the strategy carry its own credit limit.** A limit that travels on the
  order object is a limit the strategy can widen. RTS 6 Art. 1(c) requires trading
  desks to be separated from risk control, and Art. 15(4) makes the limit the firm's.
  Here it lives on the frozen `RTS6ControlConfig`.
- **Assuming a per-order check satisfies Art. 15(1)(a).** The collar must
  discriminate "both on an order-by-order basis **and** over a specified period of
  time". This engine does the first; the periodic dimension is a separate control.
- **Monitoring the unexecuted-orders ratio in aggregate only.** RTS 9 Art. 3(2)
  deems the limit exceeded when a member's activity in **one financial instrument**
  exceeds it. A firm-wide ratio can read compliant while a single symbol breaches.
  This engine holds one aggregate counter set; use
  `order-to-trade-ratio-fee-penalty-avoidance` for per-instrument tracking and the
  RTS 9 Annex message-counting methodology.

## Verification

```bash
python -m unittest discover -s skills/uk-fca-algorithmic-trading-systems-controls/scripts
```

The suite covers the collar on both sides and exactly at the limit, the value and
volume caps at their boundaries, NaN/Inf/negative/zero order fields, NaN limits in the
config, unusable order-flow counters, a missing reference price, an unusable message
ceiling, the RTS 9 ratio formula and its zero-transaction fallback, firm-wide versus
per-algorithm kill scope, latch-survives-handler-failure, blank-identifier rejection,
authorised reset, and the Art. 15(3) windowed throttle including the late-fill case.

Beyond the unit tests, verify against `assets/checklist.md` that each configured
limit has a documented calibration basis, and that the mass-cancel handler has been
exercised against the venue's test environment — an untested cancel path is an
untested Art. 12 control.

## Related Skills

- `uk-senior-managers-regime-algo-accountability`
- `mifid-ii-algo-trading-compliance-eu`
- `sec-rule-15c3-5-risk-controls-us`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `execution-algorithm-kill-switch-integration`

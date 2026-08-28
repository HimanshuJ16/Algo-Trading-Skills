# Pre-Flight / Sign-off Checklist — participation-of-volume-pov-execution

Use this before working a parent order through the POV engine in a live environment.

## Is POV the right algorithm at all
- [ ] The order has **no hard completion deadline**. POV caps participation; it does not promise completion, and a thin session can leave it substantially unfilled.
- [ ] Alpha does not decay materially inside the expected execution horizon (else `implementation-shortfall-minimization`).
- [ ] The order is not destined for an opening or closing auction (else `close-auction-participation-strategy`).
- [ ] The parent quantity fits inside one session's expected participation; if not, a multi-session budget is set upstream (`multi-day-execution-schedules-for-very-large-orders`).

## Volume basis — the single most consequential setting
- [ ] `config.volume_basis` is set **deliberately**, not defaulted.
- [ ] If `AWAY`: the feed genuinely excludes this order's own executions, and that has been confirmed against the tape, not assumed.
- [ ] If `CONSOLIDATED`: fills are reported to the engine promptly, so own prints are netted in the right interval.
- [ ] A run has been sanity-checked end to end: cumulative away volume in the report matches independently measured tape volume minus own fills.

## Rates and bounds
- [ ] `target_rate` comes from a market-impact analysis of *this* instrument, not from a default.
- [ ] `max_rate` is a documented risk-policy figure with a named approver. **It is not a standard** — IBKR documents `pctVol` at 10–50%, FIX `ParticipationRate(849)` permits up to 99.99%.
- [ ] `min_slice_qty` is at or above the instrument's real minimum tradable quantity (`minimum-fill-size-and-lot-rounding-logic`).
- [ ] `max_slice_qty` is understood as a **scheduling** bound, not a risk control — pre-trade limits live outside this engine.
- [ ] A misconfiguration is confirmed to **raise**, not clamp: `target_rate` above `max_rate` must fail loudly.

## Fill accounting
- [ ] Every share returned by `process_volume_update` is resolved by `record_fill` or `record_unfilled` — no share is left implicitly working.
- [ ] Partial fills are reported as `record_fill(part)` **then** `record_unfilled(remainder)`.
- [ ] Broker rejections are **classified** before release; the cause is fixed, or the same slice is rejected again next interval.
- [ ] A **timed-out placement is reconciled against the broker before being released.** Releasing a live order re-sends its quantity and double-executes.
- [ ] Every child order goes through `order-placement-idempotency` and `multi-broker-rate-limit-handling`.
- [ ] `overfill_qty` is monitored and any non-zero value halts the parent for reconciliation.

## Monitoring and alerts
- [ ] `realized_participation_rate` is understood as a **fills-only** number; `working_qty` is not participation.
- [ ] Alert on `RATE_CAPPED` and on any non-zero `overfill_qty`.
- [ ] Alert on a sustained run of `VOLUME_PAUSED` with `cum_target_qty` flat — the order has stalled and will not complete on its own.
- [ ] Alert on `working_qty` persisting across many updates — child orders are not being resolved.
- [ ] A human owns the late-session decision to accept an unfilled residual or switch algorithms.

## Controls and compliance
- [ ] All outstanding child orders of the parent can be cancelled as a unit, immediately (EU: RTS 6 Art. 12 — `execution-algorithm-kill-switch-integration`).
- [ ] Pre-trade risk controls sit outside the engine and cannot be disabled by a scheduling bug (EU: RTS 6 Art. 15; US market access: Rule 15c3-5, which binds the broker-dealer).
- [ ] The working parent order is under real-time monitoring by the trader **and** an independent risk function (EU: RTS 6 Art. 16).
- [ ] If this is a **share buy-back**: the binding cap is 25% of *average daily volume over prior sessions* (US Rule 10b-18(b)(4); EU Reg. 2016/1052 Art. 3(3)) — computed **outside** this engine and passed in as `total_qty`. A live participation rate is not evidence of compliance with it.
- [ ] Reports are retained for the applicable record-keeping period, with the volume basis and rate parameters recorded alongside them.

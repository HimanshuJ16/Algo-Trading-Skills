# Deep Workflow Reference — mifid-ii-algo-trading-compliance-eu

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

All article references are to Commission Delegated Regulation (EU) 2017/589 ("RTS 6").
See `references/standards.md` for the full article map and source links.

## Full Procedure

### 1. Pre-trade risk controls (Art. 15(1))

Call `MiFID2ComplianceManager.validate_pretrade_order()` before routing any order to an
EU trading venue. Article 15(1) enumerates exactly four control categories, and the
implementation maps one method to each:

| Art. 15(1) | Control | Method |
|---|---|---|
| (a) | Price collars, differentiating between financial instruments | `check_price_collar` |
| (b) | Maximum order values | `check_order_value` |
| (c) | Maximum order volumes | `check_volume` |
| (d) | Maximum messages limits (submission, modification **or cancellation**) | `record_message` |

Calibration is the firm's responsibility, not the regulation's. **RTS 6 sets no numeric
threshold for any of these.** Article 15(4) requires the limits to be derived from the
firm's capital base, clearing arrangements, trading strategy, risk tolerance and
experience; Article 9 requires them to be re-evaluated in the annual self-assessment. The
constructor defaults (5% collar, 10 msgs/sec, 100k value, 10k volume) are placeholders
that exist so the module runs, and must be replaced before any live use.

Implementation points that follow directly from the text:

- **Per-instrument collars.** Article 15(1)(a) requires collars to differentiate between
  different financial instruments. A single global percentage does not satisfy this for a
  multi-instrument universe; use `price_collar_pct_by_symbol` to override per symbol.
- **Amend and cancel traffic counts.** Article 15(1)(d) covers messages for submission,
  modification *and* cancellation. Call `record_message("AMEND")` / `record_message("CANCEL")`
  on those paths — they never reach `validate_pretrade_order`, so a rate limiter wired
  only to new orders systematically under-counts.
- **Count orders as they are sent.** Article 15(2) requires all orders sent to a venue to
  be included in the limit calculation immediately. The implementation consumes the budget
  at validation time, which over-counts (a collar-rejected order still spends a slot).
  Over-counting throttles earlier than required and is the safe direction; under-counting
  is not.
- **Execution throttles and re-enabling.** Article 15(3) requires repeated automated
  execution throttles that disable the trading system after a pre-determined number of
  repeated executions, and keep it disabled *until re-enabled by a designated staff
  member*. `reset_kill_switch(operator_id, reason)` is that gate — it demands an operator
  identity and writes an audit record. Never wire an automatic re-enable timer to it.
- **Overrides.** Article 15(6) permits submitting an order the pre-trade controls blocked,
  but only on a temporary basis, in exceptional circumstances, with risk-management
  verification and authorisation by a designated individual. If the system has an override
  path, it needs that two-person approval and an audit record; if it has none, say so in
  the self-assessment.
- **Market and credit risk limits** (Art. 15(4)) and **permission/exposure blocks**
  (Art. 15(5)) are additional obligations this reference implementation does *not* cover —
  see `kill-switch-and-drawdown-circuit-breakers` and `leverage-limit-enforcement-across-instruments`.

### 2. Kill functionality (Art. 12)

Article 12(1) is specifically about **cancellation**: the firm must be able to cancel
immediately, as an emergency measure, any or all of its unexecuted orders on any or all
connected venues. The *halt* of new order flow comes from the business continuity
arrangements in Article 14(2)(f) and 14(3) (shutting down the algorithm without creating
disorderly trading conditions). A complete control does both.

`trigger_rts6_kill_switch(operator_id, reason)` implements that pairing:

- The halt flag is set **before** the cancellation callback runs, so a failing
  cancellation can never leave the gate open.
- A failing callback is logged at CRITICAL, written to the audit trail with
  `cancellation_confirmed=False`, and re-raised as `KillSwitchCancellationError`. Order
  flow is stopped but resting orders may still be live on the venue — an operator must
  confirm cancellation with the venue out of band. A kill switch that reports success
  when the mass-cancel failed is worse than none.
- `cancel_resting_orders_fn` must be idempotent; the kill switch may be triggered
  repeatedly during an incident.
- Article 12(3) requires per-order attribution to an algorithm, trader, desk or client,
  which is what `tag_order` produces.
- Article 14(2)(e) requires a documented **usage policy** for this functionality: who may
  trigger it, on what evidence, and how re-enabling is authorised.

### 3. Order attribution and tagging

`tag_order()` builds `MiFID2OrderTag` (`algo_id`, `client_id`, `trading_capacity`,
`short_selling_flag`, `timestamp_ns`). Where the obligations actually come from:

- **RTS 6 Art. 12(3)** — the firm must be able to identify the responsible algorithm and
  trader/desk/client for each order sent to a venue.
- **MiFID II Art. 48** — the venue must be able to identify algorithmically generated
  orders, the algorithms used and the persons initiating them, *by means of flagging from
  members or participants*. The tagging obligation reaches you through the venue rulebook.
- **RTS 24 (Reg. (EU) 2017/580)** — the order record fields venues maintain, which members
  must supply.

Field-level cautions:

- `trading_capacity` is validated against the RTS 22 Field 29 code set: `DEAL`, `MTCH`,
  `AOTC`. `"MATCH"` and `"AAGT"` are not valid values.
- `short_selling_flag` is a firm-internal boolean and is **not** the MiFIR short selling
  indicator, which is a coded value (`SESH`/`SSEX`/`SELL`/`UNDI`, RTS 22 Field 62) applying
  only to instruments in scope of Regulation (EU) No 236/2012. A boolean cannot express
  `UNDI`; map it explicitly at the reporting boundary.
- `timestamp_ns` comes from the host wall clock and is not by itself evidence of RTS 25
  (Reg. (EU) 2017/574) compliance, which for high-frequency algorithmic trading requires a
  maximum divergence of 100 microseconds from UTC and 1 microsecond granularity. That is a
  host/infrastructure concern — see `clock-synchronization-ptp-for-trading-hosts`.
- Wire format is venue-specific. Verify each venue's rulebook and FIX/native specification
  rather than assuming this shape is accepted.

### 4. Testing, self-assessment and retention (Art. 5–11, 28)

- **Art. 5–7**: establish documented development and testing methodologies; a person
  designated by senior management authorises each deployment or substantial update; test
  in an environment separated from production. Retain change records showing when a change
  was made, who made it, who approved it, and its nature (Art. 5(7)).
- **Art. 6**: conformance-test against the venue (or DEA provider) on first access, on
  material venue changes, and before each deployment or material update.
- **Art. 8**: set predefined limits before deployment on the number of instruments traded,
  the price/value/number of orders, strategy positions, and the number of venues.
- **Art. 10**: stress test as part of the annual self-assessment, using the highest message
  volume and the highest trade volume of the previous six months, **each multiplied by
  two**, without affecting production.
- **Art. 9 + Annex I**: the annual self-assessment produces a validation report drawn up by
  the risk management function, audited by internal audit where one exists, and approved by
  senior management, including an analysis of compliance with the Annex I criteria.
- **Art. 28**: a firm using a high-frequency algorithmic trading technique must record each
  submitted order immediately after submission in the Annex II format and retain those
  records for **five years**.

The audit trail here supports that evidence but does not constitute it. `audit_log` is a
bounded in-memory ring buffer for inspection and testing; durable retention must come from
the `audit_sink` callable writing to durable, tamper-evident storage. Monitor
`audit_sink_failures` — a non-zero value means decisions exist only in memory.

## Known Failure Modes

- **Signed quantities bypassing the caps.** Where "sell" is encoded as a negative
  quantity, `price * quantity` is negative and slides under any positive maximum-order-value
  cap, and a raw `quantity <= max_volume` comparison passes too. Both Art. 15(1)(b) and
  15(1)(c) controls are then silently inert for every sell order. Compare on absolute
  notional and require a positive finite size.
- **Partial kill switch execution.** Halting new order placement without cancelling
  resting orders leaves the Art. 12(1) obligation unmet — cancellation is the part the
  article actually names.
- **A kill switch that reports success after a failed mass-cancel.** The venue gateway is
  exactly what tends to be unavailable during the incident that triggered the kill.
- **No attributed re-enable path.** Systems that auto-recover from a halt, or that let any
  process clear the flag, breach Art. 15(3)'s "until re-enabled by a designated staff
  member".
- **Rate limiters wired only to new orders.** Amend and cancel storms are the common cause
  of venue message-rate breaches, and Art. 15(1)(d) explicitly covers them.
- **A single global price collar** applied across a mixed universe, where Art. 15(1)(a)
  requires differentiation between instruments.
- **Percentage collars on instruments that trade at or below zero** (power, gas, some
  commodity futures): relative deviation is undefined at a zero reference and inverts in
  sign below it. The implementation fails closed by default; such instruments need an
  absolute-tick collar.
- **Mislabelled audit records.** A defaulted symbol attributes every unlabelled pre-trade
  decision to one instrument, corrupting the Art. 9 evidence. `symbol` is required.
- **Treating an in-memory audit list as retention.** Unproduceable evidence of a control
  is, for Art. 9 and Art. 28 purposes, the same as no control.
- **Citing the wrong article.** Kill functionality is Art. 12, not Art. 18 (security and
  access); pre-trade controls are Art. 15, not Art. 13 (market-manipulation surveillance).
  A self-assessment that maps controls to the wrong articles invites exactly the review it
  is meant to satisfy.

## Production Implementation Reference

- Reference code: `scripts/pretrade_risk_checks.py` (`MiFID2ComplianceManager`,
  `RTS6PreTradeResult`, `KillSwitchResult`, `MiFID2OrderTag`,
  `KillSwitchCancellationError`).
- Automated unit tests: `scripts/test_pretrade_risk_checks.py`.
- Scope limit: this module implements the Art. 15(1) order-entry checks, Art. 12 kill
  functionality and the audit hand-off. It does **not** implement Art. 13 surveillance,
  Art. 15(4)–(6) credit/permission/override controls, Art. 16 real-time monitoring or
  Art. 17 post-trade reconciliation.
- Known partial coverage: Art. 15(1)(a) requires collars to apply *both* order-by-order
  *and over a specified period of time*. `check_price_collar` implements the
  order-by-order limb only — the time-window limb needs a reference-price history this
  module does not own. Implement it alongside whatever component holds that history, and
  record the gap in the Art. 9 self-assessment until it is closed.

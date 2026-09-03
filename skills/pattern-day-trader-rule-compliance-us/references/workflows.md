# Deep Workflow Reference — pattern-day-trader-rule-compliance-us

This file holds the full technical procedure referenced by `SKILL.md`. Load this
when actually implementing the skill, not just when deciding whether it applies.
Regulatory positions and their sources are in `references/standards.md`.

## Full Procedure

### 1. Pin the policy, with provenance

Construct a `DayTradePolicy` rather than hard-coding thresholds:

```python
policy = DayTradePolicy(
    name="acme-broker-house-2026",
    source="Acme margin agreement §7, confirmed by support ticket 4821",
    source_as_of="2026-08-01",
    equity_threshold=25_000.0,
    max_day_trades_in_window=4,
    window_business_days=5,
    confirmed_with_broker=True,   # None until someone actually asks
)
engine = PDTComplianceEngine(policy=policy, holidays=nyse_holidays_2026)
```

`confirmed_with_broker` is tri-state on purpose:

| Value | Meaning | Gate behaviour |
|---|---|---|
| `True` | Broker still applies a count-based policy | Gate enforces the counts |
| `False` | Broker has migrated to intraday margin | Gate allows, and says why |
| `None` | Nobody asked | Gate enforces **and** warns |

`LEGACY_FINRA_PDT_POLICY` is supplied as the default. It carries the deleted
rule's parameters and `confirmed_with_broker=None`, so any decision dated on or
after 2026-06-04 arrives with a currency warning attached.

### 2. Feed executions, not day-trade counts

`record_execution(symbol, side, quantity, timestamp)` takes fills. Timestamps
must be timezone-aware; the engine converts to `market_timezone` (default
`America/New_York`) to derive the trading date. Naive timestamps raise unless
the engine is constructed with `assume_naive_is_market_local=True`.

Matching is quantity-aware, two-pass, FIFO within each pass:

1. same-day opening lots on the opposite side, then
2. overnight opening lots on the opposite side.

Leftover quantity opens a new lot — a scale-in, a fresh position or the far side
of a reversal. This ordering reproduces the former rule's carve-out exactly:
overnight quantity is consumed only when no same-day opening quantity remains,
which is precisely the case where no new purchase preceded the sale.

Worked cases (all covered by tests):

| Sequence | Day trades | Why |
|---|---|---|
| Buy 100, sell 100 (same day) | 1 | Same-day round trip |
| Buy 100 Mon, sell 100 Tue | 0 | Overnight carve-out |
| Buy 100 Mon; Tue: sell 100, buy 100 | 0 | Sale preceded the new purchase |
| Buy 100 Mon; Tue: buy 100, sell 100 | 1 | New purchase preceded the sale |
| Buy 100, buy 100, sell 200 (same day) | 1 | One closing execution, one day trade, quantity 200 |
| Buy 100, sell 150 (same day) | 1 | 100 offsets; 50 opens a short lot |

Counting convention: one closing execution yields at most one day trade. Brokers
differ; reconciliation is what resolves the difference.

### 3. Count over a business-day window ending at an explicit as-of date

`get_rolling_day_trade_count(as_of_date)` builds the window as the as-of business
day plus the preceding `window_business_days - 1` business days, skipping
weekends and any supplied holidays. If the as-of date is not itself a business
day, the window ends on the preceding business day, so a Saturday check sees
Friday's window rather than a silently shifted one.

`as_of_date` is required. The 1.x default — anchoring on the last recorded trade
— kept an old history permanently inside the window; anchoring on wall-clock
"today" silently mis-evaluates a backtest.

Without a holiday calendar the window excludes weekends only, and every decision
carries a warning saying so. Supply the calendar; see
`global-exchange-holiday-calendar-handling`.

### 4. Gate before submission

```python
decision = engine.evaluate_day_trade_gate(current_equity=account.equity,
                                          as_of_date=session_date)
if decision.blocked:
    audit_log.write(decision.as_log_record())
    raise DayTradeBlocked(decision.reason)
```

`PDTGateDecision` carries the rolling count, the limit, the window length, the
equity, the threshold, the policy name and source, the designation flag, the
total trades and day-trade ratio in the window, and any warnings.
`as_log_record()` flattens it for structured logging. `would_breach_pdt()`
returns the `(blocked, reason)` tuple for callers that only need the verdict.

Order of evaluation:

1. Broker confirmed migrated → allow, naming intraday margin as the live regime.
2. Equity at or above the threshold → allow. If the account is designated, the
   reason states that the threshold must hold at all times, not just now.
3. De minimis exemption, only if explicitly enabled on the policy → allow.
4. Already designated and below the threshold → block.
5. This would be the Nth day trade in the window and equity is below the
   threshold → block.

The former 6 percent test is an **exemption**, so enabling it lets trades
through. It is off by default and should only be enabled where the broker has
confirmed it applies the exemption. When enabled, the projected share is
`(count + 1) / (total_trades + 1)`; the exact denominator convention ("total
trades") was never precisely specified in the rule, so treat this as an
approximation and reconcile.

### 5. Treat the designation as sticky

Reaching the limit inside the window sets `designated_pattern_day_trader`, and it
does not decay when the window empties — the former minimum equity had to be
maintained "at all times". Use `set_broker_designation(bool)` to adopt the
broker's flag as authoritative in both directions.

### 6. Reconcile every session

```python
account = broker.get_account()
broker_count = getattr(account, "daytrade_count", None)
if not engine.reconcile_broker_count(broker_count, session_date):
    alert("day-trade count unverified or diverged")
```

`None` means the broker publishes no counter — Alpaca removed
`daytrade_count`, `pattern_day_trader`, `last_daytrade_count`,
`daytrading_buying_power` and `last_daytrading_buying_power` by 2026-07-06. The
call returns `False` and logs, because an absent counter is not agreement.

### 7. Monitor intraday margin, the regime that now binds

```python
snapshots = [
    IntradayMarginSnapshot(ts, equity=eq, maintenance_margin_requirement=mmr)
    for ts, eq, mmr in intraday_account_states
]
deficit = intraday_margin_deficit(snapshots)
if deficit and not is_de_minimis_deficit(deficit, closing_equity):
    prompt_deadline, expiry = deficit_freeze_deadline(session_date, nyse_holidays)
```

IML is equity less the maintenance margin requirement; the deficit is the
magnitude of the worst negative IML following an IML-reducing transaction that
day. `prompt_deadline` is the 5th business day (the 90-calendar-day freeze
trigger, for a customer who makes a practice of failing); `expiry` is the 15th
business day, after which the deficit stops being outstanding — but the freeze
provision applies "without regard to its expiration".

This is *your* estimate. The member may apply sweep-balance, market-value, "as
of" and simultaneity policies under Rule 4210(d)(2)(B), and must assume the
worst-case ordering where sequence cannot be demonstrated.

## Known Failure Modes

- **Regime confusion.** Blocking a trade "because FINRA requires $25,000" after
  2026-06-04, or unblocking everything because "the PDT rule is gone" while the
  broker is still in its phase-in.
- **UTC trade dates.** A 19:00 and 20:30 ET round trip stored as UTC spans two
  calendar dates; the day trade disappears from the count.
- **Dropped scale-ins.** A same-side execution ignored while a position is open
  leaves the tracker short of the real position, and the next day's closing sale
  is booked as a phantom day trade.
- **Calendar-day windows**, or weekday windows across an exchange holiday.
- **Stale window anchors.** A count evaluated as of the last recorded trade
  rather than the session date vetoes forever.
- **Future-dated records.** Backfilled or clock-skewed records dated after the
  as-of date counted inside the window.
- **Designation decay.** Clearing the flag because the rolling window emptied,
  ignoring the "at all times" maintenance obligation.
- **Absent counter read as zero.** Treating a removed `daytrade_count` field as
  a reconciled count of zero.
- **Sanction conflation.** Applying a 90-day freeze to a sub-threshold account
  that merely holds less than the minimum, which was never the trigger.

## Production Implementation Reference

- Reference code: `scripts/pdt_tracker.py` — `PDTComplianceEngine`,
  `DayTradePolicy`, `PDTGateDecision`, `DayTradeRecord`, `TradeExecution`,
  `IntradayMarginSnapshot`, `intraday_margin_deficit`, `is_de_minimis_deficit`,
  `deficit_freeze_deadline`, and the `DayTradeTracker` 1.x shim.
- Automated unit tests: `scripts/test_pdt_tracker.py`.
- Breaking changes in 2.0.0: `get_rolling_day_trade_count()` and
  `would_breach_pdt()` require `as_of_date`; `record_execution()` requires
  timezone-aware timestamps unless `assume_naive_is_market_local=True`.

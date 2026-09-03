# TASE Integration Workflows

## Workflow 1: Resolving the session phase

The phase resolution is where the two calendar defects this skill exists to prevent are
caught. Note that the trading-week regime is selected by *date*, not assumed.

```mermaid
flowchart TD
    A[Caller supplies datetime] --> B{Timezone-aware?}
    B -- No --> C[Raise TASEValidationError<br/>naive time cannot map to Israel local]
    B -- Yes --> D[astimezone Asia/Jerusalem<br/>tz database applies IST/IDT]
    D --> E{Date in supplied<br/>holiday set?}
    E -- Yes --> F[CLOSED]
    E -- No --> G{weekday in<br/>schedule.trading_weekdays?}
    G -- No --> F
    G -- Yes --> H{Short day?<br/>e.g. Friday}
    H -- Yes --> I[Use short_closing_auction / short_close]
    H -- No --> J[Use closing_auction / close]
    I --> K[Compare local time to boundaries]
    J --> K
    K --> L[PRE_OPEN / OPENING_AUCTION /<br/>CONTINUOUS_TRADING / CLOSING_AUCTION / CLOSED]
```

Two failure modes are worth restating because they are silent:

- Resolving local time with a **fixed UTC+2 offset** is correct only in winter. From the
  Friday before the last Sunday in March to the last Sunday in October, Israel is UTC+3,
  and the closing auction reads as continuous trading.
- An **empty holiday set** means every non-weekend weekday is treated as a trading day.
  The engine cannot derive TASE's Hebrew-calendar holidays; supplying them is the
  caller's job, and forgetting is indistinguishable from a market that never closes.

## Workflow 2: Order submission and the fail-closed controls

```mermaid
flowchart TD
    A[Strategy signal] --> B{Session connected?}
    B -- No --> R1[TASEConnectionError]
    B -- Yes --> C{client_order_id<br/>already tracked?}
    C -- Yes --> R2[TASEValidationError<br/>retry would orphan the original]
    C -- No --> D{enforce_session_calendar<br/>and phase accepts entry?}
    D -- No --> R3[TASEMarketClosedError<br/>retry when session opens]
    D -- Yes --> E[Parameter checks:<br/>qty, price, stop_price, display_qty]
    E --> F{Security in master?}
    F -- No --> G{require_registered_security?}
    G -- Yes --> R4[TASEValidationError<br/>collar cannot be applied]
    G -- No --> H[Warn: denomination/tick/collar skipped]
    F -- Yes --> I{order denomination ==<br/>master denomination?}
    I -- No --> R5[TASEValidationError<br/>100x mispricing]
    I -- Yes --> J[Tick alignment check]
    J --> H
    H --> K[max_order_qty]
    K --> L[Estimate notional in ILS]
    L --> M{Price present?}
    M -- Yes --> N[Convert by denomination]
    M -- No --> O{reference_price_ils > 0?}
    O -- No --> R6[TASEValidationError<br/>refuse: zero would bypass the cap]
    O -- Yes --> N
    N --> P[max_order_value_ils]
    P --> Q[Price collar vs reference]
    Q --> S[Track order, send NewOrderSingle]
```

### Handling the three failure classes distinctly

They are not interchangeable, and collapsing them into one retry path is how a rejected
order becomes a duplicated one:

| Exception | Meaning | Correct response |
| :--- | :--- | :--- |
| `TASEMarketClosedError` | Venue is not accepting entry | Queue and retry at session open |
| `TASEValidationError` | Order is malformed or unverifiable | Fix the order or the master; retrying unchanged fails identically |
| `TASERiskLimitError` | A risk control fired | Escalate to a risk decision — never auto-retry |
| `TASEConnectionError` | Session is down | Reconnect, then **reconcile before resubmitting** |

## Workflow 3: Notional estimation by denomination

The step most often got wrong. Each row is a different formula, and picking the wrong one
is a 100x error in a number that then feeds both the value cap and the collar check.

| Denomination | Cash value per unit | Failure if mishandled |
| :--- | :--- | :--- |
| `AGOROT` | `price / 100` | Treating Agorot as ILS overstates notional 100x |
| `ILS` | `price` | Treating ILS as Agorot understates notional 100x |
| `PERCENTAGE` | `price / 100 x par_value_ils` | Treating percent as ILS misstates notional by `100 / par` |

A **market order carries no price**. The estimate falls back to
`reference_price_ils x quantity`; when no positive reference price is registered the
notional is genuinely unknown and the order is refused. Returning zero instead — the
obvious-looking shortcut — makes the notional cap unreachable for exactly the order type
with the least price certainty.

## Workflow 4: Daily reference data ingestion

1. **Before the session**, pull the security master from TASE Data Hub / MAYA.
2. **Extract per instrument**: TASE security number, ISIN, instrument class, price
   denomination, tick size, previous close as the reference price, and **par value for
   every percentage-quoted instrument**.
3. **Register**. `register_security()` rejects a percentage-quoted instrument with no par
   value and a non-positive tick size, so a malformed master fails at load rather than at
   order time.
4. **Refresh the holiday set** from TASE's published schedule and rebuild the
   `TASESessionSchedule`; the schedule is frozen, so this is a replacement, not a mutation.
5. **Re-check the trading-week regime** if you are replaying history — use
   `TASESessionSchedule.for_date(d)` rather than the live schedule.

## Workflow 5: Backtesting across the 2026 trading-week change

A backtest spanning 2025-2026 crosses a calendar regime boundary. Using one schedule for
the whole span is wrong on one side of 5 January 2026 no matter which one you pick.

```python
schedule = TASESessionSchedule.for_date(bar_date, holidays=tase_holidays)
engine.config.session_schedule = schedule
```

Symptoms of getting this wrong, both of which look like data problems rather than
calendar problems:

- **Sunday bars in 2026 history** that the strategy trades but the venue never priced.
- **Friday bars in 2025 history** treated as tradable, or 2026 Friday bars silently
  dropped, shrinking the sample and flattering turnover statistics.

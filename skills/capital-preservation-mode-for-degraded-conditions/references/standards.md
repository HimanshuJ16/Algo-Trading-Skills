# Standards — capital-preservation-mode-for-degraded-conditions

## Engineering standards for a capital-preservation gate

| Property | Standard | How this skill meets it |
|---|---|---|
| Independence | The gate has zero dependencies on the alpha model or strategy logic, and a strategy bug cannot disable it. | The engine imports nothing from the strategy and is called from the order-routing chokepoint. |
| Hard stop | Once a *limit* is breached, software must not auto-recover; a human must intervene. | `EngineState.HALTED` is latched and only `manual_reset` clears it. |
| Recoverable degradation | A failure of a risk *input* is not the same event as a breach of a risk *limit*, and must not consume a human intervention. | `EngineState.DEGRADED_WARNING` blocks orders and clears itself on the next valid `update_pnl`. |
| Fail closed | Every ambiguous condition — unreadable snapshot, non-finite P&L, stale feed, unconfigured reset secret — resolves toward *not trading*. | See `restore`, `update_pnl`, `_pnl_is_stale`, `ResetAuthorizer`. |
| Monotonic timing | Interval measurement must not depend on wall-clock time, which can step in either direction. | Rolling window and staleness use `time.monotonic()`; wall clock is used only for audit timestamps. |
| Durability | A restart during a halt must not re-enable trading. | `snapshot()` / `restore()`, with an unreadable snapshot restoring as HALTED. |
| Auditability | Who cleared the switch, when, what was cleared, and whether a new risk budget was granted, must all be recoverable after the fact. | `HaltRecord` entries in `audit_log`, persisted in the snapshot. |
| Authenticated override | The override secret must be supplied at runtime, compared in constant time, and absent by default. | `ResetAuthorizer` reads `CAPITAL_PRESERVATION_RESET_TOKEN` and uses `hmac.compare_digest`. |

## Limit provenance

**None of the default values in `PreservationLimits` is set by a regulator, an exchange or a broker.** They are placeholders, and copying them into production without calibration is a misuse of this skill.

| Parameter | Default | Basis | Calibrate against |
|---|---|---|---|
| `max_daily_drawdown_usd` | 50,000.0 | **Placeholder** | The desk's stated peak-to-trough risk tolerance for one session. |
| `max_orders_per_minute` | 100 | **Placeholder** | Measured peak legitimate submission rate; `references/workflows.md` suggests ~2x peak. Must also sit *below* the venue's own message limit — see `broker-side-order-throttle-detection`. |
| `max_consecutive_errors` | 5 | **Placeholder** | Your venue's normal reject rate. The counter has no time decay, so a low-message-rate desk can accumulate a halt from errors hours apart. |
| `max_daily_loss_usd` | `None` (off) | Policy | An absolute floor measured from flat. A drawdown limit alone cannot bound a loss that follows a large intraday peak. |
| `max_pnl_staleness_seconds` | `None` (off) | Policy | Your mark-to-market publication interval plus tolerated jitter. Leaving it off means a dead feed silently disarms the drawdown control. |

## Drawdown definition

Peak-to-trough drawdown at time *t* is `max(P&L(s) for s <= t) - P&L(t)`, with the high-water mark seeded at `0.0` (flat at session start). Seeding at flat is what makes a straight-line loss from open register as a drawdown equal to the loss, so the control does not need a separate case for "never profitable".

It is **not** `abs(P&L(t)) when P&L(t) < 0`. That quantity is a loss-from-flat limit; it reports zero for any profitable session and therefore cannot detect a give-back. The two are different controls with different failure modes, which is why both are available here.

## Regulatory context

This is engineering guidance, not legal advice, and the two regimes below apply to *different populations of firms*. Neither universalises.

### EU / UK — MiFID II RTS 6

Jurisdiction: EU, and the UK as assimilated law. Applies to investment firms engaged in algorithmic trading. Source: [Commission Delegated Regulation (EU) 2017/589, EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589).

| Requirement | Source | Bearing on this skill |
|---|---|---|
| An investment firm "shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected ('kill functionality')." The firm must also be able to identify which algorithm, trader, desk or client each order came from. | RTS 6, Article 12 | **Mandatory, and this engine does not satisfy it.** Blocking new submissions is not cancelling unexecuted ones. The `on_halt` callback is the integration point for a real cancel-all; the identification requirement is out of this skill's scope entirely. |
| Pre-trade controls on order entry: price collars, maximum order values, maximum order volumes, and maximum messages limits. | RTS 6, Article 15(1)(a)–(d) | This engine implements a message-rate style control only, and at the *aggregate* level. The per-order controls (a)–(c) require order contents the engine never sees. |
| Repeated automated execution throttle controlling the number of times an algorithmic trading strategy has been applied. | RTS 6, Article 15 | The rolling order-rate window is the closest analogue here, but it counts submissions rather than strategy applications. |
| Real-time monitoring of algorithmic trading activity for signs of disorderly trading, with real-time alerts generated within five seconds of the relevant event. | RTS 6, Article 16, and Article 16(5) for the five-second bound | Bounds how long a halt may sit unreported. An `on_halt` callback that batches or polls on a longer interval cannot meet it. |

### US — SEC Rule 15c3-5 (Market Access Rule)

Jurisdiction: US. Applies to **broker-dealers with market access**, or that provide customers access to an exchange or ATS. It does **not** impose obligations on a self-directed trader running their own algorithm through a retail broker, though the control design remains good practice. Sources: [SEC final rule release 34-63241](https://www.sec.gov/files/rules/final/2010/34-63241.pdf), [SEC Division of Trading and Markets FAQs on Rule 15c3-5](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0).

| Requirement | Source | Bearing on this skill |
|---|---|---|
| Financial risk management controls reasonably designed to prevent the entry of orders exceeding appropriate pre-set credit or capital thresholds, and to prevent the entry of erroneous orders. | Rule 15c3-5(c)(1)(i) | The drawdown and session-loss limits are capital-threshold style controls. Erroneous-order prevention is per-order and out of scope here. |
| The risk management controls and supervisory procedures must be under the direct and exclusive control of the broker-dealer providing market access, subject to limited exceptions. | Rule 15c3-5(d)(1) | Reinforces the architectural requirement: the gate must not be delegated to, or bypassable by, the strategy or a client system. |

## Category

Risk Management / Emergency Controls. See also `kill-switch-and-drawdown-circuit-breakers` for the position- and exposure-limit breakers this skill deliberately does not duplicate, and `risk-control-bypass-audit-logging` for the override audit trail.

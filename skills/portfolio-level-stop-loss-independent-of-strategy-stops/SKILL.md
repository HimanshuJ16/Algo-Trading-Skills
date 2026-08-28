---
name: portfolio-level-stop-loss-independent-of-strategy-stops
description: >-
  Independent portfolio-level stop-loss engine monitoring aggregated daily and peak-to-trough drawdowns, fail-closed on unevaluable inputs, latching a trading lockout that only a human can clear, and triggering emergency global position flattening independent of sub-strategy stops.
domain: Portfolio Multi Strategy
subdomain: Risk Governance & Global Circuit Breakers
tags: ["portfolio-stop-loss", "drawdown-kill-switch", "risk-management", "circuit-breaker", "multi-strategy-risk", "nav-monitoring"]
brokers_frameworks: ["Global Portfolio Risk Framework", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying multi-strategy or multi-asset trading systems where sub-strategies maintain their own internal stop-loss rules. During systemic market shocks, correlation spikes, or liquidity crunches, individual strategy stops may fail to trigger simultaneously or suffer severe slippage. An independent portfolio-level stop-loss continuously calculates Net Asset Value (NAV) across all strategies and enforces daily ($\text{DD}_{\text{daily}} \ge \text{MaxDailyDD}$) and peak-to-trough ($\text{DD}_{\text{peak}} \ge \text{MaxPeakDD}$) limits, flattening all active positions and locking out new orders when limits are breached.

## When NOT to Use

- **As a source of regulatory thresholds.** The 5% / 10% defaults in `scripts/` are *your* risk policy. Nothing surveyed in `references/standards.md` imposes a drawdown or daily-loss number on a trading firm: MiFID II RTS 6 Art. 15(4) requires a firm to *set* market and credit risk limits based on its own capital base and risk tolerance, and SEC Rule 15c3-5 binds broker-dealers with market access, not the end trading firm. Never present these numbers to an auditor as regulatory minimums — calibrate them with `risk-limit-calibration-against-historical-drawdowns`.
- **As the order gate itself.** This engine decides *whether* the portfolio must stop; it does not inspect individual orders. Enforcing reduce-only flow while locked — so the lockout does not veto its own liquidation — belongs to `kill-switch-and-drawdown-circuit-breakers`.
- **As a replacement for per-strategy stops.** A portfolio stop is a last-resort backstop that halts the whole book. If it fires routinely, the strategy-level controls in `strategy-level-kill-switch-vs-portfolio-level-kill-switch` are too loose.
- **Inside a backtest.** A latching portfolio stop applied to a simulated equity curve truncates the drawdown tail and flatters the result. Model it as an explicit strategy rule instead — see `lookahead-bias-elimination`.
- **On a multi-currency book that has not been normalized.** The engine sums cash, prices and equity baselines arithmetically and performs no FX conversion; convert to one reporting currency first via `multi-currency-pnl-and-fx-conversion`.

## Prerequisites

- Sub-strategy position states (`strategy_id`, `symbol`, `quantity`, `current_price`, `unrealized_pnl`), sourced from the broker/custodian's account state rather than the bot's internal bookkeeping — RTS 6 Art. 17(3) frames that reconciliation duty for EU investment firms, and a fill-tracking bug otherwise hides a real breach from the one control meant to catch it.
- Portfolio equity metrics (`start_of_day_equity`, `peak_equity`, `current_cash`), all positive and all in one reporting currency.
- A record of every **settled** deposit and withdrawal (`capital_flow_since_sod`, `capital_flow_since_peak`), so a cash movement is not read as P&L.
- The correct NAV valuation mode for the account type: `CASH_PLUS_MARKET_VALUE` for cash-funded equity/spot books, `CASH_PLUS_UNREALIZED_PNL` for margined derivatives books.
- Risk policy config (`max_daily_drawdown_pct`: default 0.05, `max_peak_drawdown_pct`: default 0.10), expressed as **fractions**, not percentage points.

## Workflow

1. **Aggregated Portfolio NAV Calculation**:
   - Choose the valuation mode from the account type, then compute total NAV: $\text{NAV} = \text{CurrentCash} + \sum (\text{Qty}_i \cdot \text{Price}_i)$ for a cash-funded book, or $\text{NAV} = \text{CurrentCash} + \sum \text{UnrealizedPnL}_i$ for a margined one. On a futures or CFD account `Qty · Price` is *notional*, not equity: adding it to cash reports a leveraged, losing book as hugely profitable and the stop never fires.
2. **Fail Closed Before Measuring Anything**:
   - Treat any input you cannot evaluate as a halt condition, not as a passed check. A `NaN` mark makes every threshold comparison false, so both limits go quiet at once with no outward signal; a zero or negative `start_of_day_equity` makes the daily drawdown denominator undefined. Halt (`HALTED_INVALID_INPUT`), and where marks carry timestamps, halt on staleness too (`HALTED_STALE_PRICES`).
   - A fail-closed halt blocks *new risk* but must **not** auto-liquidate: the engine has no evidence the portfolio is actually down, and market-flattening a book on one bad tick is itself the loss event. Escalate to a human instead — and when usable data returns and shows a genuine breach, the flatten must still be requested then, rather than being swallowed by the lock the halt already set.
3. **Drawdown Evaluation, Net of Capital Flows**:
   - Remove settled flows from NAV before comparing against the baselines — a withdrawal lowers NAV without being a loss, a deposit raises it without being a gain:
     $$\text{DD}_{\text{daily}} = \frac{E_{\text{SOD}} - (\text{NAV} - F_{\text{SOD}})}{E_{\text{SOD}}}, \qquad \text{DD}_{\text{peak}} = \frac{E_{\text{peak}} - (\text{NAV} - F_{\text{peak}})}{E_{\text{peak}}}$$
   - Clamp both at zero (a portfolio above its baseline is not in drawdown), and decide the breach on the unrounded value so a reported `0.0500` cannot disagree with its own breach flag.
4. **Emergency Circuit Breaker Trigger**:
   - If $\text{DD}_{\text{daily}} \ge \text{MaxDailyDD}$ OR $\text{DD}_{\text{peak}} \ge \text{MaxPeakDD}$, trigger the emergency global flatten, override all sub-strategies, and lock the trading session.
   - Emit the flatten request **once**, on the transition into the breach — not on every subsequent poll, or a 1-second risk loop fires a fresh liquidation cascade every second while the first one is still working. Guard the transition with a lock if a strategy loop, a risk poller and an operator endpoint can all reach it.
5. **Latch the Lockout Across Evaluations**:
   - The lock must survive NAV recovery, a now-flat book, and the next day's start-of-day reset. Recomputing it from the current state each call silently re-enables trading at the next daily rollover, when $\text{DD}_{\text{daily}}$ mechanically returns to zero — exactly the auto-resume this control exists to prevent.
6. **Require an Audited Human Re-Enable**:
   - Refuse a blank operator identity and a blank reason, refuse an operator outside the configured roster, return a boolean the caller must check, and append every attempt — granted *and* refused — to the audit trail.
   - Re-enabling clears the *latch*, not the breach. After a peak-drawdown halt the breached high-water mark survives, so the next evaluation re-latches immediately; resuming requires the operator to deliberately re-baseline `peak_equity` in their own state store. Never re-baseline it automatically — that erases the peak limit entirely.
7. **Audit Report Generation**: Output structured `PortfolioStopReport`, including the NAV the drawdown was actually measured on and whether the lock came from this evaluation or a prior latch.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying Solely on Strategy-Level Stops**: Assuming strategy-level stops provide adequate portfolio-level protection during correlation breakdown.
- **Manual Intervention Delays**: Leaving portfolio drawdown response to manual human decision-making during fast market crashes.
- **Failing to Lock Trading Session**: Flattening positions upon stop-loss breach but allowing sub-strategies to immediately re-open new positions on subsequent signal cycles — or recomputing the lock from live NAV, so the lockout evaporates the moment prices bounce or the next trading day resets the daily baseline.
- **Passing a Limit as Percentage Points**: `max_daily_drawdown_pct=5` meaning "5%" reads as 500%. The breaker can never fire, and the first evidence is the loss it was supposed to prevent. Validate at construction.
- **Trusting an Unchecked `NaN`**: A stale mark or a zero-denominator P&L makes `dd >= limit` false for *both* limits simultaneously, so the engine reports `PORTFOLIO_NAV_HEALTHY` while checking nothing.
- **Auto-Liquidating on Bad Data**: Force-flattening because NAV could not be computed converts a data outage into a realized loss at market. Halt new risk, alert a human, do not sell.
- **Valuing a Margined Book as Cash + Notional**: A $500,000 notional futures position on $1,000,000 cash reports NAV of $1,500,000 rather than the true $940,000 account equity, and a 6% drawdown reads as a 50% gain.
- **Reading a Settled Withdrawal as Drawdown**: A scheduled cash transfer trips the stop and market-flattens a book that was never in trouble; symmetrically, a deposit masks a real loss.
- **Re-Firing the Flatten Every Poll**: A latch-free breach path re-issues the full liquidation on every evaluation cycle, duplicating orders into a falling market.
- **Auto-Baselining the Peak on Re-Enable**: Resetting `peak_equity` to current NAV to get the engine to unlock deletes the peak-drawdown limit while appearing to satisfy it.
- **Comparing Un-normalized Currencies**: Summing USD cash with JPY position values produces a NAV that is not a number in any currency, and a drawdown limit measured against it is meaningless.

## Verification

- Instantiate `PortfolioLevelStopLossIndependentOfStrategyStops`. Input portfolio with $\$1,000,000$ start-of-day equity. Submit position drawdowns resulting in NAV of $\$940,000$ ($6\%$ daily drawdown vs $5\%$ max limit) $\implies$ verify `DAILY_DRAWDOWN_BREACH_FLATTEN` status and `is_trading_locked = True`.
- Feed a `NaN` mark and a zero `start_of_day_equity` and confirm each returns `HALTED_INVALID_INPUT` with `is_trading_locked = True` and `positions_to_flatten_count = 0` — not `PORTFOLIO_NAV_HEALTHY`.
- Construct the config with `max_daily_drawdown_pct=5` and confirm it raises `ValueError` rather than accepting a 500% limit.
- Evaluate a breach, then evaluate a fully recovered portfolio, and confirm the report still reports the latched breach status with `is_latched = True`.
- Confirm the flatten request is emitted on the first breaching evaluation only, and that eight concurrent evaluations of the same breaching state produce exactly one liquidation request.
- Confirm `human_re_enable()` refuses a blank identity, a blank reason and an unlisted operator, that every attempt lands in `re_enable_log`, and that re-enabling while the portfolio still breaches re-latches on the very next evaluation.
- Confirm a $\$100,000$ settled withdrawal from a flat $\$1,000,000$ book reports `PORTFOLIO_NAV_HEALTHY`, and that a $\$200,000$ deposit does not mask a $\$60,000$ loss.
- Confirm the same losing futures book breaches under `CASH_PLUS_UNREALIZED_PNL` and does not under `CASH_PLUS_MARKET_VALUE`, and that the configured mode matches the account type.
- Run `python -m unittest discover -s skills/portfolio-level-stop-loss-independent-of-strategy-stops/scripts` and confirm all tests pass.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `risk-limit-calibration-against-historical-drawdowns`
- `multi-currency-pnl-and-fx-conversion`
- `margin-utilization-circuit-breaker`
- `risk-control-latency-budget`
- `lookahead-bias-elimination`
